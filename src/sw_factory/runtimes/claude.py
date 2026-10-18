"""Runs agents on the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from sw_factory.runtimes.base import Access, AgentResult, AgentSpec, RuntimeUnavailable

DEFAULT_MODEL = "claude-opus-5-5"

# Tools are both the only ones available and pre-approved; permission_mode="dontAsk" denies anything else,
# so a headless run never stalls waiting for a permission prompt.
TOOLS: dict[Access, list[str]] = {
    "none": [],
    "read": ["Read", "Glob", "Grep"],
    "write": ["Read", "Glob", "Grep", "Write", "Edit", "Bash"],
}

QueryFn = Callable[..., AsyncIterator[Any]]


class ClaudeRuntime:
    name = "claude"

    def __init__(self, query_fn: QueryFn | None = None) -> None:
        self._query_fn = query_fn

    async def run(self, spec: AgentSpec, prompt: str, cwd: Path) -> AgentResult:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKError,
                CLINotFoundError,
                ResultMessage,
                TextBlock,
                query,
            )
        except ImportError as e:
            raise RuntimeUnavailable(f"claude-agent-sdk is not installed ({e})") from e

        model = spec.model or DEFAULT_MODEL
        options = ClaudeAgentOptions(
            system_prompt=spec.system_prompt,
            model=model,
            cwd=cwd,
            tools=TOOLS[spec.access],
            allowed_tools=TOOLS[spec.access],
            permission_mode="dontAsk",
            setting_sources=[],  # don't pick up the target repo's own Claude Code settings/hooks
            # Load no MCP servers. Without this, every connector on the user's claude.ai account (mail, calendar,
            # brokerage, ...) is attached to each run: ~200K tokens of tool definitions per request, and tools an
            # agent reading untrusted issue text should never see.
            mcp_servers={},
            strict_mcp_config=True,
        )
        query_fn = self._query_fn or query

        start = time.monotonic()
        texts: list[str] = []
        result: Any = None
        try:
            async with asyncio.timeout(spec.timeout_s):
                async for message in query_fn(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                    elif isinstance(message, ResultMessage):
                        result = message
        except CLINotFoundError as e:
            raise RuntimeUnavailable(f"Claude Code CLI not found ({e})") from e
        except (ClaudeSDKError, TimeoutError) as e:
            error = f"timed out after {spec.timeout_s:.0f}s" if isinstance(e, TimeoutError) else str(e)
            return AgentResult(
                text="\n".join(texts), runtime=self.name, model=model, duration_s=time.monotonic() - start,
                error=error,
            )

        duration = time.monotonic() - start
        if result is None:
            return AgentResult(
                text="\n".join(texts), runtime=self.name, model=model, duration_s=duration,
                error="run ended without a result message",
            )
        usage = result.usage or {}
        error = None
        if result.is_error:
            error = "; ".join(result.errors or []) or result.result or result.subtype
        return AgentResult(
            text=result.result if result.result is not None else "\n".join(texts),
            runtime=self.name,
            model=model,
            duration_s=duration,
            # Total prompt size, cached or not, so oversized prompts show up in stats.
            input_tokens=sum(
                usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            ) if usage else None,
            output_tokens=usage.get("output_tokens"),
            cost_usd=result.total_cost_usd,
            error=error,
        )
