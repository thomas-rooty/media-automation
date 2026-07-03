import os
import time
import requests
import qbittorrentapi


QBIT_URL = os.getenv("QBIT_URL", "http://gluetun:8080")
QBIT_USER = os.getenv("QBIT_USER", "admin")
QBIT_PASS = os.getenv("QBIT_PASS")

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
PROGRESS_STEP = int(os.getenv("PROGRESS_STEP", "10"))


if not QBIT_PASS:
    raise RuntimeError("QBIT_PASS est manquant dans le .env")

if not DISCORD_WEBHOOK:
    raise RuntimeError("DISCORD_WEBHOOK est manquant dans le .env")


client = qbittorrentapi.Client(
    host=QBIT_URL,
    username=QBIT_USER,
    password=QBIT_PASS,
)


last_notified = {}


DOWNLOAD_STATES = {
    "downloading",
    "stalledDL",
    "metaDL",
    "forcedDL",
    "queuedDL",
    "checkingDL",
    "allocating",
}


def progress_bar(percent: int, length: int = 20) -> str:
    """
    Génère une barre de progression style Discord.

    Exemple :
    [████████░░░░░░░░░░░░] 40%
    """
    percent = max(0, min(100, percent))
    filled = int(length * percent / 100)
    empty = length - filled
    return "█" * filled + "░" * empty


def send_discord(message: str):
    payload = {
        "username": "qBittorrent",
        "content": message,
    }

    try:
        response = requests.post(
            DISCORD_WEBHOOK,
            json=payload,
            timeout=15,
        )

        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 5)
            print(f"Rate-limit Discord, retry dans {retry_after}s")
            time.sleep(float(retry_after))

            requests.post(
                DISCORD_WEBHOOK,
                json=payload,
                timeout=15,
            )

        elif response.status_code >= 400:
            print(f"Erreur Discord {response.status_code}: {response.text}")

    except Exception as e:
        print(f"Erreur envoi Discord: {e}")


def format_size(bytes_value) -> str:
    if bytes_value is None:
        return "?"

    try:
        size = float(bytes_value)
    except Exception:
        return "?"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]

    for unit in units:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} EB"


def format_speed(bytes_per_second) -> str:
    return f"{format_size(bytes_per_second)}/s"


def get_torrent_value(torrent, key, default=None):
    try:
        return getattr(torrent, key)
    except Exception:
        try:
            return torrent.get(key, default)
        except Exception:
            return default


def main():
    print("Connexion à qBittorrent...")

    try:
        client.auth_log_in()
    except Exception as e:
        raise RuntimeError(f"Connexion impossible à qBittorrent : {e}")

    print("Connecté à qBittorrent.")

    send_discord("🟢 Suivi de progression qBittorrent démarré.")

    while True:
        try:
            torrents = client.torrents_info()

            for torrent in torrents:
                torrent_hash = get_torrent_value(torrent, "hash")
                name = get_torrent_value(torrent, "name", "Torrent inconnu")
                state = str(get_torrent_value(torrent, "state", ""))

                progress = float(get_torrent_value(torrent, "progress", 0) or 0)
                percent = int(progress * 100)

                downloaded = get_torrent_value(torrent, "downloaded", 0)
                total = get_torrent_value(torrent, "size", 0)
                dlspeed = get_torrent_value(torrent, "dlspeed", 0)

                if not torrent_hash:
                    continue

                # Torrent terminé
                if percent >= 100:
                    if torrent_hash in last_notified:
                        bar = progress_bar(100)

                        message = (
                            f"✅ **{name}** terminé\n\n"
                            f"`[{bar}]` **100%**\n"
                            f"`{format_size(total)} / {format_size(total)}`"
                        )

                        send_discord(message)
                        last_notified.pop(torrent_hash, None)

                    continue

                # On ne notifie que les torrents en téléchargement
                if state not in DOWNLOAD_STATES:
                    continue

                current_step = percent - (percent % PROGRESS_STEP)
                previous_step = last_notified.get(torrent_hash, -1)

                # Évite d’envoyer 0%, 1%, etc.
                if current_step < PROGRESS_STEP:
                    continue

                # Évite les doublons
                if current_step <= previous_step:
                    continue

                last_notified[torrent_hash] = current_step

                bar = progress_bar(percent)

                message = (
                    f"📥 **{name}**\n\n"
                    f"`[{bar}]` **{percent}%**\n"
                    f"`{format_size(downloaded)} / {format_size(total)}`\n"
                    f"Vitesse : `{format_speed(dlspeed)}`"
                )

                send_discord(message)

        except qbittorrentapi.LoginFailed as e:
            print(f"Login qBittorrent échoué : {e}")

        except Exception as e:
            print(f"Erreur : {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
