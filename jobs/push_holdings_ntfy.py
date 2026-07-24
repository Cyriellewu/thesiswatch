"""One-shot: push watchlist snapshot to ntfy (after editing config/watchlist.yaml)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from push.notify import send_holdings_sync_ntfy  # noqa: E402

if __name__ == "__main__":
    ok = send_holdings_sync_ntfy()
    print("AlphaWatch: holdings ntfy", "sent" if ok else "FAILED (see logs / .env NTFY_TOPIC)")
