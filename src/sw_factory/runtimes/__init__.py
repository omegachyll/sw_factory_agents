"""Agent runtimes: the same AgentSpec runs on the Claude Agent SDK or the GitHub Copilot SDK."""

from sw_factory.runtimes.base import AgentResult, AgentRuntime, AgentSpec, RuntimeUnavailable

RUNTIMES = ("claude", "copilot")


def get_runtime(name: str) -> AgentRuntime:
    if name == "claude":
        from sw_factory.runtimes.claude import ClaudeRuntime

        return ClaudeRuntime()
    if name == "copilot":
        from sw_factory.runtimes.copilot import CopilotRuntime

        return CopilotRuntime()
    raise ValueError(f"unknown runtime {name!r}; expected one of {', '.join(RUNTIMES)}")


__all__ = ["RUNTIMES", "AgentResult", "AgentRuntime", "AgentSpec", "RuntimeUnavailable", "get_runtime"]
