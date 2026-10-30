import os
import json
import time
import fcntl
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
SELECTION = ROOT / "selection.json"
STATE_FILE = ROOT / "refresh-state.json"
WORKER_STATE = ROOT / "worker-state.json"
LOCK_FILE = ROOT / "worker.lock"

INDEXER_ID = 27
API_URL = "http://127.0.0.1:9696/api/v1/search"

CATEGORIES = [
    103010, 103030, 103050,
    107010, 107020, 107030,
    102000, 102050, 102070,
    105000, 105070,
    101000, 104030, 108010,
]

MIN_SIZE = 100_000_000
MAX_SIZE = 400_000_000
MIN_SEEDS = 1
MAX_SEEDS = 10

CATEGORY_INTERVAL = 6 * 3600
REQUEST_ERROR_PAUSE = 3600
DEFAULT_429_PAUSE = 24 * 3600

os.umask(0o077)
CACHE.mkdir(exist_ok=True)

def load(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())

def save(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(
        data, indent=2, ensure_ascii=False
    ))
    os.replace(tmp, path)
    os.chmod(path, 0o600)

def get_api_key():
    key = os.environ.get("PROWLARR_API_KEY")
    if key:
        return key

    env = ROOT.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("PROWLARR_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    raise RuntimeError("PROWLARR_API_KEY introuvable")

def retry_after_seconds(header):
    if not header:
        return DEFAULT_429_PAUSE
    try:
        return max(0, int(header))
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
            return max(
                0,
                int((date - datetime.now(timezone.utc)).total_seconds())
            )
        except (ValueError, TypeError, OverflowError):
            return DEFAULT_429_PAUSE

def identity(t):
    return (
        str(t.get("title", "")),
        round(float(t.get("size_gb", 0)) * 1e9)
    )

def eligible(t):
    size = int(t.get("size") or 0)
    seeds = int(t.get("seeders") or 0)
    return (
        MIN_SIZE <= size <= MAX_SIZE
        and MIN_SEEDS <= seeds <= MAX_SEEDS
        and bool(t.get("title"))
        and bool(t.get("downloadUrl"))
    )

def convert(t):
    size = int(t["size"])
    gb = size / 1e9
    seeds = int(t["seeders"])
    note = 1.20
    coef = (gb + 0.25) / 4.25
    zen = coef * (note / 2) * 1.25 / 7

    return {
        "title": t["title"],
        "size_gb": gb,
        "seeders": seeds,
        "note": note,
        "zen_day": zen,
        "zen_per_gb": zen / gb,
    }

def refresh():
    state = load(
        STATE_FILE,
        {"last_search": {}, "next_request_at": 0}
    )
    worker = load(WORKER_STATE, {})

    state.setdefault("last_search", {})
    state.setdefault("offsets", {})
    state.setdefault("next_request_at", 0)

    now = time.time()

    # Respecter les suspensions des deux programmes.
    resume_at = max(
        float(state["next_request_at"]),
        float(worker.get("next_request_at", 0))
    )

    if now < resume_at:
        print(
            "SUSPENDU : reprise dans",
            int(resume_at - now),
            "secondes"
        )
        return

    categories = [
        cat for cat in CATEGORIES
        if now - state["last_search"].get(str(cat), 0)
        >= CATEGORY_INTERVAL
    ]

    if not categories:
        print("Aucune catégorie à rafraîchir actuellement.")
        return

    cat = min(
        categories,
        key=lambda c: state["last_search"].get(str(c), 0)
    )

    offset = int(state["offsets"].get(str(cat), 0))
    print(f"Pagination : catégorie {cat}, offset {offset}")

    params = urllib.parse.urlencode({
        "query": "",
        "type": "search",
        "indexerIds": INDEXER_ID,
        "categories": cat,
        "limit": 100,
        "offset": offset,
    })

    req = urllib.request.Request(
        API_URL + "?" + params,
        headers={"X-Api-Key": get_api_key()}
    )

    print("Recherche C411, catégorie :", cat, flush=True)

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            results = json.load(response)

    except urllib.error.HTTPError as e:
        if e.code == 429:
            pause = max(
                DEFAULT_429_PAUSE,
                retry_after_seconds(e.headers.get("Retry-After"))
            )

            until = time.time() + pause

            state["next_request_at"] = max(
                float(state["next_request_at"]), until
            )
            worker["next_request_at"] = max(
                float(worker.get("next_request_at", 0)), until
            )

            save(STATE_FILE, state)
            save(WORKER_STATE, worker)

            print(
                "HTTP 429 : suspension partagée de",
                int(pause / 60),
                "minutes"
            )
        else:
            state["next_request_at"] = (
                time.time() + REQUEST_ERROR_PAUSE
            )
            save(STATE_FILE, state)
            print(
                "HTTP", e.code,
                ": nouvelle recherche dans une heure"
            )
        return

    except Exception as e:
        state["next_request_at"] = (
            time.time() + REQUEST_ERROR_PAUSE
        )
        save(STATE_FILE, state)
        print("Erreur de recherche :", type(e).__name__)
        return

    if not isinstance(results, list):
        print("Réponse invalide : aucun fichier modifié.")
        return

    cache_file = CACHE / f"{cat}.json"
    old_cache = load(cache_file, [])

    merged = {}

    for t in old_cache + results:
        if t.get("title") and t.get("size"):
            key = (t["title"], int(t["size"]))
            merged[key] = t

    selection = load(SELECTION, [])
    known = {identity(t) for t in selection}
    additions = []

    for t in results:
        if not eligible(t):
            continue

        candidate = convert(t)
        key = identity(candidate)

        if key in known:
            continue

        known.add(key)
        additions.append(candidate)

    selection.extend(additions)

    # Écriture atomique pour éviter qu'un cycle du worker
    # lise un fichier partiellement écrit.
    save(cache_file, list(merged.values()))
    save(SELECTION, selection)

    # Avancer à la page suivante si la page est complète.
    # Sinon, revenir au début pour les prochaines recherches.
    state["offsets"][str(cat)] = (
        offset + 100 if len(results) == 100 else 0
    )
    state["last_search"][str(cat)] = time.time()
    state["next_request_at"] = 0
    save(STATE_FILE, state)

    print("Résultats :", len(results))
    print("Nouveaux candidats :", len(additions))
    print("Catalogue total :", len(selection))
    print("Historique du worker conservé.")

if __name__ == "__main__":
    # Utilise le même verrou que le worker.
    # Si le worker travaille, ce cycle est ignoré.
    with open(LOCK_FILE, "a+") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            print("Worker occupé : rafraîchissement reporté.")
            raise SystemExit(0)

        refresh()
