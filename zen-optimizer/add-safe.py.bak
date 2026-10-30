import argparse
import hashlib
import http.cookiejar
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("zen-optimizer")
BATCH = ROOT / "batch-ready.json"
JOURNAL = ROOT / "added.json"
COOKIE_FILE = "/tmp/qbit-cookies.txt"

QBIT = "http://127.0.0.1:8080"
SAVE_PATH = "/data/Zen/torrents"
HOST_PATH = Path("/mnt/medias/Zen/torrents")
CATEGORY = "zen-optimizer"

MAX_BYTES = 300_000_000_000
FREE_RESERVE = 10_000_000_000
INTERVAL = 60
MAX_TORRENT_BYTES = 5_000_000

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
parser.add_argument("--limit", type=int, default=20)
args = parser.parse_args()

os.umask(0o077)

batch = json.loads(BATCH.read_text())
journal = (
    json.loads(JOURNAL.read_text())
    if JOURNAL.exists() else {}
)

cookies = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
cookies.load(ignore_discard=True, ignore_expires=True)

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cookies)
)

def request(url, data=None, headers=None):
    req = urllib.request.Request(
        url, data=data, headers=headers or {}
    )
    return opener.open(req, timeout=40)

def qbit_info():
    with request(QBIT + "/api/v2/torrents/info") as r:
        return json.load(r)

def torrent_infohash(raw):
    # Analyse les objets bencode sans réencoder le
    # dictionnaire "info", pour conserver son hash.
    def scan(i):
        if i >= len(raw):
            raise ValueError("Bencode tronqué")
        if raw[i:i+1] == b"i":
            end = raw.index(b"e", i+1)
            int(raw[i+1:end])
            return end + 1
        if raw[i:i+1] in (b"l", b"d"):
            kind = raw[i:i+1]
            i += 1
            while raw[i:i+1] != b"e":
                i = scan(i)
            return i + 1
        sep = raw.index(b":", i)
        length = int(raw[i:sep])
        end = sep + 1 + length
        if end > len(raw):
            raise ValueError("Chaîne tronquée")
        return end

    def get_string(i):
        sep = raw.index(b":", i)
        length = int(raw[i:sep])
        start = sep + 1
        return raw[start:start+length], start+length

    if not raw.startswith(b"d"):
        raise ValueError("Métadonnées invalides")

    i = 1
    info_hash = None

    while raw[i:i+1] != b"e":
        key, i = get_string(i)
        start = i
        i = scan(i)
        if key == b"info":
            info_hash = hashlib.sha1(raw[start:i]).hexdigest()

    if i != len(raw)-1 or not info_hash:
        raise ValueError("Dictionnaire info absent ou invalide")

    return info_hash

def get_metadata(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 9696
        or parsed.path != "/27/download"
    ):
        raise ValueError("URL Prowlarr inattendue")

    with request(url) as r:
        data = r.read(MAX_TORRENT_BYTES + 1)

    if len(data) > MAX_TORRENT_BYTES:
        raise ValueError("Métadonnées trop volumineuses")

    return data, torrent_infohash(data)

def add_torrent(raw):
    boundary = "ZenOptimizerBoundary"

    fields = {
        "category": CATEGORY,
        "savepath": SAVE_PATH,
        "paused": "false",
        "stopped": "false",
    }

    body = bytearray()

    for key, value in fields.items():
        body.extend(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"'
            f"\r\n\r\n{value}\r\n".encode()
        )

    body.extend(
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; '
        'name="torrents"; filename="release.torrent"\r\n'
        "Content-Type: application/x-bittorrent\r\n\r\n"
        .encode()
    )
    body.extend(raw)
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    with request(
        QBIT + "/api/v2/torrents/add",
        data=bytes(body),
        headers={
            "Content-Type":
                f"multipart/form-data; boundary={boundary}"
        },
    ) as r:
        reply = r.read().decode().strip()

    if reply == "Ok.":
        return
    try:
        result = json.loads(reply)
        if result.get("success_count", 0) >= 1:
            return
    except json.JSONDecodeError:
        pass
    raise RuntimeError(f"Ajout non confirmé : {reply[:150]}")

def save_journal():
    temporary = JOURNAL.with_suffix(".tmp")
    temporary.write_text(json.dumps(journal, indent=2))
    os.replace(temporary, JOURNAL)

existing = qbit_info()
hashes = {
    t["hash"].lower()
    for t in existing
}

names = {
    t["name"].casefold()
    for t in existing
}

reserved = sum(
    t.get("size", 0)
    for t in existing
    if t.get("category") == CATEGORY
)

print(f"Torrents existants : {len(existing)}")
print(f"Budget déjà réservé : {reserved / 1e9:.3f} Go")
print(f"Budget maximum : {MAX_BYTES / 1e9:.0f} Go")
print("Mode :", "AJOUT" if args.apply else "SIMULATION")

if not args.apply:
    print("Aucun téléchargement lancé.")
    print("Lancer avec --apply pour effectuer les ajouts.")
    raise SystemExit(0)

last_add = None
added = 0

for index, t in enumerate(batch, 1):
    if added >= args.limit:
        break

    title = t["title"]
    expected_size = round(t["size_gb"] * 1e9)

    if title.casefold() in names:
        print(f"[{index}] Déjà présent par nom : {title[:55]}")
        continue

    if expected_size <= 0:
        print(f"[{index}] Taille invalide")
        continue

    if reserved + expected_size > MAX_BYTES:
        print("STOP : budget de 300 Go atteint")
        break

    free_bytes = shutil.disk_usage(HOST_PATH).free
    if free_bytes < expected_size + FREE_RESERVE:
        print("STOP : espace disque libre insuffisant")
        break

    if last_add is not None:
        remaining = INTERVAL - (time.monotonic() - last_add)
        if remaining > 0:
            print(f"Attente {remaining:.0f} secondes...")
            time.sleep(remaining)

    try:
        raw, info_hash = get_metadata(t["downloadUrl"])

        if info_hash in hashes or info_hash in journal:
            print(f"[{index}] Doublon infohash : {title[:55]}")
            continue

        # Revérification juste avant l'ajout
        current = qbit_info()
        hashes = {x["hash"].lower() for x in current}
        reserved = sum(
            x.get("size", 0) for x in current
            if x.get("category") == CATEGORY
        )

        if info_hash in hashes:
            print(f"[{index}] Déjà présent dans qBittorrent")
            continue

        if reserved + expected_size > MAX_BYTES:
            print("STOP : budget atteint")
            break

        add_torrent(raw)
        last_add = time.monotonic()

        hashes.add(info_hash)
        reserved += expected_size
        journal[info_hash] = {
            "title": title,
            "size_bytes": expected_size,
        }
        save_journal()

        added += 1
        print(f"[{index}] AJOUTÉ : {title[:65]}", flush=True)

    except urllib.error.HTTPError as e:
        print(f"STOP : erreur HTTP {e.code}")
        if e.code == 429:
            print("Limitation serveur : respecter Retry-After.")
            print("Retry-After :", e.headers.get("Retry-After", "non fourni"))
        elif e.code == 503:
            print("Service indisponible. Reprendre ultérieurement.")
        break

    except Exception as e:
        print(f"STOP : {type(e).__name__}: {e}")
        print("État incertain : vérifier qBittorrent avant reprise.")
        break

print(f"\nAjouts confirmés pendant cette exécution : {added}")
print("Fin du traitement.")
