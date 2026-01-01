from __future__ import annotations

import datetime as dt
import os
import shutil
import socket
import time
import logging
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from urllib.parse import quote

from .settings import ServiceStatus, Settings


settings = Settings()

app = FastAPI(title=settings.title)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

log = logging.getLogger("uvicorn.error")


_NET_LAST: dict[str, float] = {}
_DISK_LAST: dict[str, float] = {}


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


def _read_disk_totals_linux() -> tuple[int | None, int | None]:
    """
    Read total disk read/write bytes from /proc/diskstats (Linux).
    We only include base block devices (present in /sys/block) to avoid double-counting partitions.

    Returns (read_bytes, write_bytes) or (None, None) if unavailable.
    Note: Uses 512 bytes/sector (typical) as approximation.
    """
    path = "/proc/diskstats"
    try:
        read_sectors_total = 0
        write_sectors_total = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                # Keep only base devices (exclude partitions) using /sys/block presence.
                if not os.path.exists(f"/sys/block/{name}"):
                    continue
                # Exclude loop/ram devices
                if name.startswith(("loop", "ram")):
                    continue
                # sectors read at index 5, sectors written at index 9
                try:
                    read_sectors_total += int(parts[5])
                    write_sectors_total += int(parts[9])
                except Exception:
                    continue
        # Approx sector size (512B)
        return read_sectors_total * 512, write_sectors_total * 512
    except Exception:
        return None, None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


def _require(v: str | None, name: str) -> str:
    if not v:
        raise HTTPException(status_code=503, detail=f"{name} not configured on server")
    return v


def _jellyseerr_headers() -> dict[str, str]:
    token = _require(settings.jellyseerr_api_key, "Jellyseerr API key")
    return {"X-Api-Key": token}


class JellyseerrRequest(BaseModel):
    mediaId: int
    mediaType: Literal["tv", "movie"] = "tv"
    seasons: list[int] | None = None


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
    # Helps confirm the running container actually picked up the latest code.
    try:
        code_version = int(Path(__file__).stat().st_mtime)
    except Exception:
        code_version = None
    return {"title": settings.title, "refreshSeconds": settings.refresh_seconds, "codeVersion": code_version}


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
        try:
            r = await client.get(url, params=params)
            if r.status_code >= 400:
                raise HTTPException(status_code=502, detail={"service": "weather", "status": r.status_code})
            data: Any = r.json()
        except httpx.TimeoutException:
            return {"configured": True, "label": (settings.weather_label or "").strip() or None, "error": "timeout"}
        except Exception as e:
            return {
                "configured": True,
                "label": (settings.weather_label or "").strip() or None,
                "error": "unavailable",
                "detail": str(e)[:200],
            }

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


@app.get("/api/jellyseerr/search")
async def jellyseerr_search(
    query: str, type: Literal["tv", "movie"] = "tv", debug: bool = False
) -> dict[str, Any]:
    """
    Search Jellyseerr (Overseerr-compatible API).
    """
    base = _require(settings.jellyseerr_url, "Jellyseerr URL")
    q = (query or "").strip()
    if len(q) < 2:
        return {"count": 0, "items": []}

    async with httpx.AsyncClient(timeout=12) as client:
        try:
            # Jellyseerr expects `query` to be percent-encoded (spaces as %20, not '+')
            # and rejects reserved characters when not encoded.
            encoded_q = quote(q, safe="")
            url = f"{base.rstrip('/')}/api/v1/search?query={encoded_q}&page=1"
            log.info("jellyseerr.search start query=%r url=%s", q, url)
            r = await client.get(url, headers=_jellyseerr_headers())
        except Exception as e:
            log.warning("jellyseerr.search request failed query=%r err=%r", q, e)
            raise HTTPException(status_code=502, detail={"service": "jellyseerr", "error": str(e)})

        if r.status_code >= 400:
            detail: dict[str, Any] = {"service": "jellyseerr", "status": r.status_code}
            try:
                detail["body"] = r.json()
            except Exception:
                detail["body"] = (r.text or "").strip()[:500]
            log.warning(
                "jellyseerr.search upstream error query=%r status=%s body=%r",
                q,
                r.status_code,
                detail.get("body"),
            )
            raise HTTPException(status_code=502, detail=detail)

        try:
            data = r.json()
        except Exception:
            log.warning(
                "jellyseerr.search invalid json query=%r status=%s ct=%r body=%r",
                q,
                r.status_code,
                r.headers.get("content-type"),
                (r.text or "").strip()[:200],
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "service": "jellyseerr",
                    "status": r.status_code,
                    "error": "invalid json",
                    "contentType": r.headers.get("content-type"),
                    "body": (r.text or "").strip()[:500],
                },
            )

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        log.info(
            "jellyseerr.search unexpected shape query=%r dataType=%s keys=%s",
            q,
            data.__class__.__name__,
            sorted(list(data.keys())) if isinstance(data, dict) else None,
        )
        return {"count": 0, "items": [], "debug": {"shape": data.__class__.__name__} if debug else None}

    debug_sample: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    dropped_type = 0
    dropped_id = 0
    for it in results:
        if not isinstance(it, dict):
            continue
        media_type = str(it.get("mediaType") or "").strip().lower()
        wanted = str(type).lower()
        if media_type != wanted:
            dropped_type += 1
            if debug and len(debug_sample) < 8:
                debug_sample.append(
                    {
                        "mediaType": media_type,
                        "tmdbId": it.get("tmdbId"),
                        "id": it.get("id"),
                        "name": it.get("name") or it.get("title"),
                    }
                )
            continue
        # Jellyseerr usually provides tmdbId, but some installs / versions can return `id`.
        tmdb_raw = it.get("tmdbId")
        if tmdb_raw is None:
            tmdb_raw = it.get("id")
        tmdb_id: int | None = None
        if isinstance(tmdb_raw, int):
            tmdb_id = tmdb_raw
        elif isinstance(tmdb_raw, str):
            s = tmdb_raw.strip()
            if s.isdigit():
                try:
                    tmdb_id = int(s)
                except Exception:
                    tmdb_id = None
        if tmdb_id is None:
            dropped_id += 1
            if debug and len(debug_sample) < 8:
                debug_sample.append(
                    {
                        "mediaType": media_type,
                        "tmdbId": tmdb_raw,
                        "id": it.get("id"),
                        "name": it.get("name") or it.get("title"),
                        "note": "tmdbId not int",
                    }
                )
            continue
        title = str(it.get("name") or it.get("title") or "").strip() or "—"
        year = None
        date_str = it.get("firstAirDate") if media_type == "tv" else it.get("releaseDate")
        try:
            if date_str:
                year = int(str(date_str)[:4])
        except Exception:
            year = None
        mi = it.get("mediaInfo") if isinstance(it.get("mediaInfo"), dict) else {}
        status = mi.get("status")
        seasons_status: list[dict[str, Any]] | None = None
        if media_type == "tv":
            raw_ss = mi.get("seasons")
            if isinstance(raw_ss, list):
                ss_out: list[dict[str, Any]] = []
                for s_it in raw_ss:
                    if not isinstance(s_it, dict):
                        continue
                    sn = s_it.get("seasonNumber")
                    st = s_it.get("status")
                    if isinstance(sn, int):
                        ss_out.append({"seasonNumber": sn, "status": st})
                seasons_status = ss_out if ss_out else None
        out.append(
            {
                "mediaType": media_type,
                "mediaId": tmdb_id,
                "title": title,
                "year": year,
                "status": status,
                "seasonsStatus": seasons_status,
            }
        )

    resp: dict[str, Any] = {"count": len(out), "items": out[:50]}
    if debug:
        resp["debug"] = {
            "wanted": str(type).lower(),
            "resultsCount": len(results),
            "sampleFiltered": debug_sample,
            "rawKeys": sorted(list(data.keys())) if isinstance(data, dict) else None,
        }
    if len(out) == 0:
        # High-signal one-liner when things look "ok" but we filtered everything.
        sample = []
        for it in results[:5]:
            if isinstance(it, dict):
                sample.append(
                    {
                        "mediaType": it.get("mediaType"),
                        "tmdbId": it.get("tmdbId"),
                        "id": it.get("id"),
                        "name": it.get("name") or it.get("title"),
                    }
                )
        log.info(
            "jellyseerr.search filtered_all query=%r results=%d droppedType=%d droppedId=%d sample=%s",
            q,
            len(results),
            dropped_type,
            dropped_id,
            sample,
        )
    else:
        log.info("jellyseerr.search ok query=%r results=%d kept=%d", q, len(results), len(out))
    return resp


@app.get("/api/jellyseerr/tv/{media_id}")
async def jellyseerr_tv(media_id: int) -> dict[str, Any]:
    """
    Fetch TV details (including seasons) from Jellyseerr.
    Used to pick seasons before requesting.
    """
    base = _require(settings.jellyseerr_url, "Jellyseerr URL")
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(f"{base.rstrip('/')}/api/v1/tv/{media_id}", headers=_jellyseerr_headers())
        if r.status_code >= 400:
            detail: dict[str, Any] = {"service": "jellyseerr", "status": r.status_code}
            try:
                detail["body"] = r.json()
            except Exception:
                detail["body"] = (r.text or "").strip()[:500]
            raise HTTPException(status_code=502, detail=detail)
        try:
            data: Any = r.json()
        except Exception:
            raise HTTPException(
                status_code=502,
                detail={
                    "service": "jellyseerr",
                    "status": r.status_code,
                    "error": "invalid json",
                    "contentType": r.headers.get("content-type"),
                    "body": (r.text or "").strip()[:500],
                },
            )

    if not isinstance(data, dict):
        return {"mediaId": media_id, "seasons": []}

    title = str(data.get("name") or data.get("title") or "").strip() or None
    mi = data.get("mediaInfo") if isinstance(data.get("mediaInfo"), dict) else {}
    status = mi.get("status")

    seasons: list[dict[str, Any]] = []
    raw_seasons = data.get("seasons")
    if isinstance(raw_seasons, list):
        for s in raw_seasons:
            if not isinstance(s, dict):
                continue
            sn = s.get("seasonNumber")
            if not isinstance(sn, int):
                continue
            if sn <= 0:
                # skip specials by default (season 0)
                continue
            seasons.append(
                {
                    "seasonNumber": sn,
                    "name": str(s.get("name") or f"Saison {sn}"),
                    "episodeCount": s.get("episodeCount"),
                }
            )

    # Season status list (if present)
    season_status_map: dict[int, Any] = {}
    raw_ss = mi.get("seasons")
    if isinstance(raw_ss, list):
        for ss in raw_ss:
            if not isinstance(ss, dict):
                continue
            sn = ss.get("seasonNumber")
            if isinstance(sn, int):
                season_status_map[sn] = ss.get("status")
    for s in seasons:
        sn = int(s["seasonNumber"])
        if sn in season_status_map:
            s["status"] = season_status_map.get(sn)

    seasons.sort(key=lambda x: int(x.get("seasonNumber") or 0))
    return {"mediaId": media_id, "title": title, "status": status, "seasons": seasons}


@app.post("/api/jellyseerr/request")
async def jellyseerr_request(req: JellyseerrRequest) -> dict[str, Any]:
    """
    Create a request in Jellyseerr.
    """
    base = _require(settings.jellyseerr_url, "Jellyseerr URL")

    payload: dict[str, Any] = {"mediaId": req.mediaId, "mediaType": req.mediaType}
    if req.seasons:
        payload["seasons"] = req.seasons

    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.post(
            f"{base.rstrip('/')}/api/v1/request",
            headers={**_jellyseerr_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code >= 400:
            detail: dict[str, Any] = {"service": "jellyseerr", "status": r.status_code}
            try:
                detail["body"] = r.json()
            except Exception:
                detail["body"] = (r.text or "").strip()[:500]
            raise HTTPException(status_code=502, detail=detail)
        try:
            return {"ok": True, "result": r.json()}
        except Exception:
            return {"ok": True}


@app.get("/api/weather/forecast")
async def weather_forecast(days: int = 7) -> dict[str, Any]:
    """
    Detailed weather (today + week + hourly precip) via Open-Meteo (no API key).
    """
    if settings.weather_lat is None or settings.weather_lon is None:
        return {"configured": False}

    d = max(1, min(days, 14))
    params = {
        "latitude": str(settings.weather_lat),
        "longitude": str(settings.weather_lon),
        "timezone": settings.weather_timezone or "auto",
        "forecast_days": str(d),
        "current": "temperature_2m,weather_code,is_day,precipitation,precipitation_probability",
        "hourly": "temperature_2m,precipitation,precipitation_probability,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,sunrise,sunset",
    }
    url = "https://api.open-meteo.com/v1/forecast"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(url, params=params)
            if r.status_code >= 400:
                raise HTTPException(status_code=502, detail={"service": "weather", "status": r.status_code})
            data: Any = r.json()
        except httpx.TimeoutException:
            return {"configured": True, "label": (settings.weather_label or "").strip() or None, "error": "timeout"}
        except Exception as e:
            return {
                "configured": True,
                "label": (settings.weather_label or "").strip() or None,
                "error": "unavailable",
                "detail": str(e)[:200],
            }

    cur = data.get("current") if isinstance(data, dict) else None
    hourly = data.get("hourly") if isinstance(data, dict) else None
    daily = data.get("daily") if isinstance(data, dict) else None

    out: dict[str, Any] = {
        "configured": True,
        "label": (settings.weather_label or "").strip() or None,
        "timezone": data.get("timezone") if isinstance(data, dict) else None,
        "current": None,
        "hourly": [],
        "daily": [],
    }

    if isinstance(cur, dict):
        out["current"] = {
            "time": cur.get("time"),
            "tempC": cur.get("temperature_2m"),
            "code": cur.get("weather_code"),
            "isDay": bool(int(cur.get("is_day"))) if cur.get("is_day") is not None else None,
            "precipMm": cur.get("precipitation"),
            "precipProb": cur.get("precipitation_probability"),
        }

    if isinstance(hourly, dict):
        times = hourly.get("time")
        temps = hourly.get("temperature_2m")
        codes = hourly.get("weather_code")
        is_days = hourly.get("is_day")
        precs = hourly.get("precipitation")
        probs = hourly.get("precipitation_probability")
        if isinstance(times, list):
            for i, t in enumerate(times):
                out["hourly"].append(
                    {
                        "time": t,
                        "tempC": temps[i] if isinstance(temps, list) and i < len(temps) else None,
                        "code": codes[i] if isinstance(codes, list) and i < len(codes) else None,
                        "isDay": (
                            bool(int(is_days[i])) if isinstance(is_days, list) and i < len(is_days) and is_days[i] is not None else None
                        ),
                        "precipMm": precs[i] if isinstance(precs, list) and i < len(precs) else None,
                        "precipProb": probs[i] if isinstance(probs, list) and i < len(probs) else None,
                    }
                )

    if isinstance(daily, dict):
        times = daily.get("time")
        codes = daily.get("weather_code")
        tmax = daily.get("temperature_2m_max")
        tmin = daily.get("temperature_2m_min")
        psum = daily.get("precipitation_sum")
        pmax = daily.get("precipitation_probability_max")
        sunrise = daily.get("sunrise")
        sunset = daily.get("sunset")
        if isinstance(times, list):
            for i, t in enumerate(times):
                out["daily"].append(
                    {
                        "date": t,
                        "code": codes[i] if isinstance(codes, list) and i < len(codes) else None,
                        "tMax": tmax[i] if isinstance(tmax, list) and i < len(tmax) else None,
                        "tMin": tmin[i] if isinstance(tmin, list) and i < len(tmin) else None,
                        "precipSum": psum[i] if isinstance(psum, list) and i < len(psum) else None,
                        "precipProbMax": pmax[i] if isinstance(pmax, list) and i < len(pmax) else None,
                        "sunrise": sunrise[i] if isinstance(sunrise, list) and i < len(sunrise) else None,
                        "sunset": sunset[i] if isinstance(sunset, list) and i < len(sunset) else None,
                    }
                )

    return out


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

    # Disk IO throughput (approx host view via /proc/diskstats)
    disk_read_bps: float | None = None
    disk_write_bps: float | None = None
    disk_source: str | None = None
    dr, dw = _read_disk_totals_linux()
    now2 = time.time()
    if dr is not None and dw is not None:
        last_dr = _DISK_LAST.get("r")
        last_dw = _DISK_LAST.get("w")
        last_t2 = _DISK_LAST.get("t")
        if last_dr is not None and last_dw is not None and last_t2 is not None:
            dt_s = max(0.001, now2 - float(last_t2))
            disk_read_bps = max(0.0, (float(dr) - float(last_dr)) / dt_s)
            disk_write_bps = max(0.0, (float(dw) - float(last_dw)) / dt_s)
        _DISK_LAST["r"] = float(dr)
        _DISK_LAST["w"] = float(dw)
        _DISK_LAST["t"] = float(now2)
        disk_source = "diskstats"

    return {
        "host": host,
        "memory": {
            "totalBytes": (mem_total_kb * 1024) if mem_total_kb is not None else None,
            "usedBytes": (mem_used_kb * 1024) if mem_used_kb is not None else None,
            "availBytes": (mem_avail_kb * 1024) if mem_avail_kb is not None else None,
        },
        "network": {"rxBps": rx_bps, "txBps": tx_bps, "source": net_source},
        "diskIo": {"readBps": disk_read_bps, "writeBps": disk_write_bps, "source": disk_source},
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


@app.get("/api/sonarr/importing")
async def sonarr_importing(limit: int = 20) -> dict[str, Any]:
    """
    Expose items currently being imported/moved by Sonarr (Completed Download Handling).

    Sonarr surfaces this via /api/v3/queue with statuses like "importing"/"processing".
    """
    base = _require(settings.sonarr_url, "Sonarr URL")
    api_key = _require(settings.sonarr_api_key, "Sonarr API key")
    headers = {"X-Api-Key": api_key}

    params = {
        "page": "1",
        "pageSize": "200",
        "sortKey": "timeleft",
        "sortDirection": "ascending",
        "includeSeries": "true",
        "includeEpisode": "true",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{base.rstrip('/')}/api/v3/queue", headers=headers, params=params)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Sonarr error ({r.status_code})")
        data: Any = r.json()

    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return {"count": 0, "items": []}

    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        status = str(rec.get("status") or "").strip()
        state = str(rec.get("trackedDownloadState") or "").strip()
        st = f"{status} {state}".lower()
        if "import" not in st and "process" not in st and "move" not in st:
            continue

        series = rec.get("series") if isinstance(rec.get("series"), dict) else {}
        episode = rec.get("episode") if isinstance(rec.get("episode"), dict) else {}
        out.append(
            {
                "seriesTitle": series.get("title") or rec.get("seriesTitle"),
                "episodeTitle": episode.get("title") or rec.get("episodeTitle"),
                "seasonNumber": episode.get("seasonNumber") or rec.get("seasonNumber"),
                "episodeNumber": episode.get("episodeNumber") or rec.get("episodeNumber"),
                "status": status or None,
                "trackedDownloadState": state or None,
                "progress": rec.get("progress"),
                "timeleft": rec.get("timeleft"),
                "outputPath": rec.get("outputPath") or rec.get("downloadClientOutputPath"),
            }
        )

    def sk(x: dict[str, Any]) -> str:
        return str(x.get("timeleft") or "") + str(x.get("seriesTitle") or "")

    items = sorted(out, key=sk)[: max(0, min(limit, 50))]
    return {"count": len(items), "items": items}


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
    limit: int | None = None,
) -> dict[str, Any]:
    base = _require(settings.qbittorrent_url, "qBittorrent URL")
    user = _require(settings.qbittorrent_username, "qBittorrent username")
    pw = _require(settings.qbittorrent_password, "qBittorrent password")

    async with httpx.AsyncClient(timeout=10) as client:
        await _qb_login(client, base, user, pw)
        url = f"{base.rstrip('/')}/api/v2/torrents/info"
        params: dict[str, str] = {
            "filter": "all" if filter == "active" else filter,
            "sort": "added_on",
            "reverse": "true",
        }
        # If limit is unset/<=0 => no limit (qBittorrent returns all).
        if limit is not None and limit > 0:
            params["limit"] = str(limit)
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
    view = torrents if not (limit is not None and limit > 0) else torrents[:limit]
    for t in view:
        out.append(
            {
                "hash": t.get("hash"),
                "name": t.get("name"),
                "state": t.get("state"),
                "progress": t.get("progress"),
                "dlspeed": t.get("dlspeed"),
                "upspeed": t.get("upspeed"),
                "eta": t.get("eta"),
                "size": t.get("size"),
                "amount_left": t.get("amount_left"),
                "num_seeds": t.get("num_seeds"),
                "num_leechs": t.get("num_leechs"),
                "ratio": t.get("ratio"),
                "category": t.get("category"),
                "tags": t.get("tags"),
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
        try:
            items: list[dict[str, Any]] = r.json()
        except Exception:
            raise HTTPException(
                status_code=502,
                detail={
                    "service": "jellyfin",
                    "status": r.status_code,
                    "error": "invalid json",
                    "contentType": r.headers.get("content-type"),
                    "body": (r.text or "").strip()[:200],
                },
            )

        # Series progress lookup (episodes watched / total) for episode items.
        # Only reliable when we have a configured user id.
        series_progress: dict[str, dict[str, int]] = {}
        if settings.jellyfin_user_id:
            series_ids: set[str] = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                if str(it.get("Type") or "").lower() != "episode":
                    continue
                sid = it.get("SeriesId")
                if sid:
                    series_ids.add(str(sid))

            for sid in series_ids:
                try:
                    s_url = f"{base.rstrip('/')}/Users/{settings.jellyfin_user_id}/Items/{sid}"
                    s_params = {"Fields": "RecursiveItemCount,UserData", "EnableUserData": "true"}
                    sr = await client.get(s_url, headers=_jellyfin_headers(), params=s_params)
                    if sr.status_code >= 400:
                        continue
                    data: Any = sr.json()
                    if not isinstance(data, dict):
                        continue
                    total = data.get("RecursiveItemCount")
                    ud = data.get("UserData") if isinstance(data.get("UserData"), dict) else {}
                    unplayed = ud.get("UnplayedItemCount") if isinstance(ud, dict) else None
                    if isinstance(total, int) and total > 0 and isinstance(unplayed, int) and unplayed >= 0:
                        watched = max(0, total - unplayed)
                        series_progress[sid] = {"watched": watched, "total": total}
                except Exception:
                    continue

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
        sid_val = it.get("SeriesId") if isinstance(it, dict) else None
        series_id = str(sid_val) if sid_val else None
        sp = series_progress.get(series_id) if series_id else None
        out.append(
            {
                "id": it.get("Id"),
                "name": it.get("Name"),
                "type": it.get("Type"),
                "seriesName": it.get("SeriesName"),
                "seriesId": series_id,
                "seriesProgress": sp,
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
        try:
            data = r.json()
        except Exception:
            ct = (r.headers.get("content-type") or "").strip()
            return ServiceStatus("Jellyfin", False, f"invalid json ({ct or 'unknown'})")
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

