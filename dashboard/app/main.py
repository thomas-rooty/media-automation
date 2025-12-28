from __future__ import annotations

import datetime as dt
import shutil
import socket
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .settings import ServiceStatus, Settings


settings = Settings()

app = FastAPI(title=settings.title)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


_NET_LAST: dict[str, float] = {}


def _read_net_totals_linux() -> tuple[int | None, int | None]:
    """
    Read total rx/tx bytes from /proc/net/dev (Linux).
    Returns (rx_bytes, tx_bytes) or (None, None) if unavailable.
    """
    path = "/proc/net/dev"
    try:
        rx_total = 0
        tx_total = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                iface = iface.strip()
                if not iface or iface == "lo":
                    continue
                cols = rest.strip().split()
                # format: rx_bytes ... (8 cols) ... tx_bytes ...
                if len(cols) < 16:
                    continue
                rx_total += int(cols[0])
                tx_total += int(cols[8])
        return rx_total, tx_total
    except Exception:
        return None, None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


def _require(v: str | None, name: str) -> str:
    if not v:
        raise HTTPException(status_code=503, detail=f"{name} not configured on server")
    return v


def _dt_utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_date(value: Any) -> dt.date | None:
    """
    Accepts ISO date/datetime strings from *arr and returns a date.
    Returns None for empty / invalid / sentinel dates.
    """
    if not value:
        return None
    try:
        s = str(value).strip()
        if not s:
            return None
        # Handle sentinel "0001-01-01T00:00:00Z" or similar
        if s.startswith("0001-01-01"):
            return None
        # Works for "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SSZ"
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {"title": settings.title, "refreshSeconds": settings.refresh_seconds}


@app.get("/api/weather")
async def weather() -> dict[str, Any]:
    """
    Simple weather proxy using Open-Meteo (no API key).
    Config via DASH_WEATHER_LAT / DASH_WEATHER_LON (and optional DASH_WEATHER_LABEL).
    """
    if settings.weather_lat is None or settings.weather_lon is None:
        return {"configured": False}

    params = {
        "latitude": str(settings.weather_lat),
        "longitude": str(settings.weather_lon),
        "current": "temperature_2m,weather_code,is_day",
        "timezone": settings.weather_timezone or "auto",
    }
    url = "https://api.open-meteo.com/v1/forecast"

    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(url, params=params)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail={"service": "weather", "status": r.status_code})
        data: Any = r.json()

    cur = data.get("current") if isinstance(data, dict) else None
    if not isinstance(cur, dict):
        return {"configured": True, "label": (settings.weather_label or "").strip() or None, "error": "no current"}

    temp = cur.get("temperature_2m")
    code = cur.get("weather_code")
    is_day = cur.get("is_day")
    t = cur.get("time")

    return {
        "configured": True,
        "label": (settings.weather_label or "").strip() or None,
        "tempC": temp,
        "code": code,
        "isDay": bool(int(is_day)) if is_day is not None else None,
        "time": t,
    }


def _read_meminfo() -> dict[str, int]:
    """
    Returns meminfo values in kB (Linux). Empty dict if not available.
    """
    path = "/proc/meminfo"
    try:
        out: dict[str, int] = {}
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    key = parts[0][:-1]
                    try:
                        out[key] = int(parts[1])
                    except Exception:
                        continue
        return out
    except Exception:
        return {}


@app.get("/api/system")
async def system() -> dict[str, Any]:
    """
    Basic host stats (inside container):
    - RAM usage (Linux /proc/meminfo)
    - disk usage for configured mount points
    """
    host = socket.gethostname()

    mem = _read_meminfo()
    mem_total_kb = mem.get("MemTotal")
    mem_avail_kb = mem.get("MemAvailable") or mem.get("MemFree")
    mem_used_kb = None
    if mem_total_kb is not None and mem_avail_kb is not None:
        mem_used_kb = max(0, mem_total_kb - mem_avail_kb)

    disks_cfg = settings.disks()
    if not disks_cfg:
        disks_cfg = [{"label": "Root", "path": "/"}]

    disks_out: list[dict[str, Any]] = []
    for d in disks_cfg:
        label = d.get("label") or "Disk"
        path = d.get("path") or "/"
        try:
            du = shutil.disk_usage(path)
            disks_out.append(
                {
                    "label": label,
                    "path": path,
                    "total": du.total,
                    "used": du.used,
                    "free": du.free,
                }
            )
        except Exception as e:
            disks_out.append({"label": label, "path": path, "error": str(e)})

    # Network throughput:
    # - Prefer qBittorrent global transfer speeds if configured (matches "torrent en cours" expectation)
    # - Fallback to container network namespace (/proc/net/dev) otherwise
    rx_bps: float | None = None
    tx_bps: float | None = None
    net_source: str | None = None

    if settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password:
        base = settings.qbittorrent_url
        async with httpx.AsyncClient(timeout=6) as client:
            try:
                await _qb_login(client, base, settings.qbittorrent_username, settings.qbittorrent_password)
                r = await client.get(f"{base.rstrip('/')}/api/v2/transfer/info")
                if r.status_code < 400:
                    data: Any = r.json()
                    if isinstance(data, dict):
                        rx_bps = float(data.get("dl_info_speed") or 0.0)
                        tx_bps = float(data.get("up_info_speed") or 0.0)
                        net_source = "qbittorrent"
            except Exception:
                pass

    if net_source is None:
        rx, tx = _read_net_totals_linux()
        now = time.time()
        if rx is not None and tx is not None:
            last_rx = _NET_LAST.get("rx")
            last_tx = _NET_LAST.get("tx")
            last_t = _NET_LAST.get("t")
            if last_rx is not None and last_tx is not None and last_t is not None:
                dt_s = max(0.001, now - float(last_t))
                rx_bps = max(0.0, (float(rx) - float(last_rx)) / dt_s)
                tx_bps = max(0.0, (float(tx) - float(last_tx)) / dt_s)
            _NET_LAST["rx"] = float(rx)
            _NET_LAST["tx"] = float(tx)
            _NET_LAST["t"] = float(now)
            net_source = "container"

    return {
        "host": host,
        "memory": {
            "totalBytes": (mem_total_kb * 1024) if mem_total_kb is not None else None,
            "usedBytes": (mem_used_kb * 1024) if mem_used_kb is not None else None,
            "availBytes": (mem_avail_kb * 1024) if mem_avail_kb is not None else None,
        },
        "network": {"rxBps": rx_bps, "txBps": tx_bps, "source": net_source},
        "disks": disks_out,
    }


def _start_of_today_utc() -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc)


@app.get("/api/library/today")
async def library_today(limit: int = 12) -> dict[str, Any]:
    """
    "Ajouté dans la bibliothèque aujourd’hui" = history imports Sonarr + Radarr.
    We use history endpoints and keep items whose date >= start of today (UTC).
    """
    start = _start_of_today_utc()
    lim = max(1, min(limit, 50))
    items: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10) as client:
        # Sonarr
        if settings.sonarr_url and settings.sonarr_api_key:
            try:
                r = await client.get(
                    f"{settings.sonarr_url.rstrip('/')}/api/v3/history",
                    headers={"X-Api-Key": settings.sonarr_api_key},
                    params={"page": "1", "pageSize": "50", "sortKey": "date", "sortDirection": "descending"},
                )
                if r.status_code < 400:
                    data: Any = r.json()
                    records = data.get("records") if isinstance(data, dict) else None
                    if isinstance(records, list):
                        for rec in records:
                            when = rec.get("date")
                            dt_when = None
                            try:
                                dt_when = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
                            except Exception:
                                dt_when = None
                            if not dt_when or dt_when < start:
                                continue
                            event_type = str(rec.get("eventType") or "")
                            if event_type and "import" not in event_type.lower():
                                continue
                            title = str(rec.get("sourceTitle") or rec.get("title") or "").strip()
                            if title:
                                items.append({"type": "sonarr", "title": title, "date": dt_when.isoformat()})
            except Exception:
                pass

        # Radarr
        if settings.radarr_url and settings.radarr_api_key:
            try:
                r = await client.get(
                    f"{settings.radarr_url.rstrip('/')}/api/v3/history",
                    headers={"X-Api-Key": settings.radarr_api_key},
                    params={"page": "1", "pageSize": "50", "sortKey": "date", "sortDirection": "descending"},
                )
                if r.status_code < 400:
                    data = r.json()
                    records = data.get("records") if isinstance(data, dict) else None
                    if isinstance(records, list):
                        for rec in records:
                            when = rec.get("date")
                            dt_when = None
                            try:
                                dt_when = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
                            except Exception:
                                dt_when = None
                            if not dt_when or dt_when < start:
                                continue
                            event_type = str(rec.get("eventType") or "")
                            if event_type and "import" not in event_type.lower():
                                continue
                            title = str(rec.get("sourceTitle") or rec.get("title") or "").strip()
                            if title:
                                items.append({"type": "radarr", "title": title, "date": dt_when.isoformat()})
            except Exception:
                pass

    # sort newest first
    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"count": min(lim, len(items)), "items": items[:lim]}


@app.get("/api/scans")
async def scans() -> dict[str, Any]:
    """
    Discrete "Dernier scan" info using /api/v3/system/task (Sonarr/Radarr).
    We return the most recent lastExecutionTime for each service.
    """
    out: dict[str, Any] = {"sonarr": None, "radarr": None}

    async with httpx.AsyncClient(timeout=8) as client:
        if settings.sonarr_url and settings.sonarr_api_key:
            try:
                r = await client.get(
                    f"{settings.sonarr_url.rstrip('/')}/api/v3/system/task",
                    headers={"X-Api-Key": settings.sonarr_api_key},
                )
                if r.status_code < 400:
                    tasks: Any = r.json()
                    best: str | None = None
                    if isinstance(tasks, list):
                        for t in tasks:
                            last = t.get("lastExecutionTime") or t.get("lastExecution") or t.get("lastRun")
                            if not last:
                                continue
                            s = str(last)
                            if not best or s > best:
                                best = s
                    out["sonarr"] = best
            except Exception:
                pass

        if settings.radarr_url and settings.radarr_api_key:
            try:
                r = await client.get(
                    f"{settings.radarr_url.rstrip('/')}/api/v3/system/task",
                    headers={"X-Api-Key": settings.radarr_api_key},
                )
                if r.status_code < 400:
                    tasks = r.json()
                    best = None
                    if isinstance(tasks, list):
                        for t in tasks:
                            last = t.get("lastExecutionTime") or t.get("lastExecution") or t.get("lastRun")
                            if not last:
                                continue
                            s = str(last)
                            if not best or s > best:
                                best = s
                    out["radarr"] = best
            except Exception:
                pass

    return out


def _default_links_for_host(host: str) -> list[dict[str, str]]:
    base = f"http://{host}"
    return [
        {"label": "Jellyfin", "url": f"{base}:8096"},
        {"label": "Jellyseerr", "url": f"{base}:5055"},
        {"label": "Sonarr", "url": f"{base}:8989"},
        {"label": "Radarr", "url": f"{base}:7878"},
        {"label": "Prowlarr", "url": f"{base}:9696"},
        {"label": "qBittorrent", "url": f"{base}:8080"},
    ]


@app.get("/api/links")
def links(req: Request) -> dict[str, Any]:
    configured = settings.links()
    if configured:
        return {"links": configured}
    host = req.url.hostname or "localhost"
    return {"links": _default_links_for_host(host)}


@app.get("/api/sonarr/upcoming")
async def sonarr_upcoming(days: int = 30, limit: int = 10) -> dict[str, Any]:
    base = _require(settings.sonarr_url, "Sonarr URL")
    api_key = _require(settings.sonarr_api_key, "Sonarr API key")

    start = _dt_utc_now().date()
    end = start + dt.timedelta(days=max(1, min(days, 30)))

    url = f"{base.rstrip('/')}/api/v3/calendar"
    headers = {"X-Api-Key": api_key}
    params = {"start": start.isoformat(), "end": end.isoformat(), "includeSeries": "true"}

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Sonarr error ({r.status_code})")
        items: list[dict[str, Any]] = r.json()

    def sort_key(x: dict[str, Any]) -> str:
        return str(x.get("airDateUtc") or x.get("airDate") or "")

    out: list[dict[str, Any]] = []
    for x in sorted(items, key=sort_key):
        series = x.get("series") or {}
        out.append(
            {
                "seriesTitle": series.get("title") or x.get("seriesTitle"),
                "episodeTitle": x.get("title"),
                "seasonNumber": x.get("seasonNumber"),
                "episodeNumber": x.get("episodeNumber"),
                "airDateUtc": x.get("airDateUtc") or x.get("airDate"),
                "hasFile": x.get("hasFile", False),
            }
        )

    return {"rangeDays": days, "count": min(limit, len(out)), "items": out[: max(0, min(limit, 50))]}


@app.get("/api/radarr/upcoming")
async def radarr_upcoming(days: int = 90, limit: int = 10) -> dict[str, Any]:
    base = _require(settings.radarr_url, "Radarr URL")
    api_key = _require(settings.radarr_api_key, "Radarr API key")

    start = _dt_utc_now().date()
    end = start + dt.timedelta(days=max(1, min(days, 90)))

    url = f"{base.rstrip('/')}/api/v3/calendar"
    headers = {"X-Api-Key": api_key}
    params = {"start": start.isoformat(), "end": end.isoformat()}

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Radarr error ({r.status_code})")
        items: list[dict[str, Any]] = r.json()

    def pick_date(x: dict[str, Any]) -> str:
        # Prefer Digital/Physical release when present, fallback to InCinemas
        return str(
            x.get("digitalRelease")
            or x.get("physicalRelease")
            or x.get("inCinemas")
            or x.get("premiereDate")
            or ""
        )

    out: list[dict[str, Any]] = []
    for x in sorted(items, key=pick_date):
        out.append(
            {
                "title": x.get("title"),
                "year": x.get("year"),
                "releaseDate": pick_date(x),
                "hasFile": x.get("hasFile", False),
                "status": x.get("status"),
            }
        )

    return {"rangeDays": days, "count": min(limit, len(out)), "items": out[: max(0, min(limit, 50))]}


@app.get("/api/radarr/soon")
async def radarr_soon(days_future: int = 365, limit: int = 10) -> dict[str, Any]:
    """
    "Films bientôt téléchargés" = agrégation:
    - Unreleased (monitored, pas de fichier, date de sortie future ou inconnue)
    - Missing (monitored, pas de fichier, déjà sorti)
    - Queued (présent dans la queue Radarr)
    """
    base = _require(settings.radarr_url, "Radarr URL")
    api_key = _require(settings.radarr_api_key, "Radarr API key")
    headers = {"X-Api-Key": api_key}

    today = _dt_utc_now().date()
    max_future = today + dt.timedelta(days=max(1, min(days_future, 3650)))

    async with httpx.AsyncClient(timeout=12) as client:
        # Movies list (source of truth for title + release dates)
        movies_r = await client.get(f"{base.rstrip('/')}/api/v3/movie", headers=headers)
        if movies_r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Radarr error ({movies_r.status_code})")
        movies: list[dict[str, Any]] = movies_r.json()

        # Queue (optional, but helps identify "Queued")
        queued_ids: set[int] = set()
        try:
            q_params = {"page": "1", "pageSize": "200", "sortKey": "timeleft", "sortDirection": "ascending"}
            queue_r = await client.get(f"{base.rstrip('/')}/api/v3/queue", headers=headers, params=q_params)
            if queue_r.status_code < 400:
                q_data: Any = queue_r.json()
                records = q_data.get("records") if isinstance(q_data, dict) else None
                if isinstance(records, list):
                    for rec in records:
                        mid = rec.get("movieId")
                        if isinstance(mid, int):
                            queued_ids.add(mid)
        except Exception:
            pass

    def pick_release_date(x: dict[str, Any]) -> dt.date | None:
        # Prefer Digital/Physical release when present, fallback to InCinemas / PremiereDate
        return (
            _parse_date(x.get("digitalRelease"))
            or _parse_date(x.get("physicalRelease"))
            or _parse_date(x.get("inCinemas"))
            or _parse_date(x.get("premiereDate"))
        )

    out: list[dict[str, Any]] = []
    for m in movies:
        mid = m.get("id")
        if not isinstance(mid, int):
            continue

        monitored = bool(m.get("monitored", False))
        has_file = bool(m.get("hasFile", False))
        if not monitored:
            continue
        if has_file and mid not in queued_ids:
            continue

        release_date = pick_release_date(m)

        # Categorize
        is_queued = mid in queued_ids
        if is_queued:
            reason = "Queued"
        else:
            # If already released (or release date passed), it's missing; otherwise unreleased
            if release_date and release_date <= today:
                reason = "Missing"
            else:
                reason = "Unreleased"

        # Filter far-future unreleased items (still keep queued / missing)
        if reason == "Unreleased" and release_date and release_date > max_future:
            continue

        missing_icons: list[str] = []
        if reason == "Missing":
            in_cinemas = _parse_date(m.get("inCinemas"))
            digital = _parse_date(m.get("digitalRelease"))
            physical = _parse_date(m.get("physicalRelease"))

            # Heuristics:
            # - 🎥 if we only have in-cinemas info (often means not yet on home release)
            if in_cinemas and in_cinemas <= today and (digital is None and physical is None):
                missing_icons.append("🎥")

            # - 📀 if physical (BluRay) date is in the future
            if physical and physical > today:
                missing_icons.append("📀")

            # - 🔍 missing + monitored typically means Radarr is searching
            missing_icons.append("🔍")

            # - 🌍 older missing items might be region/availability related
            if release_date and (today - release_date).days >= 90:
                missing_icons.append("🌍")

        out.append(
            {
                "id": mid,
                "title": m.get("title"),
                "year": m.get("year"),
                "releaseDate": (release_date.isoformat() if release_date else None),
                "reason": reason,
                "status": m.get("status"),
                "hasFile": has_file,
                "monitored": monitored,
                "missingIcons": missing_icons,
            }
        )

    def sort_key(x: dict[str, Any]) -> tuple[int, str, str]:
        d = x.get("releaseDate") or ""
        # Unknown dates last
        unknown = 1 if not d else 0
        return (unknown, d, str(x.get("title") or ""))

    out_sorted = sorted(out, key=sort_key)[: max(0, min(limit, 50))]
    return {"rangeDaysFuture": days_future, "count": len(out_sorted), "items": out_sorted}


async def _qb_login(client: httpx.AsyncClient, base: str, username: str, password: str) -> None:
    url = f"{base.rstrip('/')}/api/v2/auth/login"
    r = await client.post(url, data={"username": username, "password": password})
    # qBittorrent returns 200 "Ok." on success, 403 on failure
    if r.status_code >= 400 or "Ok" not in r.text:
        raise HTTPException(status_code=502, detail="qBittorrent login failed")


@app.get("/api/qbittorrent/torrents")
async def qbittorrent_torrents(
    filter: Literal["all", "downloading", "seeding", "completed", "active"] = "active",
    limit: int = 10,
) -> dict[str, Any]:
    base = _require(settings.qbittorrent_url, "qBittorrent URL")
    user = _require(settings.qbittorrent_username, "qBittorrent username")
    pw = _require(settings.qbittorrent_password, "qBittorrent password")

    async with httpx.AsyncClient(timeout=10) as client:
        await _qb_login(client, base, user, pw)
        url = f"{base.rstrip('/')}/api/v2/torrents/info"
        params = {
            "filter": "all" if filter == "active" else filter,
            "sort": "added_on",
            "reverse": "true",
            "limit": str(max(1, min(limit, 50))),
        }
        r = await client.get(url, params=params)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"qBittorrent error ({r.status_code})")
        torrents: list[dict[str, Any]] = r.json()

    if filter == "active":
        active_states = {
            "downloading",
            "metadl",
            "stalleddl",
            "queueddl",
            "checkingdl",
            "allocating",
            "forceddl",
        }
        torrents = [
            t
            for t in torrents
            if (t.get("dlspeed", 0) or 0) > 0
            or (t.get("upspeed", 0) or 0) > 0
            or str(t.get("state") or "").lower() in active_states
        ]

    out: list[dict[str, Any]] = []
    for t in torrents[: max(1, min(limit, 50))]:
        out.append(
            {
                "name": t.get("name"),
                "state": t.get("state"),
                "progress": t.get("progress"),
                "dlspeed": t.get("dlspeed"),
                "upspeed": t.get("upspeed"),
                "eta": t.get("eta"),
                "size": t.get("size"),
                "added_on": t.get("added_on"),
            }
        )
    return {"filter": filter, "count": len(out), "items": out}


def _jellyfin_headers() -> dict[str, str]:
    token = _require(settings.jellyfin_api_key, "Jellyfin API key")
    return {"X-Emby-Token": token}


@app.get("/api/jellyfin/latest")
async def jellyfin_latest(limit: int | None = None) -> dict[str, Any]:
    base = _require(settings.jellyfin_url, "Jellyfin URL")
    lim = max(1, min(limit or settings.jellyfin_latest_limit, 50))

    params = {
        "IncludeItemTypes": "Movie,Episode",
        "Limit": str(lim),
        "Fields": "DateCreated,PrimaryImageAspectRatio,PremiereDate,UserData",
        "EnableImages": "true",
        "ImageTypeLimit": "1",
        "EnableUserData": "true",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # Sur certains Jellyfin, le endpoint recommandé est user-scoped:
        # /Users/{userId}/Items/Latest
        if settings.jellyfin_user_id:
            url = f"{base.rstrip('/')}/Users/{settings.jellyfin_user_id}/Items/Latest"
        else:
            url = f"{base.rstrip('/')}/Items/Latest"
        r = await client.get(url, headers=_jellyfin_headers(), params=params)
        if r.status_code >= 400:
            detail: dict[str, Any] = {"service": "jellyfin", "status": r.status_code}
            try:
                detail["body"] = r.json()
            except Exception:
                detail["body"] = (r.text or "").strip()[:500]
            raise HTTPException(status_code=502, detail=detail)
        items: list[dict[str, Any]] = r.json()

    out: list[dict[str, Any]] = []
    for it in items:
        ud = it.get("UserData") if isinstance(it, dict) else None
        user_data_out: dict[str, Any] | None = None
        if isinstance(ud, dict):
            user_data_out = {
                "played": bool(ud.get("Played")),
                "playbackPositionTicks": ud.get("PlaybackPositionTicks"),
                "playCount": ud.get("PlayCount"),
                "lastPlayedDate": ud.get("LastPlayedDate"),
            }
        out.append(
            {
                "id": it.get("Id"),
                "name": it.get("Name"),
                "type": it.get("Type"),
                "seriesName": it.get("SeriesName"),
                "productionYear": it.get("ProductionYear"),
                "indexNumber": it.get("IndexNumber"),
                "parentIndexNumber": it.get("ParentIndexNumber"),
                "dateCreated": it.get("DateCreated"),
                "premiereDate": it.get("PremiereDate"),
                "hasPrimaryImage": bool(it.get("ImageTags", {}).get("Primary")),
                "userData": user_data_out,
            }
        )

    return {"count": len(out), "items": out}


@app.get("/api/jellyfin/items/{item_id}/image")
async def jellyfin_primary_image(item_id: str, maxHeight: int = 240, quality: int = 80) -> Response:
    base = _require(settings.jellyfin_url, "Jellyfin URL")
    url = f"{base.rstrip('/')}/Items/{item_id}/Images/Primary"
    params = {"maxHeight": str(max(80, min(maxHeight, 600))), "quality": str(max(10, min(quality, 95)))}

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_jellyfin_headers(), params=params)
        if r.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={"service": "jellyfin", "status": r.status_code, "endpoint": "primaryImage"},
            )
        content_type = r.headers.get("content-type") or "image/jpeg"
        return Response(content=r.content, media_type=content_type, headers={"Cache-Control": "public, max-age=300"})


async def _status_sonarr() -> ServiceStatus:
    if not (settings.sonarr_url and settings.sonarr_api_key):
        return ServiceStatus("Sonarr", False, "not configured")
    url = f"{settings.sonarr_url.rstrip('/')}/api/v3/system/status"
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(url, headers={"X-Api-Key": settings.sonarr_api_key})
        if r.status_code >= 400:
            return ServiceStatus("Sonarr", False, f"http {r.status_code}")
        data = r.json()
        ver = data.get("version")
        return ServiceStatus("Sonarr", True, f"v{ver}" if ver else "ok")


async def _status_radarr() -> ServiceStatus:
    if not (settings.radarr_url and settings.radarr_api_key):
        return ServiceStatus("Radarr", False, "not configured")
    url = f"{settings.radarr_url.rstrip('/')}/api/v3/system/status"
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(url, headers={"X-Api-Key": settings.radarr_api_key})
        if r.status_code >= 400:
            return ServiceStatus("Radarr", False, f"http {r.status_code}")
        data = r.json()
        ver = data.get("version")
        return ServiceStatus("Radarr", True, f"v{ver}" if ver else "ok")


async def _status_jellyfin() -> ServiceStatus:
    if not settings.jellyfin_url:
        return ServiceStatus("Jellyfin", False, "not configured")
    url = f"{settings.jellyfin_url.rstrip('/')}/System/Info/Public"
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            return ServiceStatus("Jellyfin", False, f"http {r.status_code}")
        data = r.json()
        ver = data.get("Version")
        return ServiceStatus("Jellyfin", True, f"v{ver}" if ver else "ok")


async def _status_qb() -> ServiceStatus:
    if not (settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password):
        return ServiceStatus("qBittorrent", False, "not configured")
    base = settings.qbittorrent_url
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            await _qb_login(client, base, settings.qbittorrent_username, settings.qbittorrent_password)
        except HTTPException as e:
            return ServiceStatus("qBittorrent", False, str(e.detail))
        r = await client.get(f"{base.rstrip('/')}/api/v2/app/version")
        if r.status_code >= 400:
            return ServiceStatus("qBittorrent", False, f"http {r.status_code}")
        return ServiceStatus("qBittorrent", True, f"v{r.text.strip()}" if r.text else "ok")


@app.get("/api/status")
async def status() -> JSONResponse:
    sonarr_s, radarr_s, qb_s, jelly_s = (
        await _status_sonarr(),
        await _status_radarr(),
        await _status_qb(),
        await _status_jellyfin(),
    )
    items = [sonarr_s, radarr_s, qb_s, jelly_s]
    return JSONResponse(
        {
            "ok": all(x.ok for x in items if x.detail != "not configured"),
            "items": [{"name": x.name, "ok": x.ok, "detail": x.detail} for x in items],
        }
    )

