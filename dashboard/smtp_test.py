"""
Standalone SMTP diagnostic - run this directly on your machine to figure
out whether the "timed out" error is a network/firewall block or an
actual login problem.

Usage:
    python smtp_test.py

It reads the same dashboard/.env file the app uses, so run it from
inside the water_quality_project/dashboard folder (or edit DOTENV_PATH
below).
"""

import os
import smtplib
import socket

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    print("(python-dotenv not installed - reading real env vars only)")

HOST = os.environ.get("ALERT_SMTP_HOST", "smtp.gmail.com")
USER = os.environ.get("ALERT_SMTP_USER", "")
PASSWORD = os.environ.get("ALERT_SMTP_PASSWORD", "")

print(f"Testing against host: {HOST}\n")

# Step 1: can we even reach the server on each port? (pure network test,
# no login attempted yet - this is what tells you if it's a firewall block)
for port in (587, 465):
    try:
        s = socket.create_connection((HOST, port), timeout=8)
        print(f"[OK]   TCP connect to {HOST}:{port} succeeded")
        s.close()
    except Exception as e:
        print(f"[FAIL] TCP connect to {HOST}:{port} -> {e}")

print()

# Step 2: try an actual STARTTLS login on 587 (what the app itself does)
if USER and PASSWORD:
    try:
        with smtplib.SMTP(HOST, 587, timeout=10) as server:
            server.starttls()
            server.login(USER, PASSWORD)
        print("[OK]   STARTTLS login on port 587 succeeded")
    except Exception as e:
        print(f"[FAIL] STARTTLS login on port 587 -> {e}")

    # Step 3: try SSL login on 465 as a fallback (some networks/ISPs only
    # allow this one, since 587 is more commonly blocked)
    try:
        with smtplib.SMTP_SSL(HOST, 465, timeout=10) as server:
            server.login(USER, PASSWORD)
        print("[OK]   SSL login on port 465 succeeded")
    except Exception as e:
        print(f"[FAIL] SSL login on port 465 -> {e}")
else:
    print("ALERT_SMTP_USER / ALERT_SMTP_PASSWORD not found in .env - skipping login tests.")
