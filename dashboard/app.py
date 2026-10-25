"""
Water Quality Monitoring Dashboard - Backend
-----------------------------------------------
Flask server that:
  1. Accepts sensor readings (from ESP32 POST, or a built-in dummy
     data generator while your hardware isn't ready yet).
  2. Accepts an uploaded water sample image, runs it through the
     CV model, and combines it with the latest sensor reading via
     the fusion layer to produce a final verdict.
  3. Serves a simple web dashboard showing live readings + verdict.
  4. Persists readings + verdicts to a local SQLite DB (dashboard/data/aquascan.db)
     so history survives a server restart.
  5. Optionally sends an email alert when a test comes back "Contaminated".

RUN:
  pip install -r ../requirements.txt
  python app.py
Then open http://localhost:5000 in your browser.

SWITCHING FROM DUMMY DATA TO REAL ESP32 DATA:
  Just flash sensor_reader.ino to your ESP32 once it's ready and
  point SERVER_URL in that file at this machine's IP + port 5000.
  Real POSTs to /api/sensor-data will start overriding the dummy
  values automatically - no code change needed here.

EMAIL ALERTS (optional):
  Set these environment variables before running to enable an email
  alert whenever a test verdict comes back "Contaminated":
    ALERT_EMAIL_ENABLED=true
    ALERT_SMTP_HOST=smtp.gmail.com
    ALERT_SMTP_PORT=587
    ALERT_SMTP_USER=youraddress@gmail.com
    ALERT_SMTP_PASSWORD=<app password>
    ALERT_EMAIL_TO=recipient@example.com
  (For Gmail you need an "app password", not your normal login password.)
  Leave ALERT_EMAIL_ENABLED unset/false to skip this entirely - the
  dashboard still shows an on-screen + browser notification either way.
"""

import csv
import html
import io
import json
import os
import random
import re
import smtplib
import sys
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

# Load a .env file placed next to this file (dashboard/.env), regardless of
# what the current working directory happens to be (e.g. when run via
# `gunicorn --chdir dashboard ...` from the Dockerfile).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from fusion.fusion_model import (  # noqa: E402
    PH_SAFE_MAX,
    PH_SAFE_MIN,
    TDS_SAFE_MAX,
    TURBIDITY_SAFE_MAX,
    rule_based_fusion,
)
import db  # noqa: E402

app = Flask(__name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
db.init_db()

# In-memory "latest" snapshot only (history now lives in SQLite, see db.py)
latest_data = {
    "tds": 0,
    "turbidity": 0,
    "temperature": 0,
    "ph": None,
    "water_level_distance_cm": None,
    "source": "waiting...",
    "timestamp": None,
}
latest_verdict = None

CAMERA_LATEST_FILE = os.path.join(UPLOAD_FOLDER, "latest_camera.jpg")
camera_status = {"available": False, "timestamp": None, "device_id": None}

# Set to True only if you want fake data for testing the UI without
# hardware connected. Set to False (default) to wait for real ESP32
# readings only.
USE_DUMMY_DATA = False

DEFAULT_CONFIDENCE_THRESHOLD = 0.4

# ---------- Email alert config (see module docstring) ----------
# SMTP server credentials still come from the .env file / real env vars -
# those are secrets and don't belong in the UI or the database.
ALERT_EMAIL_ENABLED = os.environ.get("ALERT_EMAIL_ENABLED", "false").lower() == "true"
ALERT_SMTP_HOST = os.environ.get("ALERT_SMTP_HOST", "")
ALERT_SMTP_PORT = int(os.environ.get("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_USER = os.environ.get("ALERT_SMTP_USER", "")
ALERT_SMTP_PASSWORD = os.environ.get("ALERT_SMTP_PASSWORD", "")

# The actual *recipient list*, on the other hand, is edited from the
# dashboard UI (Alert Settings panel) and persisted in the DB, so it
# can be changed without touching the server config. ALERT_EMAIL_TO from
# the .env file is only used to seed the list the very first time the
# app runs (so existing setups keep working with zero changes).
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_alert_recipients():
    raw = db.get_setting("alert_recipients")
    if raw is None:
        # First run: seed from the old single-address env var, if set.
        seed = [e.strip() for e in os.environ.get("ALERT_EMAIL_TO", "").split(",") if e.strip()]
        db.set_setting("alert_recipients", json.dumps(seed))
        return seed
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def set_alert_recipients(emails):
    cleaned, seen = [], set()
    for e in emails:
        e = (e or "").strip()
        if e and e.lower() not in seen and EMAIL_RE.match(e):
            cleaned.append(e)
            seen.add(e.lower())
    db.set_setting("alert_recipients", json.dumps(cleaned))
    return cleaned


def _fmt_reading(value, unit, decimals=1):
    if value is None:
        return "—"
    return f"{value:.{decimals}f} {unit}"


def _build_alert_email(result):
    """Builds a MIMEMultipart: plain-text fallback + styled HTML version.
    Inline CSS + table layout on purpose - that is what renders reliably
    in Gmail/Outlook. Contains: status banner, readings vs safe limits,
    issues, the priority action and the full recommendation plan."""
    verdict = result.get("verdict", "Contaminated")
    issues = result.get("issues", []) or []
    recommendations = result.get("recommendations", []) or []
    plan = result.get("recommendation_plan", []) or []
    advice = result.get("overall_advice", "") or ""
    priority_action = result.get("priority_action") or ""
    readings = result.get("sensor_readings", {}) or {}
    cv_dets = (result.get("cv_result") or {}).get("detections", []) or []
    timestamp = time.strftime("%d %b %Y, %I:%M %p")
    e = html.escape

    palette = {
        "Safe":            {"bg": "#2F855A", "soft": "#E6F4EC", "label": "SAFE"},
        "Needs Treatment": {"bg": "#B7791F", "soft": "#FBF1DC", "label": "NEEDS TREATMENT"},
        "Contaminated":    {"bg": "#C0392B", "soft": "#FBE9E7", "label": "CONTAMINATED"},
    }
    pal = palette.get(verdict, palette["Contaminated"])
    sev_color = {"Critical": "#C0392B", "Moderate": "#B7791F", "Mild": "#2F855A"}

    def _status(value, lo=None, hi=None):
        if value is None:
            return ("N/A", "#64748B")
        if (hi is not None and value > hi) or (lo is not None and value < lo):
            return ("OUT OF RANGE", "#C0392B")
        return ("OK", "#2F855A")

    ph_val = readings.get("ph")
    rows = [
        ("TDS", _fmt_reading(readings.get("tds"), "ppm", 0), f"\u2264 {TDS_SAFE_MAX} ppm",
         _status(readings.get("tds"), hi=TDS_SAFE_MAX)),
        ("Turbidity", _fmt_reading(readings.get("turbidity"), "NTU"), f"\u2264 {TURBIDITY_SAFE_MAX} NTU",
         _status(readings.get("turbidity"), hi=TURBIDITY_SAFE_MAX)),
        ("pH", _fmt_reading(ph_val, "", 1).strip(), f"{PH_SAFE_MIN} \u2013 {PH_SAFE_MAX}",
         _status(ph_val, lo=PH_SAFE_MIN, hi=PH_SAFE_MAX)),
        ("Temperature", _fmt_reading(readings.get("temperature"), "\u00b0C"), "reference only",
         ("\u2014", "#64748B")),
    ]

    # ---------- plain-text fallback ----------
    t = [
        f"AQUASCAN WATER QUALITY ALERT - {pal['label']}",
        f"Time: {timestamp}",
        "",
        "SENSOR READINGS",
    ]
    for label, value, safe, (status, _c) in rows:
        t.append(f"  {label:<12} {value:<12} (safe: {safe})  [{status}]")
    if cv_dets:
        t += ["", "VISION MODEL: " + ", ".join(f"{d['class']} ({d['confidence']:.0%})" for d in cv_dets)]
    if priority_action:
        t += ["", "HIGHEST PRIORITY ACTION", f"  {priority_action}"]
    t += ["", "ISSUES DETECTED"] + ([f"  - {i}" for i in issues] or ["  - None"])
    t += ["", "RECOMMENDED ACTIONS"]
    if plan:
        for n, item in enumerate(plan, 1):
            t += [
                f"  {n}. {item['parameter']} - {item['severity']} ({item['urgency']})",
                f"     Reading: {item['reading']} (safe: {item['safe_range']})",
                f"     Immediate action: {item['immediate_action']}",
                "     Treatment:",
            ] + [f"       - {step}" for step in item.get("treatment_steps", [])] + [
                f"     Long-term: {item['long_term']}",
                f"     Retest: {item['retest_interval']}",
                "",
            ]
    else:
        t += [f"  - {r}" for r in recommendations] or ["  - None"]
    if advice:
        t += ["", f"Summary: {advice}"]
    t += ["", "-- Automated alert from AquaScan. Screening only; confirm with a lab test before use."]
    text_body = "\n".join(t)

    # ---------- HTML pieces ----------
    cell = "padding:10px 12px;border-top:1px solid #E2E8F0;"
    reading_rows_html = "".join(
        f'<tr>'
        f'<td style="{cell}font-size:13px;color:#0F172A;font-weight:600;">{e(label)}</td>'
        f'<td style="{cell}font-size:13px;color:#0F172A;font-family:Consolas,Menlo,monospace;">{e(value)}</td>'
        f'<td style="{cell}font-size:12px;color:#64748B;">{e(safe)}</td>'
        f'<td style="{cell}font-size:11px;font-weight:700;color:{color};text-align:right;">{status}</td>'
        f'</tr>'
        for label, value, safe, (status, color) in rows
    )

    cv_html = ""
    if cv_dets:
        cv_text = ", ".join(f"{e(str(d['class']))} ({d['confidence']:.0%})" for d in cv_dets)
        cv_html = f'<p style="margin:12px 0 0;font-size:13px;color:#334155;"><strong>Vision model:</strong> {cv_text}</p>'

    priority_html = ""
    if priority_action:
        priority_html = (
            '<tr><td style="padding:20px 28px 0;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{pal["soft"]};border-left:4px solid {pal["bg"]};border-radius:6px;">'
            '<tr><td style="padding:14px 16px;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{pal["bg"]};margin-bottom:4px;">Highest priority action</div>'
            f'<div style="font-size:14px;color:#0F172A;line-height:1.55;">{e(priority_action)}</div>'
            '</td></tr></table></td></tr>'
        )

    issues_html = "".join(
        f'<tr><td style="padding:4px 0;font-size:13.5px;color:#0F172A;line-height:1.5;">&#8226;&nbsp; {e(i)}</td></tr>'
        for i in issues
    ) or '<tr><td style="font-size:13px;color:#64748B;">No specific issues logged.</td></tr>'

    def _plan_card(n, item):
        color = sev_color.get(item["severity"], "#64748B")
        steps = "".join(
            f'<li style="margin:0 0 3px;">{e(step)}</li>' for step in item.get("treatment_steps", [])
        )
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;border:1px solid #E2E8F0;border-left:4px solid {color};border-radius:6px;">'
            '<tr><td style="padding:14px 16px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="font-size:14px;font-weight:700;color:#0F172A;">{n}. {e(item["parameter"])}</td>'
            f'<td align="right"><span style="font-size:10.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:{color};border:1px solid {color};border-radius:999px;padding:2px 8px;">{e(item["severity"])} &middot; {e(item["urgency"])}</span></td>'
            '</tr></table>'
            f'<div style="font-size:12px;color:#64748B;margin:4px 0 10px;font-family:Consolas,Menlo,monospace;">Reading {e(item["reading"])} &nbsp;|&nbsp; Safe {e(item["safe_range"])}</div>'
            f'<div style="font-size:13px;color:#0F172A;line-height:1.55;"><strong>Do now:</strong> {e(item["immediate_action"])}</div>'
            '<div style="font-size:13px;color:#0F172A;margin-top:10px;"><strong>Treatment</strong></div>'
            f'<ul style="margin:4px 0 0;padding-left:18px;font-size:13px;color:#334155;line-height:1.5;">{steps}</ul>'
            f'<div style="font-size:13px;color:#0F172A;line-height:1.55;margin-top:10px;"><strong>Long-term:</strong> {e(item["long_term"])}</div>'
            f'<div style="font-size:12px;color:#64748B;margin-top:8px;">&#9201; {e(item["retest_interval"])}</div>'
            '</td></tr></table>'
        )

    if plan:
        plan_html = "".join(_plan_card(n, item) for n, item in enumerate(plan, 1))
    else:
        plan_html = "".join(
            f'<p style="margin:0 0 8px;font-size:13.5px;color:#0F172A;line-height:1.5;">&#10003;&nbsp; {e(r)}</p>'
            for r in recommendations
        ) or '<p style="font-size:13px;color:#64748B;">No specific recommendations.</p>'

    advice_html = ""
    if advice:
        advice_html = (
            '<tr><td style="padding:4px 28px 24px;">'
            f'<p style="margin:0;font-size:13px;color:#475569;line-height:1.55;"><strong>Summary:</strong> {e(advice)}</p>'
            '</td></tr>'
        )

    def _section(title):
        return (f'<div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
                f'color:#64748B;margin:0 0 10px;">{title}</div>')

    n_issues = len(issues)
    plural = "s" if n_issues != 1 else ""
    th = "padding:8px 12px;font-size:11px;color:#64748B;font-weight:700;"

    html_body = f"""\
<html>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;background:#ffffff;border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;">

        <!-- Header -->
        <tr><td style="background:#0F172A;padding:18px 28px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td style="color:#ffffff;font-size:16px;font-weight:700;">AquaScan <span style="font-weight:400;color:#94A3B8;font-size:12px;">&nbsp;Water Quality Monitoring</span></td>
            <td align="right" style="color:#94A3B8;font-size:12px;">{e(timestamp)}</td>
          </tr></table>
        </td></tr>

        <!-- Status banner -->
        <tr><td style="background:{pal['bg']};padding:18px 28px;">
          <div style="font-size:11px;font-weight:700;letter-spacing:.1em;color:#ffffff;">WATER QUALITY ALERT</div>
          <div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:2px;">{pal['label']}</div>
          <div style="font-size:13px;color:#ffffff;margin-top:4px;">{n_issues} issue{plural} detected in the latest sample</div>
        </td></tr>

        <!-- Readings -->
        <tr><td style="padding:24px 28px 0;">
          {_section('Sensor readings')}
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E2E8F0;border-radius:6px;">
            <tr style="background:#F8FAFC;">
              <td style="{th}">PARAMETER</td>
              <td style="{th}">READING</td>
              <td style="{th}">SAFE LIMIT</td>
              <td style="{th}text-align:right;">STATUS</td>
            </tr>
            {reading_rows_html}
          </table>
          {cv_html}
        </td></tr>

        {priority_html}

        <!-- Issues -->
        <tr><td style="padding:24px 28px 0;">
          {_section('Issues detected')}
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{issues_html}</table>
        </td></tr>

        <!-- Recommendations -->
        <tr><td style="padding:24px 28px 12px;">
          {_section('Recommended actions')}
          {plan_html}
        </td></tr>

        {advice_html}

        <!-- Footer -->
        <tr><td style="padding:16px 28px;background:#F8FAFC;border-top:1px solid #E2E8F0;">
          <p style="margin:0;font-size:11.5px;color:#94A3B8;line-height:1.5;">Automated alert from your AquaScan dashboard. Results come from sensor and camera screening; confirm with a laboratory test before using or releasing this water. Manage recipients from the dashboard's Alert recipients panel.</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"[AquaScan] {pal['label']} \u2013 {n_issues} issue{plural} detected "
        f"\u2013 action required ({time.strftime('%d %b %Y %H:%M')})"
    )
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def _try_send(host, port, use_ssl, user, password, recipients, msg):
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())


def send_alert_email(result):
    """Best-effort email alert on a Contaminated verdict. Silently no-ops
    (just logs) if not configured or if sending fails - a bad SMTP config
    should never break the actual analysis request.

    Tries STARTTLS on port 587 first, falls back to SSL on port 465 if
    that fails - some networks/ISPs block 587 but allow 465 (or vice
    versa), and a "timed out" error usually means a blocked port, not a
    wrong password."""
    if not ALERT_EMAIL_ENABLED:
        return
    recipients = get_alert_recipients()
    if not (ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASSWORD and recipients):
        print("[alert] ALERT_EMAIL_ENABLED is true but SMTP settings/recipients are incomplete - skipping.")
        return

    msg = _build_alert_email(result)
    msg["From"] = ALERT_SMTP_USER
    msg["To"] = ", ".join(recipients)

    attempts = [(ALERT_SMTP_PORT, False)]
    if ALERT_SMTP_PORT != 465:
        attempts.append((465, True))  # SSL fallback, in case 587/STARTTLS is blocked

    last_error = None
    for port, use_ssl in attempts:
        try:
            _try_send(ALERT_SMTP_HOST, port, use_ssl, ALERT_SMTP_USER, ALERT_SMTP_PASSWORD, recipients, msg)
            print(f"[alert] Email sent to {len(recipients)} recipient(s) via port {port}.")
            return
        except Exception as e:  # noqa: BLE001 - alerting must never crash the request
            last_error = e
            print(f"[alert] Send via port {port} failed: {e}")

    print(f"[alert] Failed to send email after all attempts. Last error: {last_error}")


def dummy_data_loop():
    """Generates plausible fake sensor readings every 5s while
    USE_DUMMY_DATA is toggled on. Runs continuously in the background;
    it just no-ops (sleeps) while the flag is off."""
    global latest_data
    while True:
        if USE_DUMMY_DATA:
            latest_data = {
                "tds": round(random.uniform(150, 700), 1),
                "turbidity": round(random.uniform(0, 12), 1),
                "temperature": round(random.uniform(20, 32), 1),
                "ph": round(random.uniform(5.5, 9.0), 2),
                "source": "dummy (simulated)",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            db.insert_reading(latest_data)
        time.sleep(5)


# Start the background thread unconditionally, at import time (not just
# inside `if __name__ == "__main__"`). This matters because gunicorn
# imports this module directly and never runs that block - without this,
# the dummy generator would never start in production even after the
# toggle button switches USE_DUMMY_DATA to True.
threading.Thread(target=dummy_data_loop, daemon=True).start()


@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/sensor-data", methods=["POST"])
def receive_sensor_data():
    """Real ESP32 posts JSON here: {"tds":.., "turbidity":.., "temperature":..}"""
    global latest_data, USE_DUMMY_DATA
    payload = request.get_json(force=True)

    USE_DUMMY_DATA = False  # real hardware has spoken, stop faking it
    latest_data = {
        "tds": payload.get("tds"),
        "turbidity": payload.get("turbidity"),
        "temperature": payload.get("temperature"),
        "ph": payload.get("ph"),
        "water_level_distance_cm": payload.get("water_level_distance_cm"),
        "source": "ESP32 (live)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    db.insert_reading(latest_data)
    return jsonify({"status": "ok"})


@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(db.get_readings(50))


@app.route("/api/verdict-history", methods=["GET"])
def get_verdict_history():
    """Most recent test first."""
    return jsonify(db.get_verdicts(20))


@app.route("/api/export-readings-csv", methods=["GET"])
def export_readings_csv():
    readings = db.get_readings(1000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "tds_ppm", "turbidity_ntu", "ph", "temperature_c", "source"])
    for r in readings:
        writer.writerow([r["timestamp"], r["tds"], r["turbidity"], r["ph"], r["temperature"], r["source"]])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=aquascan_readings.csv"},
    )


@app.route("/api/export-verdicts-csv", methods=["GET"])
def export_verdicts_csv():
    verdicts = db.get_verdicts(1000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "verdict", "issues", "recommendations", "overall_advice"])
    for v in verdicts:
        writer.writerow([
            v["timestamp"], v["verdict"],
            " | ".join(v["issues"]),
            " | ".join(v["recommendations"]),
            v["overall_advice"],
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=aquascan_test_history.csv"},
    )


@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/toggle-dummy", methods=["POST"])
def toggle_dummy():
    """Flips the dummy-data generator on/off. Used by the dashboard's
    toggle button so you can demo the UI without real ESP32 hardware."""
    global USE_DUMMY_DATA
    USE_DUMMY_DATA = not USE_DUMMY_DATA
    if not USE_DUMMY_DATA:
        latest_data["source"] = "waiting..."
    return jsonify({"dummy_active": USE_DUMMY_DATA})


@app.route("/api/alert-settings", methods=["GET"])
def get_alert_settings():
    return jsonify({
        "enabled": ALERT_EMAIL_ENABLED,
        "recipients": get_alert_recipients(),
        # so the UI can explain *why* alerts won't fire even with recipients set
        "smtp_configured": bool(ALERT_SMTP_HOST and ALERT_SMTP_USER and ALERT_SMTP_PASSWORD),
    })


@app.route("/api/alert-settings", methods=["POST"])
def update_alert_settings():
    """Body: {"recipients": ["a@x.com", "b@y.com", ...]}"""
    payload = request.get_json(force=True) or {}
    emails = payload.get("recipients", [])
    if not isinstance(emails, list):
        return jsonify({"error": "recipients must be a list of email addresses"}), 400

    invalid = [e for e in emails if e and e.strip() and not EMAIL_RE.match(e.strip())]
    saved = set_alert_recipients(emails)

    response = {"recipients": saved}
    if invalid:
        response["skipped_invalid"] = invalid
    return jsonify(response)


@app.route("/api/latest", methods=["GET"])
def get_latest():
    return jsonify({
        "sensors": latest_data,
        "verdict": latest_verdict,
        "dummy_active": USE_DUMMY_DATA,
        "camera": {
            **camera_status,
            "url": "/api/camera/latest.jpg" if camera_status["available"] else None,
        },
    })


@app.route("/api/camera-frame", methods=["POST"])
def receive_camera_frame():
    """Receive a JPEG frame directly from the ESP32-CAM."""
    global camera_status

    data = request.get_data(cache=False)
    if not data:
        return jsonify({"error": "Empty camera frame"}), 400
    if len(data) > 3 * 1024 * 1024:
        return jsonify({"error": "JPEG frame is larger than 3 MB"}), 413

    content_type = (request.headers.get("Content-Type") or "").lower()
    if "image/jpeg" not in content_type:
        return jsonify({"error": "Expected image/jpeg"}), 415

    with open(CAMERA_LATEST_FILE, "wb") as f:
        f.write(data)

    camera_status = {
        "available": True,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device_id": request.headers.get("X-Device-ID", "aquascan-cam-01"),
    }
    return jsonify({"status": "ok", "bytes": len(data), "timestamp": camera_status["timestamp"]})


@app.route("/api/camera/latest.jpg", methods=["GET"])
def get_latest_camera():
    if not os.path.exists(CAMERA_LATEST_FILE):
        return jsonify({"error": "No ESP32-CAM frame received yet"}), 404
    return send_from_directory(UPLOAD_FOLDER, "latest_camera.jpg", mimetype="image/jpeg", max_age=0)


@app.route("/api/analyze-camera", methods=["POST"])
def analyze_camera():
    """Run the same CV + sensor fusion pipeline on the latest ESP32-CAM JPEG."""
    global latest_verdict

    if not os.path.exists(CAMERA_LATEST_FILE):
        return jsonify({"error": "No ESP32-CAM frame received yet"}), 404

    try:
        confidence_threshold = float(request.form.get("confidence", DEFAULT_CONFIDENCE_THRESHOLD))
        confidence_threshold = max(0.05, min(0.95, confidence_threshold))
    except (TypeError, ValueError):
        confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD

    save_path = os.path.join(UPLOAD_FOLDER, "latest_sample.jpg")
    with open(CAMERA_LATEST_FILE, "rb") as src, open(save_path, "wb") as dst:
        dst.write(src.read())

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    history_filename = f"test_{int(time.time() * 1000)}.jpg"
    history_path = os.path.join(UPLOAD_FOLDER, history_filename)
    with open(save_path, "rb") as src, open(history_path, "wb") as dst:
        dst.write(src.read())

    try:
        from cv_model.predict_yolo import predict_image
        cv_result = predict_image(save_path, confidence_threshold=confidence_threshold)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400

    result = rule_based_fusion(
        tds=latest_data["tds"] or 0,
        turbidity=latest_data["turbidity"] or 0,
        temperature=latest_data["temperature"] or 0,
        cv_result=cv_result,
        ph=latest_data.get("ph"),
    )
    result["image_url"] = "/uploads/latest_sample.jpg"
    result["camera_source"] = True
    result["confidence_threshold_used"] = confidence_threshold
    latest_verdict = result

    history_entry = dict(result)
    history_entry["image_url"] = f"/uploads/{history_filename}"
    history_entry["timestamp"] = timestamp
    db.insert_verdict(history_entry)

    if result["verdict"] == "Contaminated":
        threading.Thread(target=send_alert_email, args=(result,), daemon=True).start()

    return jsonify(result)


@app.route("/api/analyze-image", methods=["POST"])
def analyze_image():
    """Upload a water sample image -> runs CV model + fuses with the
    latest sensor reading -> returns final verdict.
    Optional form field "confidence": CV detection threshold (0-1),
    defaults to DEFAULT_CONFIDENCE_THRESHOLD if not sent or invalid."""
    global latest_verdict

    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    try:
        confidence_threshold = float(request.form.get("confidence", DEFAULT_CONFIDENCE_THRESHOLD))
        confidence_threshold = max(0.05, min(0.95, confidence_threshold))
    except (TypeError, ValueError):
        confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD

    image_file = request.files["image"]

    # Keep "latest_sample.jpg" for backward compat (webcam preview etc),
    # but also save a uniquely-named copy so past tests aren't overwritten
    # and can be shown in the history panel.
    save_path = os.path.join(UPLOAD_FOLDER, "latest_sample.jpg")
    image_file.save(save_path)

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    history_filename = f"test_{int(time.time() * 1000)}.jpg"
    history_path = os.path.join(UPLOAD_FOLDER, history_filename)
    with open(save_path, "rb") as src, open(history_path, "wb") as dst:
        dst.write(src.read())

    try:
        from cv_model.predict_yolo import predict_image
        cv_result = predict_image(save_path, confidence_threshold=confidence_threshold)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400

    result = rule_based_fusion(
        tds=latest_data["tds"] or 0,
        turbidity=latest_data["turbidity"] or 0,
        temperature=latest_data["temperature"] or 0,
        cv_result=cv_result,
        ph=latest_data.get("ph"),
    )
    result["image_url"] = "/uploads/latest_sample.jpg"
    result["confidence_threshold_used"] = confidence_threshold
    latest_verdict = result

    # Persist a copy for the history panel (own image_url, doesn't get
    # overwritten by the next test like latest_sample.jpg does)
    history_entry = dict(result)
    history_entry["image_url"] = f"/uploads/{history_filename}"
    history_entry["timestamp"] = timestamp
    db.insert_verdict(history_entry)

    if result["verdict"] == "Contaminated":
        threading.Thread(target=send_alert_email, args=(result,), daemon=True).start()

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
