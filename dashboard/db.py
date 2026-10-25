"""
Lightweight SQLite persistence for AquaScan.
-----------------------------------------------
Keeps sensor readings and image-test verdicts on disk so history
survives a server restart (previously it lived only in an in-memory
deque and was lost every time). Uses Python's built-in sqlite3 -
no extra dependency needed.
"""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "aquascan.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tds REAL, turbidity REAL, temperature REAL,
            ph REAL, do_val REAL, orp REAL,
            source TEXT, timestamp TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verdicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            verdict TEXT,
            issues TEXT,
            recommendations TEXT,
            overall_advice TEXT,
            sensor_readings TEXT,
            cv_result TEXT,
            image_url TEXT,
            timestamp TEXT
        )
    """)
    # Migration: older DB files won't have these columns yet (added when the
    # detailed, inspector-style recommendation plan was introduced). Adding
    # them via ALTER TABLE keeps existing history rows intact.
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(verdicts)")}
    if "recommendation_plan" not in existing_cols:
        conn.execute("ALTER TABLE verdicts ADD COLUMN recommendation_plan TEXT")
    if "priority_action" not in existing_cols:
        conn.execute("ALTER TABLE verdicts ADD COLUMN priority_action TEXT")
    # Simple key-value settings store. Used for the alert-recipients list
    # so it can be edited from the dashboard UI and survives a restart,
    # instead of being hardcoded to a single ALERT_EMAIL_TO env var.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = _connect()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = _connect()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def insert_reading(reading):
    conn = _connect()
    conn.execute(
        "INSERT INTO readings (tds, turbidity, temperature, ph, do_val, orp, source, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            reading.get("tds"), reading.get("turbidity"), reading.get("temperature"),
            reading.get("ph"), reading.get("do"), reading.get("orp"),
            reading.get("source"), reading.get("timestamp"),
        ),
    )
    conn.commit()
    conn.close()


def get_readings(limit=50):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    readings = [
        {
            "tds": r["tds"], "turbidity": r["turbidity"], "temperature": r["temperature"],
            "ph": r["ph"], "do": r["do_val"], "orp": r["orp"],
            "source": r["source"], "timestamp": r["timestamp"],
        }
        for r in rows
    ]
    readings.reverse()  # oldest -> newest, matches old deque behaviour
    return readings


def insert_verdict(entry):
    conn = _connect()
    conn.execute(
        "INSERT INTO verdicts (verdict, issues, recommendations, overall_advice, "
        "sensor_readings, cv_result, image_url, timestamp, recommendation_plan, priority_action) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry.get("verdict"),
            json.dumps(entry.get("issues", [])),
            json.dumps(entry.get("recommendations", [])),
            entry.get("overall_advice"),
            json.dumps(entry.get("sensor_readings", {})),
            json.dumps(entry.get("cv_result", {})),
            entry.get("image_url"),
            entry.get("timestamp"),
            json.dumps(entry.get("recommendation_plan", [])),
            entry.get("priority_action"),
        ),
    )
    conn.commit()
    conn.close()


def get_verdicts(limit=20):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM verdicts ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [
        {
            "verdict": r["verdict"],
            "issues": json.loads(r["issues"]),
            "recommendations": json.loads(r["recommendations"]),
            "overall_advice": r["overall_advice"],
            "sensor_readings": json.loads(r["sensor_readings"]),
            "cv_result": json.loads(r["cv_result"]),
            "image_url": r["image_url"],
            "timestamp": r["timestamp"],
            # Older rows saved before the detailed recommendation plan was
            # added won't have these columns populated - default gracefully.
            "recommendation_plan": json.loads(r["recommendation_plan"]) if r["recommendation_plan"] else [],
            "priority_action": r["priority_action"],
        }
        for r in rows
    ]
