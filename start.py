import subprocess
import sys
import os
import threading

PORT = os.getenv("PORT", "5000")

def run_flask():
    subprocess.run([
        sys.executable, "-m", "gunicorn",
        "app:app",
        "--bind", f"0.0.0.0:{PORT}",
        "--workers", "1",
        "--threads", "4",
        "--timeout", "120",
        "--log-level", "info"
    ])

def run_bot():
    subprocess.run([sys.executable, "bot.py"])

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"[start.py] Flask API started on port {PORT}")
    
    # Bot runs in main thread (blocking)
    run_bot()
