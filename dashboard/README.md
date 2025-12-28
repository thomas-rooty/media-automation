# Salon Dashboard (LAN / tablette)

Mini dashboard **FastAPI + HTML/CSS/JS** destiné à un affichage plein écran (tablette Android / Chrome) sur le réseau local.

## Architecture (simple et robuste)

- **Backend**: `FastAPI` sert :
  - le frontend statique (`/` + `/static/*`)
  - des endpoints `/api/*` qui **proxient** Sonarr / qBittorrent / Jellyfin (les clés restent côté serveur)
- **Frontend**: `static/index.html` + `static/styles.css` + `static/app.js`
  - rafraîchissement auto (par défaut 45s)
  - thème sombre, grosses cartes, lisible à distance
  - boutons “accès rapide” vers les UIs complètes

## Variables d’environnement

Voir `env.example`. Les plus importantes :

- `DASH_SONARR_URL` / `DASH_SONARR_API_KEY`
- `DASH_RADARR_URL` / `DASH_RADARR_API_KEY`
- `DASH_QBITTORRENT_URL` / `DASH_QBITTORRENT_USERNAME` / `DASH_QBITTORRENT_PASSWORD`
- `DASH_JELLYFIN_URL` / `DASH_JELLYFIN_API_KEY`
- (optionnel) `DASH_LINKS_JSON` (URLs **LAN** accessibles depuis la tablette)

## Endpoints (exemples)

- `GET /api/sonarr/upcoming?days=7&limit=8`
- `GET /api/radarr/upcoming?days=21&limit=8`
- `GET /api/qbittorrent/torrents?filter=active&limit=6`
- `GET /api/jellyfin/latest?limit=9`
- `GET /api/jellyfin/items/{itemId}/image?maxHeight=240&quality=80`
- `GET /api/status`
- `GET /api/links`

## Lancer via Docker (minimal)

1. Assure-toi que le réseau Docker de ta stack existe (souvent `media-automation_media-net`).
2. Copie `dashboard/dashboard.env.example` -> `dashboard/dashboard.env` et remplis les valeurs (API keys, etc.).
3. Démarre :

```bash
docker compose -f dashboard/docker-compose.dashboard.yml up -d --build
```

Si ton réseau ne s’appelle pas `media-automation_media-net`, passe-le en variable :

```bash
MEDIA_NET_NAME=<ton_reseau> docker compose -f dashboard/docker-compose.dashboard.yml up -d --build
```

Puis ouvre: `http://<IP_DU_SERVEUR>:8008/`
