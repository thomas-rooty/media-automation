from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.main import _jellyfin_image_candidate, _qb_headers, _qb_login, app, jellyfin_latest, settings


class DashboardSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_health_is_independent_from_upstreams(self) -> None:
        response = await self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    async def test_frontend_is_served(self) -> None:
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Centre de contrôle média", response.text)
        self.assertIn('id="serviceStrip"', response.text)
        self.assertIn('class="mobile-dock"', response.text)

    async def test_status_never_hides_expected_services(self) -> None:
        response = await self.client.get("/api/status?refresh=true")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        slugs = {item["slug"] for item in body["items"]}
        self.assertGreaterEqual(body["summary"]["total"], 12)
        self.assertTrue({"gluetun", "qbittorrent", "sonarr-private", "cloudflared"}.issubset(slugs))
        self.assertNotIn("watchtower", slugs)
        self.assertEqual(body["summary"]["healthy"] + (body["summary"]["total"] - body["summary"]["healthy"]), body["summary"]["total"])

    async def test_default_links_follow_request_host(self) -> None:
        response = await self.client.get("/api/links", headers={"host": "media-box.local:3000"})
        self.assertEqual(response.status_code, 200)
        urls = [item["url"] for item in response.json()["links"]]
        self.assertTrue(all("media-box.local" in url for url in urls))

    async def test_qbittorrent_docker_host_can_be_overridden(self) -> None:
        previous = settings.qbittorrent_host
        try:
            settings.qbittorrent_host = "127.0.0.1"
            headers = _qb_headers("http://gluetun:8080")
        finally:
            settings.qbittorrent_host = previous
        self.assertEqual(headers["Host"], "127.0.0.1:8080")
        self.assertEqual(headers["Origin"], "http://127.0.0.1:8080")
        self.assertEqual(headers["Referer"], "http://127.0.0.1:8080/")

    async def test_qbittorrent_accepts_empty_204_login(self) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(204))
        async with httpx.AsyncClient(transport=transport) as client:
            await _qb_login(client, "http://gluetun:8080", "admin", "password")

    async def test_jellyfin_latest_uses_stable_items_query(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["userId"] = request.url.params.get("userId", "")
            captured["fields"] = ",".join(request.url.params.get_list("fields"))
            captured["types"] = ",".join(request.url.params.get_list("includeItemTypes"))
            captured["sortBy"] = request.url.params.get("sortBy", "")
            return httpx.Response(200, json={"Items": []})

        real_client = httpx.AsyncClient
        fake_client = real_client(transport=httpx.MockTransport(handler))
        previous = (settings.jellyfin_url, settings.jellyfin_api_key, settings.jellyfin_user_id)
        settings.jellyfin_url = "http://jellyfin:8096"
        settings.jellyfin_api_key = "test-key"
        settings.jellyfin_user_id = "user-123"
        try:
            with patch("app.main.httpx.AsyncClient", return_value=fake_client):
                result = await jellyfin_latest(limit=8)
        finally:
            settings.jellyfin_url, settings.jellyfin_api_key, settings.jellyfin_user_id = previous

        self.assertEqual(result["count"], 0)
        self.assertEqual(captured["path"], "/Items")
        self.assertEqual(captured["userId"], "user-123")
        self.assertEqual(captured["types"], "Movie,Episode")
        self.assertEqual(captured["fields"], "DateCreated,PrimaryImageAspectRatio")
        self.assertEqual(captured["sortBy"], "DateCreated")

    async def test_jellyfin_latest_retries_without_stale_user_id(self) -> None:
        requests: list[bool] = []

        def handler(request: httpx.Request) -> httpx.Response:
            has_user = "userId" in request.url.params
            requests.append(has_user)
            if has_user:
                return httpx.Response(400, text="Error processing request.")
            return httpx.Response(200, json={"Items": []})

        real_client = httpx.AsyncClient
        fake_client = real_client(transport=httpx.MockTransport(handler))
        previous = (settings.jellyfin_url, settings.jellyfin_api_key, settings.jellyfin_user_id)
        settings.jellyfin_url = "http://jellyfin:8096"
        settings.jellyfin_api_key = "test-key"
        settings.jellyfin_user_id = "stale-user"
        try:
            with patch("app.main.httpx.AsyncClient", return_value=fake_client):
                result = await jellyfin_latest(limit=1)
        finally:
            settings.jellyfin_url, settings.jellyfin_api_key, settings.jellyfin_user_id = previous

        self.assertEqual(requests, [True, False])
        self.assertFalse(result["userContext"])

    async def test_jellyfin_episode_image_falls_back_to_series(self) -> None:
        candidate = _jellyfin_image_candidate(
            {
                "Id": "episode-1",
                "Type": "Episode",
                "ImageTags": {},
                "SeriesId": "series-1",
                "SeriesPrimaryImageTag": "image-tag",
            }
        )
        self.assertEqual(candidate, ("series-1", "Primary"))

    async def test_jellyfin_episode_prefers_own_thumbnail(self) -> None:
        candidate = _jellyfin_image_candidate(
            {
                "Id": "episode-1",
                "Type": "Episode",
                "ImageTags": {"Thumb": "thumb-tag", "Primary": "primary-tag"},
                "SeriesId": "series-1",
                "SeriesPrimaryImageTag": "series-tag",
            }
        )
        self.assertEqual(candidate, ("episode-1", "Thumb"))


if __name__ == "__main__":
    unittest.main()
