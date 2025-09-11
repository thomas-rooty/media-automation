Write-Host "=== Vérification de Docker Desktop ==="

# Vérifie si Docker Desktop est lancé
$dockerRunning = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue

if (-not $dockerRunning) {
    Write-Host "Docker Desktop n'est pas lancé. Lancement..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    # Attente de Docker
    $dockerReady = $false
    do {
        Start-Sleep -Seconds 2
        try {
            docker info > $null 2>&1
            if ($LASTEXITCODE -eq 0) {
                $dockerReady = $true
            } else {
                Write-Host "Docker pas encore prêt..."
            }
        } catch {
            Write-Host "En attente de Docker..."
        }
    } while (-not $dockerReady)

    # Forcer le moteur Linux
    Write-Host "Forçage du moteur Docker Linux..."
    & 'C:\Program Files\Docker\Docker\DockerCli.exe' -SwitchLinuxEngine

    Write-Host "Docker est prêt."
} else {
    Write-Host "Docker Desktop est déjà en cours."
}

# Essai d'arrêt des conteneurs existants
Write-Host "Tentative d'arrêt des conteneurs Docker actifs..."
try {
    $containers = docker ps -q 2>$null
    if ($containers) {
        $containers | ForEach-Object { docker stop $_ }
    } else {
        Write-Host "Aucun conteneur actif à arrêter."
    }
} catch {
    Write-Host "Erreur lors de l'arrêt des conteneurs : $_"
}

# Lancement de la stack
cd "C:\Users\tccar\Projects\media-automation"
Write-Host "Lancement de la stack Docker..."

$composeOutput = docker compose up -d 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Erreur lors du lancement de la stack Docker :"
    Write-Host $composeOutput
    exit 1
}

Write-Host "Stack Docker démarrée avec succès."


# Lancement de Jellyfin (version native Windows)
$jellyfinPath = "C:\Program Files\Jellyfin\Server\jellyfin-windows-tray\Jellyfin.Windows.Tray.exe"
if (Test-Path $jellyfinPath) {
    Write-Host "Lancement de Jellyfin..."
    Start-Process $jellyfinPath
} else {
    Write-Host "Jellyfin non trouvé à l'emplacement spécifié."
}

# Ouverture de Jellyfin dans le navigateur
$jellyfinUrl = "http://localhost:8096"
Write-Host "Ouverture de Jellyfin dans le navigateur..."
Start-Process $jellyfinUrl

# Ouverture des autres interfaces web dans le navigateur
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
