import json
import time
import urllib.request
import urllib.parse
import http.cookiejar
from pathlib import Path

ROOT = Path("zen-optimizer")
BATCH = json.loads((ROOT / "batch-ready.json").read_text())

QBIT = "http://127.0.0.1:8080"
CATEGORY = "zen-optimizer"
SAVE_PATH = "/data/Zen/torrents"

cookies = http.cookiejar.MozillaCookieJar(
    "/tmp/qbit-cookies.txt"
)
cookies.load(ignore_discard=True, ignore_expires=True)

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cookies)
)

def qbit_get(endpoint):
    with opener.open(QBIT + endpoint, timeout=30) as response:
        return json.load(response)

def qbit_add(data):
    boundary = "ZenOptimizerBoundary"

    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="category"\r\n\r\n'
        f"{CATEGORY}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="savepath"\r\n\r\n'
        f"{SAVE_PATH}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="torrents"; filename="release.torrent"\r\n'
        "Content-Type: application/x-bittorrent\r\n\r\n"
    ).encode() + data + (
        f"\r\n--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        QBIT + "/api/v2/torrents/add",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        },
        method="POST",
    )

    with opener.open(req, timeout=30) as response:
        return response.read().decode().strip()

existing = qbit_get("/api/v2/torrents/info")
existing_names = {
    t["name"].casefold()
    for t in existing
}

print(f"Torrents déjà présents : {len(existing)}")

# Le premier torrent a déjà été ajouté.
for index, torrent in enumerate(BATCH[1:], start=2):
    try:
        with urllib.request.urlopen(
            torrent["downloadUrl"], timeout=30
        ) as response:
            data = response.read(5 * 1024 * 1024 + 1)

        if len(data) > 5 * 1024 * 1024 or not data.startswith(b"d"):
            print(f"[{index}/20] Métadonnées invalides")
            continue

        result = qbit_add(data)

        print(
            f"[{index}/20] {result} | "
            f"{torrent['title'][:65]}"
        )

        time.sleep(1)

    except Exception as error:
        print(f"[{index}/20] ERREUR : {error}")

print("Ajout du lot terminé.")
