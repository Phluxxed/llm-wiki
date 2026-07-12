"""Thin compatibility imports for the canonical llm-wiki graph runtime."""

# llm-wiki-adapter runtime_contract=2
try:
    from llm_wiki_core.legacy_graph import *  # noqa: F403
except ImportError as exc:
    raise SystemExit("install or upgrade llm-wiki to use this wiki adapter") from exc
