"""Ensure the repository root is importable during tests.

Lets `import api_models`, `from tasks import ...`, and `import api_server` resolve
regardless of pytest's invocation directory or import mode. Standard, side-effect-free.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
