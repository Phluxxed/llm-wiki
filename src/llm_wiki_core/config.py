from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


CURRENT_SCHEMA_VERSION = "1"
CURRENT_RUNTIME_CONTRACT = "2"
CONFIG_FILENAME = ".llm-wiki.toml"

DEFAULT_EXCLUDES = (".git", ".venv", "node_modules")
LEGACY_DEFAULT_PROVIDERS = ("seed", "frontmatter", "text", "graph", "source")
DEFAULT_PROVIDERS = (*LEGACY_DEFAULT_PROVIDERS, "loci")
KNOWN_PROVIDERS = set(DEFAULT_PROVIDERS)
GRAPH_BACKENDS = {"loci", "legacy"}
KNOWN_STATES = {
    "current",
    "historical",
    "superseded",
    "contradicted",
    "weak",
    "inferred",
    "unspecified",
}


@dataclass(frozen=True)
class ConfigError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContentConfig:
    exclude_directories: tuple[str, ...] = DEFAULT_EXCLUDES
    source_directory: str = "sources"


@dataclass(frozen=True)
class CompilerConfig:
    providers: tuple[str, ...] = DEFAULT_PROVIDERS
    graph_backend: str = "loci"
    target_bytes: int = 48_000
    max_bytes: int = 192_000
    target_items: int = 24
    max_items: int = 96


@dataclass(frozen=True)
class StateConfig:
    field: str = "knowledge_state"
    default: str = "unspecified"


@dataclass(frozen=True)
class StewardshipConfig:
    mode: str = "manual"


@dataclass(frozen=True)
class WikiConfig:
    schema_version: str
    runtime_contract: str
    profile: str
    content: ContentConfig
    compiler: CompilerConfig
    state: StateConfig
    stewardship: StewardshipConfig
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ConfigInspection:
    status: str
    config: WikiConfig | None = None
    error: ConfigError | None = None


class _InvalidConfig(ValueError):
    def __init__(self, message: str, *, key: str | None = None, value: Any = None):
        super().__init__(message)
        self.key = key
        self.value = value


def inspect_wiki_config(wiki_root: str | Path) -> ConfigInspection:
    root = Path(wiki_root).expanduser().resolve()
    path = root / CONFIG_FILENAME
    if not path.is_file():
        return ConfigInspection(status="legacy_missing")

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return _invalid_result(f"Could not parse {CONFIG_FILENAME}", reason=type(exc).__name__)

    try:
        _reject_sensitive_keys(raw)
        schema_version = _required_string(raw, "schema_version")
        runtime_contract = _required_string(raw, "runtime_contract")
        profile = _optional_string(raw, "profile", "default")
    except _InvalidConfig as exc:
        return _invalid_from_exception(exc)

    if schema_version != CURRENT_SCHEMA_VERSION:
        return ConfigInspection(
            status="unsupported_schema",
            error=ConfigError(
                "SCHEMA_VERSION_UNSUPPORTED",
                "Wiki configuration schema is not supported",
                {"found": schema_version, "supported": CURRENT_SCHEMA_VERSION},
            ),
        )
    if runtime_contract != CURRENT_RUNTIME_CONTRACT:
        return ConfigInspection(
            status="runtime_incompatible",
            error=ConfigError(
                "RUNTIME_CONTRACT_INCOMPATIBLE",
                "Wiki requires a different llm-wiki runtime contract",
                {"found": runtime_contract, "supported": CURRENT_RUNTIME_CONTRACT},
            ),
        )

    try:
        config = WikiConfig(
            schema_version=schema_version,
            runtime_contract=runtime_contract,
            profile=profile,
            content=_content_config(_section(raw, "content")),
            compiler=_compiler_config(_section(raw, "compiler")),
            state=_state_config(_section(raw, "state")),
            stewardship=_stewardship_config(_section(raw, "stewardship")),
            raw=raw,
        )
    except _InvalidConfig as exc:
        return _invalid_from_exception(exc)
    return ConfigInspection(status="compatible", config=config)


def _content_config(raw: Mapping[str, Any]) -> ContentConfig:
    excludes = _string_list(raw, "exclude_directories", DEFAULT_EXCLUDES, prefix="content")
    source_directory = _optional_string(raw, "source_directory", "sources", prefix="content")
    for index, value in enumerate(excludes):
        _require_relative_path(value, f"content.exclude_directories[{index}]")
    _require_relative_path(source_directory, "content.source_directory")
    return ContentConfig(exclude_directories=excludes, source_directory=source_directory)


def _compiler_config(raw: Mapping[str, Any]) -> CompilerConfig:
    providers = _string_list(raw, "providers", DEFAULT_PROVIDERS, prefix="compiler")
    if not providers:
        raise _InvalidConfig("At least one compiler provider is required", key="compiler.providers")
    unknown_providers = sorted(set(providers) - KNOWN_PROVIDERS)
    if unknown_providers:
        raise _InvalidConfig(
            "Unknown compiler providers are not supported",
            key="compiler.providers",
            value=unknown_providers,
        )
    graph_backend = _optional_string(raw, "graph_backend", "loci", prefix="compiler")
    if graph_backend not in GRAPH_BACKENDS:
        raise _InvalidConfig(
            "Unknown compiler graph backend",
            key="compiler.graph_backend",
            value=graph_backend,
        )
    target_bytes = _positive_int(raw, "target_bytes", 48_000, prefix="compiler")
    max_bytes = _positive_int(raw, "max_bytes", 192_000, prefix="compiler")
    target_items = _positive_int(raw, "target_items", 24, prefix="compiler")
    max_items = _positive_int(raw, "max_items", 96, prefix="compiler")
    if target_bytes > max_bytes:
        raise _InvalidConfig(
            "Compiler target bytes cannot exceed maximum bytes",
            key="compiler.target_bytes",
            value=target_bytes,
        )
    if target_items > max_items:
        raise _InvalidConfig(
            "Compiler target items cannot exceed maximum items",
            key="compiler.target_items",
            value=target_items,
        )
    return CompilerConfig(
        providers=providers,
        graph_backend=graph_backend,
        target_bytes=target_bytes,
        max_bytes=max_bytes,
        target_items=target_items,
        max_items=max_items,
    )


def _state_config(raw: Mapping[str, Any]) -> StateConfig:
    field_name = _optional_string(raw, "field", "knowledge_state", prefix="state")
    default = _optional_string(raw, "default", "unspecified", prefix="state")
    if default not in KNOWN_STATES:
        raise _InvalidConfig("Unknown default knowledge state", key="state.default", value=default)
    return StateConfig(field=field_name, default=default)


def _stewardship_config(raw: Mapping[str, Any]) -> StewardshipConfig:
    mode = _optional_string(raw, "mode", "manual", prefix="stewardship")
    if mode != "manual":
        raise _InvalidConfig("Only manual stewardship is supported", key="stewardship.mode", value=mode)
    return StewardshipConfig(mode=mode)


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise _InvalidConfig("Configuration section must be a table", key=key, value=value)
    return value


def _required_string(raw: Mapping[str, Any], key: str, *, prefix: str = "") -> str:
    if key not in raw:
        raise _InvalidConfig("Required configuration key is missing", key=_key(prefix, key))
    return _string_value(raw[key], _key(prefix, key))


def _optional_string(raw: Mapping[str, Any], key: str, default: str, *, prefix: str = "") -> str:
    if key not in raw:
        return default
    return _string_value(raw[key], _key(prefix, key))


def _string_value(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidConfig("Configuration value must be a non-empty string", key=key, value=value)
    return value.strip()


def _string_list(
    raw: Mapping[str, Any],
    key: str,
    default: tuple[str, ...],
    *,
    prefix: str,
) -> tuple[str, ...]:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise _InvalidConfig("Configuration value must be a list of strings", key=_key(prefix, key), value=value)
    return tuple(item.strip() for item in value)


def _positive_int(raw: Mapping[str, Any], key: str, default: int, *, prefix: str) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _InvalidConfig("Configuration value must be a positive integer", key=_key(prefix, key), value=value)
    return value


def _require_relative_path(value: str, key: str) -> None:
    path = Path(value).expanduser()
    if path.is_absolute() or value.startswith("~") or ".." in path.parts:
        raise _InvalidConfig("Configuration paths must stay relative to the wiki root", key=key, value=value)


def _reject_sensitive_keys(raw: Mapping[str, Any], prefix: str = "") -> None:
    for key, value in raw.items():
        path = _key(prefix, str(key))
        normalized = str(key).lower().replace("-", "_")
        parts = set(normalized.split("_"))
        if parts & {"secret", "password", "token"} or normalized in {
            "api_key",
            "agent_id",
            "registry",
            "registry_path",
            "llm_wiki_home",
        }:
            raise _InvalidConfig("Sensitive or agent-specific configuration is not allowed", key=path)
        if isinstance(value, dict):
            _reject_sensitive_keys(value, path)


def _invalid_from_exception(exc: _InvalidConfig) -> ConfigInspection:
    details: dict[str, Any] = {}
    if exc.key is not None:
        details["key"] = exc.key
    if exc.value is not None:
        details["value"] = exc.value
    return ConfigInspection(
        status="invalid",
        error=ConfigError("WIKI_CONFIG_INVALID", str(exc), details),
    )


def _invalid_result(message: str, **details: Any) -> ConfigInspection:
    return ConfigInspection(
        status="invalid",
        error=ConfigError("WIKI_CONFIG_INVALID", message, details),
    )


def _key(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key
