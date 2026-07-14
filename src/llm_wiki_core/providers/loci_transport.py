from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import json
import os
import shutil
from threading import Thread
from typing import Any, TypeVar

from anyio import fail_after
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


T = TypeVar("T")


class LociGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class LociMcpClient:
    """Synchronous facade over one bounded local loci MCP session."""

    def __init__(
        self,
        *,
        command: str | None = None,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 15.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._command = command
        self._args = args
        self._timeout_seconds = timeout_seconds

    def run(self, operation: Callable[[ClientSession], Awaitable[T]]) -> T:
        command = self._resolve_command()
        return _run_coroutine(self._run_session(command, operation))

    def _resolve_command(self) -> str:
        configured = self._command or os.environ.get("LLM_WIKI_LOCI_MCP_COMMAND", "loci-mcp")
        resolved = shutil.which(configured)
        if resolved is None:
            raise LociGatewayError(
                "LOCI_MCP_UNAVAILABLE",
                "The core loci MCP traversal service is not available",
                {"transport": "mcp_stdio"},
            )
        return resolved

    async def _run_session(
        self,
        command: str,
        operation: Callable[[ClientSession], Awaitable[T]],
    ) -> T:
        params = StdioServerParameters(
            command=command,
            args=list(self._args),
            env=os.environ.copy(),
        )
        try:
            with fail_after(self._timeout_seconds):
                with open(os.devnull, "w", encoding="utf-8") as errlog:
                    async with stdio_client(params, errlog=errlog) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            return await operation(session)
        except TimeoutError as exc:
            raise LociGatewayError(
                "LOCI_MCP_TIMEOUT",
                "The core loci MCP traversal service timed out",
                {"transport": "mcp_stdio"},
            ) from exc
        except LociGatewayError:
            raise
        except Exception as exc:
            if any(isinstance(item, TimeoutError) for item in _exception_tree(exc)):
                raise LociGatewayError(
                    "LOCI_MCP_TIMEOUT",
                    "The core loci MCP traversal service timed out",
                    {"transport": "mcp_stdio"},
                ) from exc
            structured = _structured_gateway_error(exc)
            if structured is not None:
                raise structured from exc
            raise LociGatewayError(
                "LOCI_MCP_FAILED",
                "The core loci MCP traversal service failed",
                {"type": type(exc).__name__, "transport": "mcp_stdio"},
            ) from exc


def tool_mapping(result: Any) -> Mapping[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if getattr(result, "isError", False):
        error = structured.get("error") if isinstance(structured, Mapping) else None
        if isinstance(error, Mapping):
            raise LociGatewayError(
                str(error.get("code") or "LOCI_MCP_FAILED"),
                str(error.get("message") or "loci MCP tool call failed"),
                error.get("details") if isinstance(error.get("details"), Mapping) else None,
            )
        raise LociGatewayError("LOCI_MCP_FAILED", "loci MCP tool call failed")

    if isinstance(structured, Mapping):
        return structured
    for block in getattr(result, "content", ()):
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return parsed
    raise LociGatewayError(
        "LOCI_RESULT_INVALID",
        "loci MCP response has no object payload",
    )


def tool_payload(result: Any, key: str) -> Any:
    payload = tool_mapping(result)
    if key in payload:
        return payload[key]
    raise LociGatewayError(
        "LOCI_RESULT_INVALID",
        f"loci MCP response has no {key} payload",
    )


def _structured_gateway_error(exc: BaseException) -> LociGatewayError | None:
    for item in _exception_tree(exc):
        if isinstance(item, LociGatewayError):
            return item
        code = getattr(item, "code", None)
        if not isinstance(code, str) or not code:
            continue
        details = getattr(item, "details", None)
        return LociGatewayError(
            code,
            str(item) or code,
            details if isinstance(details, Mapping) else None,
        )
    return None


def _exception_tree(exc: BaseException):
    yield exc
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            yield from _exception_tree(nested)


def _run_coroutine(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as exc:  # propagated in the caller's thread
            failure.append(exc)

    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]
