# 📦 Stack Torrent + VPN (Docker)

Ce dépôt permet de déployer une stack complète pour le téléchargement automatisé via BitTorrent, protégée par un VPN (PIA). Elle inclut les services suivants :

## 🧩 Services inclus

| Service      | Rôle                                                                 |
|--------------|----------------------------------------------------------------------|
| **Gluetun**  | Fournit un VPN (PIA) et une interface réseau protégée.               |
| **qBittorrent** | Client torrent avec interface Web.                                   |
| **Sonarr**   | Gère les séries (recherche, téléchargement, renommage).              |
| **Radarr**   | Gère les films (recherche, téléchargement, renommage).               |
| **Prowlarr** | Moteur d’indexeurs pour Sonarr/Radarr.                               |
| **Huntarr**  | Dashboard pour centraliser les activités de la stack.                |
| **Overseerr**| Interface de demande de médias pour les utilisateurs.                |
| **FlareSolverr** | Bypass Cloudflare pour certains indexeurs (via Prowlarr).             |

## 🚀 Lancement rapide

1. **Cloner ce dépôt** :
   ```bash
   git clone <url_du_dépôt>
   cd <nom_du_dossier>
   ```

2. **Créer les dossiers nécessaires** :
   ```bash
   mkdir -p config/{gluetun,qbittorrent,sonarr,radarr,prowlarr,huntarr,overseerr} \
            config/prowlarr/Definitions/Custom \
            downloads media/{Series,Movies}
   ```

3. **Donner les bons droits** (utilisateur ID 1000) :
   ```bash
   sudo chown -R 1000:1000 config downloads media
   ```

4. **Configurer Gluetun (VPN)** :  
   Dans le fichier `docker-compose.yml`, modifie ces lignes avec tes identifiants Private Internet Access, ou dans le .env du projet :
   ```yaml
   - OPENVPN_USER=votre_identifiant
   - OPENVPN_PASSWORD=votre_mot_de_passe
   - SERVER_REGIONS=DE Frankfurt  # ou un autre serveur PIA
   ```

5. **Démarrer la stack** :
   ```bash
   docker-compose up -d
   ```

## 🌐 Accès aux interfaces

| Service      | URL par défaut                   |
|--------------|----------------------------------|
| **qBittorrent** | http://localhost:8080 (user: admin / mdp: adminadmin) |
| **Sonarr**   | http://localhost:8989            |
| **Radarr**   | http://localhost:7878            |
| **Prowlarr** | http://localhost:9696            |
| **Huntarr**  | http://localhost:9705            |
| **Overseerr**| http://localhost:5055            |
| **FlareSolverr** | http://localhost:8191            |

## ⚙️ Configuration rapide

### 📥 qBittorrent
- Interface Web : `admin` / `adminadmin` (à changer dès le premier lancement).
- Dossier de téléchargement : `./downloads`

### 📺 Sonarr / 🎬 Radarr
- Configurer les chemins :
  - **Séries** : `/series`
  - **Films** : `/movies`
- Configurer le client torrent : ajouter `qBittorrent` sur `http://qbittorrent:8080`

### 🌍 Prowlarr
- Ajouter vos indexeurs préférés.
- Lier Prowlarr à Sonarr et Radarr dans l'onglet *Applications*.

### 🧠 Overseerr
- Permet aux utilisateurs de faire des demandes de films/séries.
- À connecter à Radarr/Sonarr via les paramètres Overseerr.

## 🔐 Sécurité

- Tout le trafic torrent passe par **Gluetun** (VPN PIA).
- Pas de fuite IP possible grâce à `network_mode: "service:gluetun"` sur qBittorrent.

## 🛑 Arrêter les services

```bash
docker-compose down
```

## ✅ Conseils

- Pense à sauvegarder les dossiers `config/` pour ne pas perdre ta configuration.
- Tu peux accéder aux journaux de chaque service avec :
  ```bash
  docker logs <nom_du_conteneur>
  ```
