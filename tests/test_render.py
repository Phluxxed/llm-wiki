"""Tests for scripts/render.py."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


class RenderModuleTest(unittest.TestCase):
    def test_module_imports(self):
        import render  # noqa: F401


if __name__ == "__main__":
    unittest.main()
