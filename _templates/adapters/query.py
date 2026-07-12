"""Thin compatibility entrypoint for the canonical llm-wiki runtime."""

# llm-wiki-adapter runtime_contract=2
from pathlib import Path

try:
    from llm_wiki_core.legacy_cli import main as _canonical_main
except ImportError as exc:
    raise SystemExit("install or upgrade llm-wiki to use this wiki adapter") from exc


WIKI_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    return _canonical_main(argv=argv, wiki_root=WIKI_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
