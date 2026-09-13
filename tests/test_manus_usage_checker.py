import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import manus_usage_checker as checker


class ManusUsageCheckerTests(unittest.TestCase):
    def test_parse_datetime_supports_iso_date_and_unix_milliseconds(self):
        self.assertEqual(checker.parse_datetime("2026-09-13T10:00:00"), datetime(2026, 9, 13, 10))
        self.assertEqual(checker.parse_datetime("2026/09/13").date().isoformat(), "2026-09-13")
        self.assertEqual(checker.parse_datetime(1_700_000_000_000).year, 2023)
        self.assertIsNone(checker.parse_datetime("invalid"))

    def test_parse_number_supports_commas_and_suffixes(self):
        self.assertEqual(checker.parse_number("1,200"), 1200)
        self.assertEqual(checker.parse_number("1.5K"), 1500)
        self.assertEqual(checker.parse_number("invalid"), 0)

    def test_normalize_record_accepts_common_aliases(self):
        result = checker.normalize_record({
            "createdAt": "2026-09-13T10:00:00Z",
            "taskName": "Weekly report",
            "state": "completed",
            "credits_used": "2.5",
            "duration_seconds": "30",
        })
        self.assertEqual(result["task"], "Weekly report")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["usage"], 2.5)
        self.assertEqual(result["duration"], 30)

    def test_load_csv_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "history.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=("date", "task", "status", "usage"))
                writer.writeheader()
                writer.writerow({"date": "2026-09-13", "task": "A", "status": "success", "usage": "3"})
            json_path = root / "history.json"
            json_path.write_text(json.dumps({"records": [{"timestamp": "2026-09-12", "title": "B", "amount": 4}]}), encoding="utf-8")
            csv_records = checker.load_history(csv_path)
            json_records = checker.load_history(json_path)
        self.assertEqual(csv_records[0]["usage"], 3)
        self.assertEqual(json_records[0]["task"], "B")
        self.assertEqual(json_records[0]["usage"], 4)

    def test_aggregate_and_group_by_task(self):
        records = [
            checker.normalize_record({"date": "2026-09-13", "task": "A", "status": "success", "usage": 3, "duration": 10}),
            checker.normalize_record({"date": "2026-09-12", "task": "A", "status": "failed", "usage": 2, "duration": 20}),
            checker.normalize_record({"date": "2026-09-11", "task": "B", "status": "completed", "usage": 5, "duration": 5}),
        ]
        result = checker.aggregate(records, since=datetime(2026, 9, 12))
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["usage"], 5)
        self.assertEqual(result["success"], 1)
        groups = checker.group_by_task(records)
        self.assertEqual(groups["A"]["count"], 2)
        self.assertEqual(groups["A"]["usage"], 5)

    def test_invalid_record_and_extension_are_rejected(self):
        self.assertIsNone(checker.normalize_record({"task": "missing date"}))
        with self.assertRaises(ValueError):
            checker.load_history("history.txt")


if __name__ == "__main__":
    unittest.main()
