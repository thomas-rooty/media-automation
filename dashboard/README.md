# Dashboard CapyFlix

Dashboard FastAPI sans framework frontend ni dépendance CDN. Les clés API restent côté serveur et l’interface fonctionne comme un centre de contrôle tactile.

## Intégration recommandée

Le dashboard est déjà inclus dans le `docker-compose.yml` à la racine et exposé sur le port 3000 :

```powershell
docker compose up -d --build dashboard
```

Il rejoint les trois réseaux de la stack, monte `/var/run/docker.sock` en lecture seule et surveille tous les conteneurs attendus.

## Lancement autonome

La stack principale doit avoir créé ses réseaux. Depuis la racine du dépôt :

```powershell
docker compose --env-file .env -f dashboard/docker-compose.dashboard.yml up -d --build
```

L’interface autonome est disponible sur `http://<serveur>:8008` par défaut.

## API

Les routes importantes sont :

- `GET /api/health` : santé du dashboard lui-même ;
- `GET /api/status` : snapshot consolidé API + Docker ;
- `GET /api/system` : mémoire, disques, réseau et I/O ;
- `GET /api/qbittorrent/torrents` : téléchargements ;
- `GET /api/sonarr/upcoming` et `/api/radarr/soon` : calendrier ;
- `GET /api/jellyfin/latest` : derniers médias ;
- `GET /api/jellyseerr/search` et `POST /api/jellyseerr/request` : demandes ;
- `GET /api/weather` et `/api/weather/forecast` : météo optionnelle.

`/api/status` met en cache les sondes pendant 10 secondes pour éviter de surcharger les services. Le paramètre `?refresh=true` force un nouveau diagnostic.

## Responsive

- tablette paysage : grille 12 colonnes, état global toujours visible et grandes cibles tactiles ;
- téléphone portrait : cartes en une colonne, contenus horizontaux glissants et dock d’actions fixe ;
- réduction des animations automatique avec `prefers-reduced-motion`.
