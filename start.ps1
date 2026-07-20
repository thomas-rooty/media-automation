param(
    [switch]$OpenServices
)

$ErrorActionPreference = "Stop"
$projectPath = $PSScriptRoot
$envPath = Join-Path $projectPath ".env"

function Write-Step([string]$Message) {
    Write-Host "`n› $Message" -ForegroundColor Cyan
}

Write-Host "CapyFlix · démarrage de la plateforme" -ForegroundColor Green

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker n'est pas installé ou n'est pas disponible dans le PATH."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $dockerDesktop)) {
        throw "Le moteur Docker ne répond pas et Docker Desktop est introuvable."
    }

    Write-Step "Démarrage de Docker Desktop"
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 2
        docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw "Docker n'a pas répondu après 2 minutes."
    }
}

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $projectPath ".env.example") -Destination $envPath
    throw "Un fichier .env a été créé. Renseignez les identifiants AirVPN et les clés API, puis relancez ce script."
}

$requiredVariables = @(
    "WIREGUARD_PUBLIC_KEY",
    "WIREGUARD_PRIVATE_KEY",
    "WIREGUARD_ADDRESSES"
)
$envValues = @{}
Get-Content -LiteralPath $envPath -Encoding utf8 | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z0-9_]+)=(.*)$') {
        $envValues[$Matches[1]] = $Matches[2].Trim()
    }
}
$missing = $requiredVariables | Where-Object {
    -not $envValues.ContainsKey($_) -or [string]::IsNullOrWhiteSpace($envValues[$_]) -or $envValues[$_] -like "CHANGE_ME*"
}
if ($missing.Count -gt 0) {
    throw "Variables VPN manquantes dans .env : $($missing -join ', ')"
}

Push-Location $projectPath
try {
    Write-Step "Validation de la configuration"
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "La configuration Docker Compose est invalide." }

    Write-Step "Construction et démarrage des services"
    docker compose up -d --build --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Le démarrage Docker Compose a échoué." }

    Write-Step "État des services"
    docker compose ps
} finally {
    Pop-Location
}

$dashboardPort = if ($envValues["DASHBOARD_PORT"]) { $envValues["DASHBOARD_PORT"] } else { "3000" }
$dashboardUrl = "http://localhost:$dashboardPort"
Write-Host "`nDashboard disponible sur $dashboardUrl" -ForegroundColor Green
Start-Process $dashboardUrl

if ($OpenServices) {
    @("http://localhost:8096", "http://localhost:5055", "http://localhost:8989", "http://localhost:7878") |
        ForEach-Object { Start-Process $_ }
}
