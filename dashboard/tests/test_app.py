from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.main import _qb_headers, app, jellyfin_latest, settings


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

    async def test_jellyfin_latest_uses_user_id_query_parameter(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["userId"] = request.url.params.get("userId", "")
            return httpx.Response(200, json=[])

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
        self.assertEqual(captured["path"], "/Items/Latest")
        self.assertEqual(captured["userId"], "user-123")


if __name__ == "__main__":
    unittest.main()
