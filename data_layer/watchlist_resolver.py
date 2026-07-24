"""Central resolver for the user's private watchlist file.

The real `config/watchlist.yaml` is gitignored and private to each user. This
module guarantees it exists by copying `watchlist.example.yaml` on first use,
so a fresh clone runs out of the box with sample (non-real) holdings, and every
other module can keep reading `config/watchlist.yaml` unchanged.
"""
from __future__ import annotations

import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_REAL = _REPO / "config" / "watchlist.yaml"
_EXAMPLE = _REPO / "config" / "watchlist.example.yaml"


def watchlist_path() -> Path:
    """Return the path to the user's watchlist, seeding it from the example on
    first run. Falls back to the example path if seeding isn't possible."""
    if _REAL.exists():
        return _REAL
    if _EXAMPLE.exists():
        try:
            shutil.copyfile(_EXAMPLE, _REAL)
            return _REAL
        except OSError:
            return _EXAMPLE
    return _REAL
