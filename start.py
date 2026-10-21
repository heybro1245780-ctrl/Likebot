import subprocess
import sys
import os
import time

PORT          = os.getenv("PORT", "5000")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Backoff config: starts at MIN, doubles each crash, caps at MAX
BACKOFF_MIN = 5    # seconds
BACKOFF_MAX = 60   # seconds
# If bot stays alive longer than this, reset backoff to min
STABLE_THRESHOLD = 30  # seconds


def start_flask():
    proc = subprocess.Popen([
        sys.executable, "-m", "gunicorn",
        "app:app",
        "--bind", f"0.0.0.0:{PORT}",
        "--workers", "1",
        "--threads", "4",
        "--timeout", "120",
        "--log-level", "info"
    ])
    print(f"[start.py] Flask started (PID {proc.pid}) on port {PORT}", flush=True)
    return proc


if __name__ == "__main__":

    # ── Always start Flask first ───────────────────────────────────────────────
    flask_proc = start_flask()
    time.sleep(3)  # let Flask bind before bot tries to hit it

    # ── No token: keep Flask alive, print clear instructions ──────────────────
    if not DISCORD_TOKEN:
        print("[start.py] ERROR: DISCORD_TOKEN is not set!", flush=True)
        print("[start.py] → Go to Render dashboard > Environment > add DISCORD_TOKEN", flush=True)
        print("[start.py] → Get a new token from discord.com/developers/applications", flush=True)
        print("[start.py] Flask is alive (health checks will pass). Bot NOT running.", flush=True)
        try:
            flask_proc.wait()
        except KeyboardInterrupt:
            flask_proc.terminate()
        sys.exit(0)

    # ── Infinite bot restart loop ──────────────────────────────────────────────
    attempt   = 0
    backoff   = BACKOFF_MIN

    while True:
        attempt += 1
        print(f"[start.py] ── Bot start attempt #{attempt} ──────────────────────", flush=True)

        start_time = time.time()
        try:
            result = subprocess.run([sys.executable, "bot.py"])
            exit_code = result.returncode
        except KeyboardInterrupt:
            print("[start.py] KeyboardInterrupt — shutting everything down.", flush=True)
            flask_proc.terminate()
            sys.exit(0)
        except Exception as e:
            print(f"[start.py] Failed to launch bot.py: {e}", flush=True)
            exit_code = 1

        uptime = time.time() - start_time

        # ── Restart decision ──────────────────────────────────────────────────
        if exit_code == 0:
            # Clean exit (Ctrl+C inside bot, or bot.close() called)
            # Still restart — Render should always keep it alive
            print(f"[start.py] Bot exited cleanly after {uptime:.0f}s. Restarting in {BACKOFF_MIN}s...", flush=True)
            backoff = BACKOFF_MIN
            time.sleep(BACKOFF_MIN)
            continue

        # Crashed
        if uptime >= STABLE_THRESHOLD:
            # Ran long enough to be considered stable — reset backoff
            backoff = BACKOFF_MIN
            print(f"[start.py] Bot ran {uptime:.0f}s then crashed (exit {exit_code}). Restarting in {backoff}s...", flush=True)
        else:
            print(f"[start.py] Bot crashed after {uptime:.0f}s (exit {exit_code}). Restarting in {backoff}s...", flush=True)

        # Make sure Flask is still alive; if it died, restart it too
        if flask_proc.poll() is not None:
            print("[start.py] Flask died — restarting Flask too...", flush=True)
            flask_proc = start_flask()

        time.sleep(backoff)

        # Double backoff for next crash (exponential), cap at max
        backoff = min(backoff * 2, BACKOFF_MAX)
