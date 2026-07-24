#!/usr/bin/env python3
"""One-command launcher for the ThesisWatch web app.

Starts the FastAPI engine bridge (port 8000) and the React/Vite dev server
(port 5173), then opens the browser. The React app talks to the API through
Vite's dev proxy, so you only need to open http://localhost:5173.

Usage:
    python run_web.py            # offline demo data (no network)
    python run_web.py --live     # allow live market data / news fetches

The first run installs the frontend dependencies with `npm install`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
API_PORT = 8000
WEB_PORT = 5173


def _npm() -> str:
    exe = shutil.which("npm") or shutil.which("npm.cmd")
    if not exe:
        sys.exit("npm not found. Install Node.js 18+ from https://nodejs.org and retry.")
    return exe


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the ThesisWatch web app.")
    ap.add_argument("--live", action="store_true", help="Allow live data (default: offline demo).")
    args = ap.parse_args()

    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    if not args.live:
        env["ALPHAWATCH_OFFLINE"] = "1"

    npm = _npm()
    if not (APP / "node_modules").exists():
        print("[thesiswatch] Installing frontend dependencies (first run)...")
        subprocess.run([npm, "install"], cwd=APP, env=env, check=True)

    procs: list[subprocess.Popen] = []
    try:
        print(f"[thesiswatch] Starting API on http://localhost:{API_PORT} ...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api_server:app", "--port", str(API_PORT)],
            cwd=ROOT, env=env,
        ))
        print(f"[thesiswatch] Starting web UI on http://localhost:{WEB_PORT} ...")
        procs.append(subprocess.Popen(
            [npm, "run", "dev", "--", "--port", str(WEB_PORT)],
            cwd=APP, env=env,
        ))
        time.sleep(4)
        webbrowser.open(f"http://localhost:{WEB_PORT}")
        print("[thesiswatch] Running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\n[thesiswatch] Shutting down...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
