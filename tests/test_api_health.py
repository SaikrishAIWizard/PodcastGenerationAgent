import unittest

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
