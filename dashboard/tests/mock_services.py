"""Local upstream fixture used for visual and manual dashboard testing."""

from __future__ import annotations

import datetime as dt

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse


app = FastAPI()


def _iso(days: int = 0, hours: int = 0) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days, hours=hours)).isoformat()


@app.get("/api/v3/system/status")
@app.get("/api/v1/system/status")
def arr_status() -> dict[str, str]:
    return {"version": "4.0.12"}


@app.get("/api/v3/calendar")
def calendar(request: Request) -> list[dict]:
    if request.query_params.get("includeSeries") == "true":
        return [
            {"series": {"title": "Severance"}, "title": "Cold Harbor", "seasonNumber": 2, "episodeNumber": 10, "airDateUtc": _iso(hours=3), "hasFile": False},
            {"series": {"title": "The Last of Us"}, "title": "The Path", "seasonNumber": 2, "episodeNumber": 4, "airDateUtc": _iso(days=1, hours=2), "hasFile": False},
            {"series": {"title": "Foundation"}, "title": "A Song for the End", "seasonNumber": 3, "episodeNumber": 2, "airDateUtc": _iso(days=4), "hasFile": False},
        ]
    return []


@app.get("/api/v3/queue")
def queue(request: Request) -> dict[str, list]:
    if request.query_params.get("includeSeries") == "true":
        return {"records": []}
    return {"records": [{"movieId": 2}]}


@app.get("/api/v3/movie")
def movies() -> list[dict]:
    today = dt.date.today()
    return [
        {"id": 1, "title": "Mickey 17", "year": 2025, "monitored": True, "hasFile": False, "digitalRelease": str(today - dt.timedelta(days=5)), "status": "released"},
        {"id": 2, "title": "Project Hail Mary", "year": 2026, "monitored": True, "hasFile": False, "digitalRelease": str(today + dt.timedelta(days=12)), "status": "announced"},
        {"id": 3, "title": "Dune: Messiah", "year": 2026, "monitored": True, "hasFile": False, "inCinemas": str(today + dt.timedelta(days=110)), "status": "announced"},
    ]


@app.get("/api/v3/history")
def history() -> dict[str, list]:
    return {"records": [
        {"date": _iso(hours=-1), "eventType": "downloadFolderImported", "sourceTitle": "Severance S02E10 2160p WEB-DL"},
        {"date": _iso(hours=-3), "eventType": "downloadFolderImported", "sourceTitle": "Mickey 17 2025 1080p BluRay"},
    ]}


@app.get("/api/v3/system/task")
def tasks() -> list[dict]:
    return [{"name": "Refresh", "lastExecutionTime": _iso(hours=-1)}]


@app.post("/api/v2/auth/login")
def qb_login() -> PlainTextResponse:
    return PlainTextResponse("Ok.")


@app.get("/api/v2/app/version")
def qb_version() -> PlainTextResponse:
    return PlainTextResponse("5.0.4")


@app.get("/api/v2/torrents/info")
def torrents() -> list[dict]:
    return [
        {"hash": "one", "name": "Severance.S02E10.2160p.WEB-DL.x265", "state": "downloading", "progress": 0.72, "dlspeed": 18_400_000, "upspeed": 640_000, "eta": 540, "size": 9_600_000_000, "amount_left": 2_688_000_000, "num_seeds": 42, "num_leechs": 8},
        {"hash": "two", "name": "Mickey.17.2025.1080p.BluRay.x265", "state": "stalledDL", "progress": 0.34, "dlspeed": 0, "upspeed": 0, "eta": 8_640_000, "size": 5_200_000_000, "amount_left": 3_432_000_000, "num_seeds": 0, "num_leechs": 3},
        {"hash": "three", "name": "Foundation.S03E02.1080p.WEB-DL", "state": "downloading", "progress": 0.91, "dlspeed": 6_200_000, "upspeed": 180_000, "eta": 130, "size": 3_100_000_000, "amount_left": 279_000_000, "num_seeds": 18, "num_leechs": 2},
    ]


@app.get("/api/v2/transfer/info")
def transfer() -> dict[str, int]:
    return {"dl_info_speed": 24_600_000, "up_info_speed": 820_000}


@app.get("/api/v2/sync/maindata")
def main_data() -> dict[str, dict[str, int]]:
    return {"server_state": {"alltime_ul": 1_800_000_000_000, "alltime_dl": 8_400_000_000_000}}


@app.get("/System/Info/Public")
@app.get("/System/Info")
def jellyfin_status() -> dict[str, str]:
    return {"Version": "10.10.7"}


@app.get("/Items")
def jellyfin_latest() -> dict[str, list[dict]]:
    return {"Items": [
        {"Id": "1", "Name": "Cold Harbor", "Type": "Episode", "SeriesName": "Severance", "ParentIndexNumber": 2, "IndexNumber": 10, "DateCreated": _iso(hours=-1), "ImageTags": {"Primary": "a"}, "UserData": {"Played": False}},
        {"Id": "2", "Name": "Mickey 17", "Type": "Movie", "ProductionYear": 2025, "DateCreated": _iso(hours=-3), "ImageTags": {"Primary": "b"}, "UserData": {"Played": False, "PlaybackPositionTicks": 1200}},
        {"Id": "3", "Name": "The Path", "Type": "Episode", "SeriesName": "The Last of Us", "ParentIndexNumber": 2, "IndexNumber": 4, "DateCreated": _iso(hours=-8), "ImageTags": {"Primary": "c"}, "UserData": {"Played": True}},
        {"Id": "4", "Name": "Silo", "Type": "Episode", "SeriesName": "Silo", "ParentIndexNumber": 2, "IndexNumber": 7, "DateCreated": _iso(days=-1), "ImageTags": {"Primary": "d"}, "UserData": {"Played": False}},
    ]}


@app.get("/Items/{item_id}/Images/Primary")
def jellyfin_image(item_id: str) -> Response:
    colors = {"1": ("#4d8b78", "#8ee6bd"), "2": ("#71584a", "#f2bd78"), "3": ("#465b87", "#9dc2ff"), "4": ("#674d7d", "#cba8f5")}
    start, end = colors.get(item_id, ("#334", "#889"))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="360"><defs><linearGradient id="g"><stop stop-color="{start}"/><stop offset="1" stop-color="{end}"/></linearGradient></defs><rect width="600" height="360" fill="url(#g)"/><circle cx="470" cy="90" r="120" fill="white" opacity=".09"/><path d="M0 300L180 130l110 90 100-110 210 190" fill="none" stroke="white" opacity=".25" stroke-width="28"/></svg>'
    return Response(svg, media_type="image/svg+xml")


@app.post("/Library/Refresh", status_code=204)
def jellyfin_refresh() -> None:
    return None


@app.get("/api/v1/status")
def jellyseerr_status() -> dict[str, str]:
    return {"version": "2.5.2"}


@app.get("/api/v1/search")
def jellyseerr_search(query: str = "") -> dict[str, list]:
    return {"results": [
        {"mediaType": "tv", "tmdbId": 95396, "name": "Severance", "firstAirDate": "2022-02-18", "mediaInfo": {"status": 4}},
        {"mediaType": "tv", "tmdbId": 125988, "name": "Silo", "firstAirDate": "2023-05-04", "mediaInfo": {"status": 1}},
        {"mediaType": "movie", "tmdbId": 696506, "title": "Mickey 17", "releaseDate": "2025-03-05", "mediaInfo": {"status": 5}},
    ]}


@app.get("/api/v1/tv/{media_id}")
def jellyseerr_tv(media_id: int) -> dict:
    return {
        "name": "Silo",
        "seasons": [
            {"seasonNumber": 1, "name": "Saison 1", "episodeCount": 10},
            {"seasonNumber": 2, "name": "Saison 2", "episodeCount": 10},
        ],
        "mediaInfo": {"status": 4, "seasons": [{"seasonNumber": 1, "status": 5}, {"seasonNumber": 2, "status": 1}]},
    }


@app.post("/api/v1/request")
def jellyseerr_request() -> dict[str, int | str]:
    return {"id": 42, "status": "pending"}


@app.get("/health")
def flaresolverr_health() -> dict[str, str]:
    return {"status": "ok", "version": "3.4.0"}


@app.get("/api/status")
def portainer_status() -> dict[str, str]:
    return {"Version": "2.31.3"}


@app.get("/api/system/status")
def bazarr_status() -> dict[str, str]:
    return {"version": "1.5.2"}


@app.get("/v1/vpn/status")
def vpn_status() -> dict[str, str]:
    return {"status": "running"}


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}
