from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASH_", extra="ignore")

    # General
    title: str = "CapyFlix"
    refresh_seconds: int = 30

    # Sonarr
    sonarr_url: str | None = None  # e.g. http://sonarr:8989
    sonarr_api_key: str | None = None

    # A second Sonarr instance is part of the main stack. Keeping it explicit
    # avoids the long-standing blind spot where the container could be down
    # without ever appearing in the dashboard.
    sonarr_private_url: str | None = None
    sonarr_private_api_key: str | None = None

    # Radarr
    radarr_url: str | None = None  # e.g. http://radarr:7878
    radarr_api_key: str | None = None

    # qBittorrent
    qbittorrent_url: str | None = None  # e.g. http://gluetun:8080 (same network) or http://qbittorrent:8080
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None
    # Optional HTTP Host override for qBittorrent host-header validation.
    # The TCP destination can remain the Gluetun Docker hostname.
    qbittorrent_host: str | None = None

    # Jellyfin
    jellyfin_url: str | None = None  # e.g. http://jellyfin:8096
    jellyfin_api_key: str | None = None
    jellyfin_user_id: str | None = None
    jellyfin_latest_limit: int = 12

    # Jellyseerr (requests from dashboard)
    jellyseerr_url: str | None = None  # e.g. http://jellyseerr:5055
    jellyseerr_api_key: str | None = None

    # Prowlarr
    prowlarr_url: str | None = None  # e.g. http://prowlarr:9696
    prowlarr_api_key: str | None = None

    # Bazarr
    bazarr_url: str | None = None  # e.g. http://bazarr:6767
    bazarr_api_key: str | None = None

    # FlareSolverr
    flaresolverr_url: str | None = None  # e.g. http://flaresolverr:8191

    # Autobrr
    autobrr_url: str | None = None  # e.g. http://autobrr:7474

    # Watcharr
    watcharr_url: str | None = None  # e.g. http://watcharr:3080

    # Portainer
    portainer_url: str | None = None  # e.g. http://portainer:9000

    # Glances
    glances_url: str | None = None  # e.g. http://glances:61208

    # Dozzle
    dozzle_url: str | None = None  # e.g. http://dozzle:8080

    # Nextcloud
    nextcloud_url: str | None = None  # e.g. http://nextcloud:80

    # Gluetun (VPN control server)
    gluetun_url: str | None = None  # e.g. http://gluetun:8000

    # Docker Engine socket. When mounted read-only, it lets the dashboard
    # report stopped/unhealthy containers (including services without an HTTP
    # API such as Cloudflared).
    docker_socket: str = "/var/run/docker.sock"

    # Links (quick navigation buttons)
    # JSON string: [{"label":"Jellyfin","url":"http://192.168.1.10:8096"}, ...]
    links_json: str = "[]"

    # System disks to show (inside container paths)
    # JSON string: [{"label":"Serveur","path":"/"},{"label":"SSD","path":"/mnt/ssd"}]
    disks_json: str = "[]"

    # Weather (Open-Meteo, no API key)
    # Recommended: set lat/lon + optional label (city name shown in UI).
    weather_lat: float | None = None
    weather_lon: float | None = None
    weather_label: str = ""
    weather_timezone: str = "auto"

    @field_validator("weather_lat", "weather_lon", mode="before")
    @classmethod
    def empty_number_is_none(cls, value: Any) -> Any:
        # Docker Compose intentionally passes an empty string when weather is
        # optional and not configured.
        return None if value == "" else value

    def links(self) -> list[dict[str, str]]:
        try:
            raw: Any = json.loads(self.links_json)
            if isinstance(raw, list):
                out: list[dict[str, str]] = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("label", "")).strip()
                    url = str(item.get("url", "")).strip()
                    if label and url:
                        out.append({"label": label, "url": url})
                return out
        except Exception:
            pass
        return []

    def disks(self) -> list[dict[str, str]]:
        try:
            raw: Any = json.loads(self.disks_json)
            if isinstance(raw, list):
                out: list[dict[str, str]] = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("label", "")).strip()
                    path = str(item.get("path", "")).strip()
                    if label and path:
                        out.append({"label": label, "path": path})
                return out
        except Exception:
            pass
        return []


@dataclass(frozen=True)
class ServiceStatus:
    slug: str
    name: str
    ok: bool
    detail: str | None = None
    category: str = "service"
    state: str = "unknown"
    response_ms: int | None = None
    container: str | None = None

