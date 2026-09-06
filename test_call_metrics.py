"""Tests for privacy-preserving live-call timing telemetry."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from call_metrics import CallMetrics


class CallMetricsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "metrics.db"
        self.metrics = CallMetrics(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_records_only_named_timing_event(self):
        self.assertTrue(self.metrics.record_event("CA123", "asr_final", turn_id=2, timestamp_ms=1_000))
        with sqlite3.connect(self.path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(call_metric_events)")}
            row = conn.execute("SELECT call_sid, turn_id, event_name, timestamp_ms FROM call_metric_events").fetchone()
        self.assertEqual(columns, {"id", "call_sid", "turn_id", "event_name", "timestamp_ms"})
        self.assertEqual(row, ("CA123", "2", "asr_final", 1_000))

    def test_calculates_adjacent_turn_stages(self):
        for name, timestamp in (
            ("caller_speech_end", 1000),
            ("asr_final", 1250),
            ("llm_first_token", 1400),
            ("tts_first_audio", 1600),
            ("playback_mark", 1750),
        ):
            self.assertTrue(self.metrics.record_event("CA123", name, turn_id="1", timestamp_ms=timestamp))
        result = self.metrics.turn_latency("CA123", "1")
        self.assertEqual(result["latencies_ms"], {
            "caller_speech_end_to_asr_final": 250,
            "asr_final_to_llm_first_token": 150,
            "llm_first_token_to_tts_first_audio": 200,
            "tts_first_audio_to_playback_mark": 150,
        })

    def test_repeated_stage_uses_the_first_timestamp(self):
        self.metrics.record_event("CA123", "asr_final", timestamp_ms=200)
        self.metrics.record_event("CA123", "asr_final", timestamp_ms=250)
        self.assertEqual(self.metrics.turn_latency("CA123", 0)["events_ms"]["asr_final"], 200)

    def test_records_decision_without_conversation_content(self):
        self.assertTrue(self.metrics.record_decision("CA123", "listen", "caller retains the floor", turn_id=3, timestamp_ms=100))
        with self.metrics._connect() as conn:
            row = conn.execute("SELECT call_sid, turn_id, action, reason, timestamp_ms FROM call_turn_decisions").fetchone()
        self.assertEqual(tuple(row), ("CA123", "3", "listen", "caller retains the floor", 100))

    def test_rejects_invalid_event_names(self):
        with self.assertRaises(ValueError):
            self.metrics.record_event("CA123", "audio transcript")

    def test_operator_timeline_is_content_free_and_ordered(self):
        self.metrics.record_event("CA123", "asr_final", turn_id=1, timestamp_ms=200)
        self.metrics.record_decision("CA123", "plan", "likely turn completion", turn_id=1, timestamp_ms=210)
        timeline = self.metrics.call_timeline("CA123")
        self.assertEqual([item["kind"] for item in timeline], ["event", "decision"])
        self.assertEqual(timeline[1]["detail"], "likely turn completion")


if __name__ == "__main__":
    unittest.main()
