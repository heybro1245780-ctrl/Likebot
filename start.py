import subprocess
import sys
import os
import time

PORT          = os.getenv("PORT", "5000")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

BOT_RESTART_DELAY = 15   # seconds to wait before restarting bot after crash
MAX_BOT_RESTARTS  = 10   # give up after this many crashes (keeps Flask alive)


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
    print(f"[start.py] Flask started (PID {flask_proc.pid}) on port {PORT}", flush=True)

    # ── Short pause so Flask is ready before bot starts ───────────────────────
    time.sleep(3)

    # ── No token: keep Flask alive indefinitely ───────────────────────────────
    if not DISCORD_TOKEN:
        print("[start.py] WARNING: DISCORD_TOKEN not set — bot will not start.", flush=True)
        print("[start.py] Set DISCORD_TOKEN in Render > Environment tab, then redeploy.", flush=True)
        try:
            flask_proc.wait()
        except KeyboardInterrupt:
            flask_proc.terminate()
        sys.exit(0)

    # ── Bot restart loop (Flask stays alive regardless) ───────────────────────
    crash_count = 0
    while crash_count < MAX_BOT_RESTARTS:
        print(f"[start.py] Starting Discord bot (attempt {crash_count + 1})...", flush=True)
        try:
            result = subprocess.run([sys.executable, "bot.py"])
            exit_code = result.returncode
        except KeyboardInterrupt:
            print("[start.py] Interrupted — shutting down.", flush=True)
            break
        except Exception as e:
            print(f"[start.py] Bot launch error: {e}", flush=True)
            exit_code = 1

        crash_count += 1

        # 401 Unauthorized = bad token, no point retrying
        if exit_code == 0:
            print("[start.py] Bot exited cleanly.", flush=True)
            break

        print(f"[start.py] Bot crashed (exit {exit_code}). Restart {crash_count}/{MAX_BOT_RESTARTS} in {BOT_RESTART_DELAY}s...", flush=True)
        time.sleep(BOT_RESTART_DELAY)

    if crash_count >= MAX_BOT_RESTARTS:
        print(f"[start.py] Bot crashed {MAX_BOT_RESTARTS} times. Giving up — Flask still running.", flush=True)

    # ── Keep Flask alive after bot gives up ───────────────────────────────────
    print("[start.py] Keeping Flask alive (bot stopped).", flush=True)
    try:
        flask_proc.wait()
    except KeyboardInterrupt:
        flask_proc.terminate()
