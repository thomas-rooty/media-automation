# CapyFlix · plateforme média automatisée

CapyFlix regroupe une stack média Docker complète et un dashboard tactile conçu pour une tablette en mode paysage, avec une interface mobile adaptée au portrait.

## Services

| Domaine | Services |
|---|---|
| VPN et téléchargements | Gluetun (AirVPN / WireGuard), qBittorrent |
| Indexation | Prowlarr, FlareSolverr |
| Bibliothèque | Sonarr, Sonarr privé, Radarr, Bazarr |
| Média | Jellyfin, Jellyseerr |
| Exploitation | Portainer, Watchtower, Cloudflare Tunnel |
| Supervision | Dashboard CapyFlix (FastAPI + interface web) |

Le dashboard surveille les APIs applicatives et l’état réel des conteneurs via le socket Docker monté en lecture seule. Cloudflare Tunnel, qui ne fournit pas d’API HTTP exploitable ici, reste ainsi visible. Watchtower est volontairement exclu de la supervision.

## Démarrage

1. Créer la configuration locale :

   ```powershell
   Copy-Item .env.example .env
   ```

2. Renseigner dans `.env` les paramètres WireGuard AirVPN, les identifiants qBittorrent et les clés API.

3. Valider puis démarrer :

   ```powershell
   docker compose config --quiet
   docker compose up -d --build
   ```

   Sous Windows, `./start.ps1` effectue ces contrôles, démarre uniquement cette stack et ouvre le dashboard. Il n’arrête plus les autres conteneurs de la machine.

4. Ouvrir [http://localhost:3000](http://localhost:3000).

## URLs par défaut

| Interface | URL |
|---|---|
| Dashboard CapyFlix | `http://<serveur>:3000` |
| qBittorrent | `http://<serveur>:8080` |
| Jellyfin | `http://<serveur>:8096` |
| Jellyseerr | `http://<serveur>:5055` |
| Sonarr | `http://<serveur>:8989` |
| Sonarr privé | `http://<serveur>:8990` |
| Radarr | `http://<serveur>:7878` |
| Prowlarr | `http://<serveur>:9696` |
| Bazarr | `http://<serveur>:6767` |
| Portainer | `http://<serveur>:9000` ou `https://<serveur>:9443` |

## Ce que montre le dashboard

- état complet des 12 services supervisés, latence API et état Docker ;
- téléchargements actifs, progression, débit et ETA ;
- prochains épisodes et films surveillés/manquants ;
- derniers ajouts Jellyfin et demande de médias via Jellyseerr ;
- mémoire, disques, trafic en direct et historique qBittorrent ;
- activité d’import Sonarr/Radarr et heures des derniers scans ;
- météo locale optionnelle.

Chaque panneau gère sa panne indépendamment : une API indisponible n’efface plus les autres données et affiche une erreur actionnable.

## Exploitation

```powershell
# État synthétique
docker compose ps

# Logs du dashboard
docker compose logs -f dashboard

# Reconstruire uniquement le dashboard
docker compose up -d --build dashboard

# Arrêter uniquement cette stack
docker compose down
```

Le contrôle Gluetun utilise la route WireGuard actuelle `/v1/vpn/status`. Le serveur de contrôle reste accessible uniquement sur les réseaux Docker internes.

Pour lancer seulement le dashboard sur le port 8008, voir [`dashboard/README.md`](dashboard/README.md).
