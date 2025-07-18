Write-Host "=== Vérification de Docker Desktop ==="

# Vérifie si Docker Desktop est lancé
$dockerRunning = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue

if (-not $dockerRunning) {
    Write-Host "Docker Desktop n'est pas lancé. Lancement..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    # Attendre que Docker soit prêt
    $dockerReady = $false
    do {
        Start-Sleep -Seconds 2
        try {
            docker info > $null 2>&1
            $dockerReady = $true
        } catch {
            Write-Host "En attente de Docker..."
        }
    } while (-not $dockerReady)

    Write-Host "Docker est prêt."
}
else {
    Write-Host "Docker Desktop est déjà en cours."
}

# Arrêt de tous les conteneurs en cours d'exécution
Write-Host "Arrêt de tous les conteneurs Docker actifs..."
docker ps -q | ForEach-Object { docker stop $_ }

# Passage au dossier du projet
cd "C:\Users\tccar\Projects\media-automation"

# Lancement de la stack Docker
Write-Host "Lancement de la stack Docker..."
docker-compose up -d

# Lancement de Plex Media Server si présent
$plexPath = "C:\Program Files\Plex\Plex Media Server\Plex Media Server.exe"
if (Test-Path $plexPath) {
    Write-Host "Lancement de Plex Media Server..."
    Start-Process $plexPath
} else {
    Write-Host "Plex Media Server non trouvé à l'emplacement par défaut."
}

# Ouverture des interfaces web dans le navigateur
$plexUrl = "http://127.0.0.1:32400"
Write-Host "Ouverture de Plex dans le navigateur..."
Start-Process $plexUrl

$overseerrUrl = "http://localhost:5055"
Write-Host "Ouverture d'Overseerr dans le navigateur..."
Start-Process $overseerrUrl

$radarrUrl = "http://localhost:7878"
Write-Host "Ouverture de Radarr dans le navigateur..."
Start-Process $radarrUrl

$sonarrUrl = "http://localhost:8989"
Write-Host "Ouverture de Sonarr dans le navigateur..."
Start-Process $sonarrUrl

$qbittorrentUrl = "http://localhost:8080"
Write-Host "Ouverture de qBittorrent dans le navigateur..."
Start-Process $qbittorrentUrl

Write-Host "`nStack média démarrée avec succès."
