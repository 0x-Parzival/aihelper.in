"""Privacy-preserving, local timing telemetry for live voice calls.

This module deliberately stores only call/turn identifiers, event names, and
timestamps.  It has no fields for audio, transcripts, phone numbers, or model
content.  Recording is best effort: a busy writer lock drops an individual
event rather than delaying the real-time audio loop.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable


DEFAULT_STAGES = (
    "caller_speech_end",
    "turn_predicted",
    "asr_final",
    "llm_first_token",
    "tts_first_audio",
    "playback_mark",
)


class CallMetrics:
    """A small SQLite event store intended for the call-media hot path."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._write_lock = threading.Lock()
        self._initialized = False
        self._init_lock = threading.Lock()

    def init_db(self) -> None:
        """Create the schema. Safe to call more than once."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS call_metric_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        call_sid TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        event_name TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_call_metric_events_turn
                      ON call_metric_events(call_sid, turn_id, timestamp_ms);
                    CREATE INDEX IF NOT EXISTS idx_call_metric_events_name
                      ON call_metric_events(call_sid, turn_id, event_name, timestamp_ms);
                    CREATE TABLE IF NOT EXISTS call_turn_decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        call_sid TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_call_turn_decisions_turn
                      ON call_turn_decisions(call_sid, turn_id, timestamp_ms);
                    """
                )
            self._initialized = True

    def record_event(
        self,
        call_sid: str,
        event_name: str,
        *,
        turn_id: str | int = "0",
        timestamp_ms: int | None = None,
    ) -> bool:
        """Best-effort write of one timing event.

        Returns ``False`` if another thread is already writing, so callers can
        safely ignore telemetry rather than introducing media latency.
        """
        if not call_sid:
            raise ValueError("call_sid is required")
        if not event_name or not event_name.replace("_", "").isalnum():
            raise ValueError("event_name must contain only letters, numbers, and underscores")
        if timestamp_ms is None:
            timestamp_ms = time.time_ns() // 1_000_000
        if not isinstance(timestamp_ms, int):
            raise TypeError("timestamp_ms must be an integer")

        self.init_db()
        if not self._write_lock.acquire(blocking=False):
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO call_metric_events(call_sid, turn_id, event_name, timestamp_ms) "
                    "VALUES (?, ?, ?, ?)",
                    (str(call_sid), str(turn_id), event_name, timestamp_ms),
                )
            return True
        finally:
            self._write_lock.release()

    def record_decision(self, call_sid: str, action: str, reason: str, *, turn_id: str | int = "0", timestamp_ms: int | None = None) -> bool:
        """Record a turn-policy decision without audio or transcript content."""
        if not call_sid:
            raise ValueError("call_sid is required")
        if not action or not action.replace("_", "").isalnum():
            raise ValueError("action must contain only letters, numbers, and underscores")
        if not reason or len(reason) > 200:
            raise ValueError("reason must be 1..200 characters")
        if timestamp_ms is None:
            timestamp_ms = time.time_ns() // 1_000_000
        self.init_db()
        if not self._write_lock.acquire(blocking=False):
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO call_turn_decisions(call_sid, turn_id, action, reason, timestamp_ms) VALUES (?, ?, ?, ?, ?)",
                    (str(call_sid), str(turn_id), action, reason, timestamp_ms),
                )
            return True
        finally:
            self._write_lock.release()

    def turn_latency(self, call_sid: str, turn_id: str | int) -> dict:
        """Return first occurrence of each stage and adjacent elapsed times.

        Missing events are represented by absent keys; no latency is fabricated.
        """
        self.init_db()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_name, MIN(timestamp_ms) AS timestamp_ms "
                "FROM call_metric_events WHERE call_sid = ? AND turn_id = ? "
                "GROUP BY event_name",
                (str(call_sid), str(turn_id)),
            ).fetchall()
        events = {row["event_name"]: row["timestamp_ms"] for row in rows}
        ordered = [(stage, events[stage]) for stage in DEFAULT_STAGES if stage in events]
        latencies = {
            f"{previous}_to_{current}": current_time - previous_time
            for (previous, previous_time), (current, current_time) in zip(ordered, ordered[1:])
        }
        return {"call_sid": str(call_sid), "turn_id": str(turn_id), "events_ms": events, "latencies_ms": latencies}

    def call_turn_latencies(self, call_sid: str) -> list[dict]:
        """Return latency summaries for every observed turn in a call."""
        self.init_db()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT turn_id FROM call_metric_events WHERE call_sid = ? "
                "GROUP BY turn_id ORDER BY MIN(id)",
                (str(call_sid),),
            ).fetchall()
        return [self.turn_latency(call_sid, row["turn_id"]) for row in rows]

    def call_timeline(self, call_sid: str) -> list[dict]:
        """Return an ordered, content-free operator timeline for one call."""
        self.init_db()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT turn_id, event_name AS name, timestamp_ms, NULL AS detail, 'event' AS kind "
                "FROM call_metric_events WHERE call_sid = ? "
                "UNION ALL "
                "SELECT turn_id, action AS name, timestamp_ms, reason AS detail, 'decision' AS kind "
                "FROM call_turn_decisions WHERE call_sid = ? "
                "ORDER BY timestamp_ms, kind",
                (str(call_sid), str(call_sid)),
            ).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=0.05)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=50")
        return conn
