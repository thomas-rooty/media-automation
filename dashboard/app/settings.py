from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASH_", extra="ignore")

    # General
    title: str = "CapyFlix Monitoring"
    refresh_seconds: int = 45

    # Sonarr
    sonarr_url: str | None = None  # e.g. http://sonarr:8989
    sonarr_api_key: str | None = None

    # Radarr
    radarr_url: str | None = None  # e.g. http://radarr:7878
    radarr_api_key: str | None = None

    # qBittorrent
    qbittorrent_url: str | None = None  # e.g. http://gluetun:8080 (same network) or http://qbittorrent:8080
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None

    # Jellyfin
    jellyfin_url: str | None = None  # e.g. http://jellyfin:8096
    jellyfin_api_key: str | None = None
    jellyfin_user_id: str | None = None
    jellyfin_latest_limit: int = 12

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
    name: str
    ok: bool
    detail: str | None = None

