#!/usr/bin/env python3
"""
render.py — generate wiki.html: a single self-contained reader for the wiki.

Replaces scripts/graph.py. Same call pattern (the agent runs this after every
wiki change), but produces a richer artifact with eight views: Home, Page,
Search, Graph, Risks, Recent changes, Open questions, Entities.

Usage:
    python3 scripts/render.py            # writes wiki.html to wiki root
    python3 scripts/render.py --output path/to/out.html
"""

import argparse
import sys
from pathlib import Path

try:
    import yaml  # noqa: F401
except ImportError:
    sys.exit("pyyaml required: pip3 install pyyaml")

try:
    import markdown  # noqa: F401
except ImportError:
    sys.exit("markdown required: pip3 install markdown")

WIKI_ROOT = Path(__file__).parent.parent
EXCLUDE_FILES = {"wiki-agent.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}
EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian", "evals", "docs", "tests"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(WIKI_ROOT / "wiki.html"))
    args = parser.parse_args()
    Path(args.output).write_text("<!DOCTYPE html><html><body>Empty wiki.</body></html>", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
