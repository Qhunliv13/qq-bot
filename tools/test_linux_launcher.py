#!/usr/bin/env python3
"""Run the shared offline launcher tests with stdlib only (including in WSL)."""

from pathlib import Path
import unittest


if __name__ == "__main__":
    # Discovery loads the same fixture as pytest, without requiring pytest in WSL.
    root = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests"), pattern="test_linux_launcher.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
