import subprocess
import sys
import os
import threading

PORT = os.getenv("PORT", "5000")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

def run_bot():
    if not DISCORD_TOKEN:
        print("[start.py] WARNING: DISCORD_TOKEN not set — bot will not start.")
        print("[start.py] Add DISCORD_TOKEN in Render dashboard > Environment tab.")
        return
    print("[start.py] Starting Discord bot...")
    subprocess.run([sys.executable, "bot.py"])

if __name__ == "__main__":
    # Bot runs in background thread (won't crash Flask if it fails)
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    print(f"[start.py] Starting Flask API on port {PORT}...")

    # Flask runs in main thread — Render health check requires this
    os.execv(sys.executable, [
        sys.executable, "-m", "gunicorn",
        "app:app",
        "--bind", f"0.0.0.0:{PORT}",
        "--workers", "1",
        "--threads", "4",
        "--timeout", "120",
        "--log-level", "info"
    ])
