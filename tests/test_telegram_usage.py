import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_generator.telegram import usage


class TelegramUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_deliveries_increment_global_and_user_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            with patch.object(usage, "USAGE_FILE", path):
                first = await usage.record_successful_delivery(101, "first_user")
                second = await usage.record_successful_delivery(202, "second_user")
                updated = await usage.record_successful_delivery(101, "new_name")
                own_totals = await usage.get_usage(202)

            self.assertEqual(
                first,
                {
                    "total_successful_deliveries": 1,
                    "user_successful_deliveries": 1,
                },
            )
            self.assertEqual(
                second,
                {
                    "total_successful_deliveries": 2,
                    "user_successful_deliveries": 1,
                },
            )
            self.assertEqual(
                updated,
                {
                    "total_successful_deliveries": 3,
                    "user_successful_deliveries": 2,
                },
            )
            self.assertEqual(own_totals["user_successful_deliveries"], 1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["users"]["101"],
                {"username": "new_name", "successful_deliveries": 2},
            )

    async def test_invalid_usage_file_reports_storage_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            path.write_text("not json", encoding="utf-8")
            with patch.object(usage, "USAGE_FILE", path):
                with self.assertRaises(usage.UsageStoreError):
                    await usage.get_usage()


if __name__ == "__main__":
    unittest.main()
