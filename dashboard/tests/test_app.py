from __future__ import annotations

import unittest

import httpx

from app.main import app


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
        self.assertGreaterEqual(body["summary"]["total"], 13)
        self.assertTrue({"gluetun", "qbittorrent", "sonarr-private", "watchtower", "cloudflared"}.issubset(slugs))
        self.assertEqual(body["summary"]["healthy"] + (body["summary"]["total"] - body["summary"]["healthy"]), body["summary"]["total"])

    async def test_default_links_follow_request_host(self) -> None:
        response = await self.client.get("/api/links", headers={"host": "media-box.local:3000"})
        self.assertEqual(response.status_code, 200)
        urls = [item["url"] for item in response.json()["links"]]
        self.assertTrue(all("media-box.local" in url for url in urls))


if __name__ == "__main__":
    unittest.main()
