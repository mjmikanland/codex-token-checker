import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import codex_gui_v6 as app


class TokenUtilityTests(unittest.TestCase):
    def test_empty_usage_has_all_expected_counters(self):
        self.assertEqual(
            app.empty_usage(),
            {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 0,
            },
        )

    def test_normalize_usage_converts_values_and_ignores_invalid_values(self):
        result = app.normalize_usage(
            {
                "input_tokens": "12",
                "output_tokens": 3.8,
                "cached_input_tokens": None,
                "reasoning_output_tokens": "invalid",
                "unknown": 999,
            }
        )
        self.assertEqual(result["input_tokens"], 12)
        self.assertEqual(result["output_tokens"], 3)
        self.assertEqual(result["cached_input_tokens"], 0)
        self.assertEqual(result["reasoning_output_tokens"], 0)
        self.assertEqual(result["total_tokens"], 0)
        self.assertNotIn("unknown", result)

    def test_add_usage_and_delta(self):
        total = app.empty_usage()
        app.add_usage(total, {"input_tokens": 10, "total_tokens": 15})
        app.add_usage(total, {"input_tokens": 2, "total_tokens": 4})
        self.assertEqual(total["input_tokens"], 12)
        self.assertEqual(total["total_tokens"], 19)

        newer = {"input_tokens": 20, "total_tokens": 30}
        older = {"input_tokens": 25, "total_tokens": 10}
        delta = app.usage_delta(newer, older)
        self.assertEqual(delta["input_tokens"], 0)
        self.assertEqual(delta["total_tokens"], 20)

    def test_number_and_compact_number(self):
        self.assertEqual(app.number(1234567), "1,234,567")
        self.assertEqual(app.compact_number(999), "999")
        self.assertEqual(app.compact_number(1200), "1.2K")
        self.assertEqual(app.compact_number(2_500_000), "2.5M")

    def test_parse_timestamp_accepts_iso_unix_seconds_and_milliseconds(self):
        self.assertEqual(app.parse_timestamp("2026-01-02T03:04:05"), datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(app.parse_timestamp("2026-01-02T03:04:05Z").year, 2026)
        self.assertEqual(app.parse_timestamp(1_700_000_000).year, 2023)
        self.assertEqual(app.parse_timestamp(1_700_000_000_000).year, 2023)
        self.assertIsNone(app.parse_timestamp("not-a-timestamp"))
        self.assertIsNone(app.parse_timestamp(None))

    def test_basename_project_handles_empty_and_nested_paths(self):
        self.assertEqual(app.basename_project("C:/work/demo"), "demo")
        self.assertEqual(app.basename_project(""), "(プロジェクト不明)")
        self.assertEqual(app.basename_project(None), "(プロジェクト不明)")


class SessionLoadingTests(unittest.TestCase):
    def write_session(self, root: Path, relative: str, lines):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        return path

    def token_count(self, timestamp, total, input_tokens, rate=25, reset="2026-01-02T10:00:00Z"):
        return {
            "timestamp": timestamp,
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": 2,
                    "cache_write_input_tokens": 1,
                    "output_tokens": 3,
                    "reasoning_output_tokens": 4,
                    "total_tokens": total,
                }},
                "rate_limits": {
                    "primary": {"used_percent": rate, "resets_at": reset},
                    "plan_type": "plus",
                },
            },
        }

    def test_load_sessions_reads_latest_usage_metadata_rate_and_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(
                root,
                "sessions/2026/01/02/session.jsonl",
                [
                    {"payload": {"type": "message", "metadata": {"cwd": "/work/demo", "model": "gpt-test"}}},
                    {"timestamp": "2026-01-02T09:00:00Z", "payload": {"type": "ignored"}},
                    self.token_count("2026-01-02T09:00:00Z", 10, 5, rate=20),
                    self.token_count("2026-01-02T09:05:00Z", 25, 12, rate=30),
                    {"not": "valid json event"},
                ],
            )
            with patch.object(app, "SESSIONS_DIR", root):
                sessions = app.load_sessions()

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["usage"]["total_tokens"], 25)
        self.assertEqual(session["usage"]["input_tokens"], 12)
        self.assertEqual(session["rate"], 30.0)
        self.assertEqual(session["max_rate"], 30.0)
        self.assertEqual(session["plan"], "plus")
        self.assertEqual(session["project"], "demo")
        self.assertEqual(session["model"], "gpt-test")
        self.assertEqual(len(session["samples"]), 2)
        self.assertEqual(session["date"].isoformat(), "2026-01-02")

    def test_load_sessions_skips_missing_usage_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(root, "sessions/2026/01/02/no-token.jsonl", [{"payload": {"type": "message"}}])
            malformed = root / "sessions/2026/01/02/malformed.jsonl"
            malformed.parent.mkdir(parents=True, exist_ok=True)
            malformed.write_text("{invalid json\n", encoding="utf-8")
            with patch.object(app, "SESSIONS_DIR", root):
                self.assertEqual(app.load_sessions(), [])

    def test_recent_usage_uses_deltas_from_cumulative_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(
                root,
                "sessions/2026/01/02/session.jsonl",
                [
                    self.token_count("2026-01-02T09:00:00", 100, 60),
                    self.token_count("2026-01-02T09:10:00", 140, 80),
                ],
            )
            with patch.object(app, "SESSIONS_DIR", root):
                sessions = app.load_sessions()

        since = datetime(2026, 1, 2, 9, 5)
        recent = app.recent_usage_from_samples(sessions, since)
        self.assertEqual(recent["total_tokens"], 40)
        self.assertEqual(recent["input_tokens"], 20)

    def test_rate_values_finds_nested_used_percent_values(self):
        values = app._rate_values({"primary": {"used_percent": 12}, "secondary": [{"used_percent": "34.5"}]})
        self.assertCountEqual(values, [12.0, 34.5])


if __name__ == "__main__":
    unittest.main()
