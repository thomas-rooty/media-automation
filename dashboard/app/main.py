from __future__ import annotations

import datetime as dt
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


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


def _require(v: str | None, name: str) -> str:
    if not v:
        raise HTTPException(status_code=503, detail=f"{name} not configured on server")
    return v


def _dt_utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {"title": settings.title, "refreshSeconds": settings.refresh_seconds}


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
async def sonarr_upcoming(days: int = 7, limit: int = 10) -> dict[str, Any]:
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
async def radarr_upcoming(days: int = 14, limit: int = 10) -> dict[str, Any]:
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
        "Fields": "DateCreated,PrimaryImageAspectRatio,PremiereDate",
        "EnableImages": "true",
        "ImageTypeLimit": "1",
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
    sonarr_s, qb_s, jelly_s = await _status_sonarr(), await _status_qb(), await _status_jellyfin()
    items = [sonarr_s, qb_s, jelly_s]
    return JSONResponse(
        {
            "ok": all(x.ok for x in items if x.detail != "not configured"),
            "items": [{"name": x.name, "ok": x.ok, "detail": x.detail} for x in items],
        }
    )

