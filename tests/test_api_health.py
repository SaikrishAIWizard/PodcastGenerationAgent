import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from podcast_generator.api.main import app


class HealthEndpointTests(unittest.TestCase):
    def test_health_and_keep_alive_endpoints(self):
        with TestClient(app) as client:
            for path in ("/health", "/keep-alive"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"status": "ok"})

    def test_usage_route_exposes_only_the_global_delivery_total(self):
        with patch(
            "podcast_generator.api.main.get_usage",
            new=AsyncMock(
                return_value={
                    "total_successful_deliveries": 12,
                    "user_successful_deliveries": 0,
                }
            ),
        ):
            response = TestClient(app).get("/usage")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"total_successful_deliveries": 12},
        )
