import subprocess
import sys
import os
import time
import signal

PORT          = os.getenv("PORT", "5000")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")


if __name__ == "__main__":

    # ── Start Flask/Gunicorn as a background subprocess ───────────────────────
    flask_proc = subprocess.Popen([
        sys.executable, "-m", "gunicorn",
        "app:app",
        "--bind", f"0.0.0.0:{PORT}",
        "--workers", "1",
        "--threads", "4",
        "--timeout", "120",
        "--log-level", "info"
    ])
    print(f"[start.py] Flask started (PID {flask_proc.pid}) on port {PORT}")

    # ── Short pause so Flask is ready before bot starts ───────────────────────
    time.sleep(3)

    # ── Start Discord bot in main thread (blocking) ───────────────────────────
    if not DISCORD_TOKEN:
        print("[start.py] WARNING: DISCORD_TOKEN not set — bot will not start.")
        print("[start.py] Add DISCORD_TOKEN in Render dashboard > Environment tab.")
        # Keep Flask alive even without the bot
        try:
            flask_proc.wait()
        except KeyboardInterrupt:
            flask_proc.terminate()
    else:
        print("[start.py] Starting Discord bot...")
        try:
            bot_proc = subprocess.run([sys.executable, "bot.py"])
        except KeyboardInterrupt:
            pass
        finally:
            # If bot exits for any reason, also kill Flask
            flask_proc.terminate()

