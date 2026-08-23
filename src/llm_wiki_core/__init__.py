"""Canonical runtime primitives shared by llm-wiki transports and adapters."""

from .config import (
    CURRENT_RUNTIME_CONTRACT,
    CURRENT_SCHEMA_VERSION,
    ConfigError,
    ConfigInspection,
    WikiConfig,
    inspect_wiki_config,
)
from .documents import WikiPage, collect_pages, safe_source_path
from .graph import Edge, collect_typed_edges, resolve_link
from .kernel_projection import (
    KernelProjection,
    KernelProjectionError,
    KernelSource,
    compile_kernel_projection,
)

__all__ = [
    "CURRENT_RUNTIME_CONTRACT",
    "CURRENT_SCHEMA_VERSION",
    "ConfigError",
    "ConfigInspection",
    "WikiConfig",
    "WikiPage",
    "Edge",
    "KernelProjection",
    "KernelProjectionError",
    "KernelSource",
    "collect_pages",
    "collect_typed_edges",
    "compile_kernel_projection",
    "inspect_wiki_config",
    "resolve_link",
    "safe_source_path",
]
