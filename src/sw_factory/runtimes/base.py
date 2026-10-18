"""The runtime-neutral interface every agent runs through, whichever SDK executes it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

# "read": may read files and search the repo. "write": may also edit files and run shell commands.
Access = Literal["read", "write"]

DEFAULT_TIMEOUT_S = 30 * 60


class RuntimeUnavailable(Exception):
    """The runtime can't run here: its SDK isn't installed, or it isn't logged in or subscribed."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    system_prompt: str
    runtime: str
    model: str | None = None
    access: Access = "read"
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass
class AgentResult:
    text: str
    runtime: str
    model: str | None
    duration_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None  # None when the runtime doesn't report cost
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class AgentRuntime(Protocol):
    name: str

    async def run(self, spec: AgentSpec, prompt: str, cwd: Path) -> AgentResult: ...
