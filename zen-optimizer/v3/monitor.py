import json
import os
import time
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path
from datetime import datetime

QBIT = os.getenv("QBIT_URL", "http://gluetun:8080")
USER = os.environ["QBIT_USER"]
PASSWORD = os.environ["QBIT_PASS"]
STATE = Path("/app/state/queue-state.json")
BUDGET = 300_000_000_000

def check():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )

    credentials = urllib.parse.urlencode({
        "username": USER,
        "password": PASSWORD,
    }).encode()

    request = urllib.request.Request(
        QBIT + "/api/v2/auth/login",
        data=credentials,
    )

    with opener.open(request, timeout=15) as response:
        status = response.status
        body = response.read().strip()

        if not (
            (status == 200 and body == b"Ok.")
            or status == 204
        ):
            raise RuntimeError(
                f"Authentification refusée : HTTP {status}"
            )

    # Vérifier la session avec une requête authentifiée.
    with opener.open(
        QBIT + "/api/v2/app/version",
        timeout=15,
    ) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Session qBittorrent inutilisable : "
                f"HTTP {response.status}"
            )

    with opener.open(
        QBIT + "/api/v2/torrents/info?category=zen-optimizer",
        timeout=15,
    ) as response:
        torrents = json.load(response)

    state = json.loads(STATE.read_text())

    total = len(torrents)
    completed = sum(
        t.get("progress", 0) >= 1 for t in torrents
    )
    reserved = sum(
        t.get("size", 0) for t in torrents
    )

    wrong_paths = [
        t["name"] for t in torrents
        if t.get("save_path") != "/data/Zen/torrents"
    ]

    print(
        f"\n=== ZEN OPTIMIZER V3 "
        f"{datetime.now().isoformat(timespec='seconds')} ===",
        flush=True,
    )
    print(f"Torrents : {total}", flush=True)
    print(f"Terminés : {completed}", flush=True)
    print(f"Incomplets : {total-completed}", flush=True)
    print(f"Stockage réservé : {reserved/1e9:.3f} Go", flush=True)
    print(f"Budget restant : {max(0,BUDGET-reserved)/1e9:.3f} Go", flush=True)
    print(
        f"Infohash suivis : {len(state.get('existing', {}))}",
        flush=True,
    )
    print(
        f"Destinations incorrectes : {len(wrong_paths)}",
        flush=True,
    )

    if reserved > BUDGET:
        print("ALERTE : BUDGET DEPASSE", flush=True)

    if wrong_paths:
        print("ALERTE : chemins inattendus", flush=True)

while True:
    try:
        check()
    except Exception as error:
        print(
            f"ERREUR SUPERVISION : {type(error).__name__}: {error}",
            flush=True,
        )
    time.sleep(300)
