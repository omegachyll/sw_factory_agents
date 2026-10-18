"""Runs agents on the GitHub Copilot SDK.

EXPERIMENTAL: written against github-copilot-sdk 1.0.22's signatures and tested with a fake client only. It has not
yet been run end to end against a real Copilot subscription.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from sw_factory.runtimes.base import AgentResult, AgentSpec, RuntimeUnavailable

INSTALL_HINT = "install the extra: uv tool install -e '.[copilot]' (or uv sync --extra copilot)"

# Permission request kinds each access level may approve; everything else (MCP, URLs, memory, ...) is rejected.
ALLOWED_KINDS: dict[str, set[str]] = {
    "none": set(),
    "read": {"read"},
    "write": {"read", "write", "shell"},
}

ClientFactory = Callable[[Path], Any]


def _field(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


class CopilotRuntime:
    name = "copilot"

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory

    def _permission_handler(self, spec: AgentSpec) -> Callable[[Any, Any], Any]:
        from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

        allowed = ALLOWED_KINDS[spec.access]

        def handle(request: Any, invocation: Any) -> Any:
            kind = getattr(request, "kind", None)
            if kind in allowed:
                return PermissionDecisionApproveOnce()
            return PermissionDecisionReject(feedback=f"agent '{spec.name}' has {spec.access} access; {kind} denied")

        return handle

    async def run(self, spec: AgentSpec, prompt: str, cwd: Path) -> AgentResult:
        try:
            from copilot import CopilotClient
            from copilot.session_events import AssistantMessageData, SessionEventType
        except ImportError as e:
            raise RuntimeUnavailable(f"github-copilot-sdk is not installed; {INSTALL_HINT}") from e

        make_client = self._client_factory or (lambda wd: CopilotClient(working_directory=str(wd)))
        start = time.monotonic()
        input_tokens = output_tokens = 0
        saw_usage = False
        errors: list[str] = []

        def on_event(event: Any) -> None:
            nonlocal input_tokens, output_tokens, saw_usage
            if event.type == SessionEventType.ASSISTANT_USAGE:
                saw_usage = True
                input_tokens += event.data.input_tokens or 0
                output_tokens += event.data.output_tokens or 0
            elif event.type == SessionEventType.SESSION_ERROR:
                errors.append(event.data.message)

        try:
            async with make_client(cwd) as client:
                auth = await client.get_auth_status()
                if not _field(auth, "isAuthenticated"):
                    message = _field(auth, "statusMessage") or "not logged in"
                    raise RuntimeUnavailable(f"Copilot is not authenticated: {message}")
                async with await client.create_session(
                    model=spec.model,
                    system_message={"mode": "replace", "content": spec.system_prompt},
                    working_directory=str(cwd),
                    on_permission_request=self._permission_handler(spec),
                ) as session:
                    session.on(on_event)
                    reply = await session.send_and_wait(prompt, timeout=spec.timeout_s)
        except RuntimeUnavailable:
            raise
        except TimeoutError:
            errors.append(f"timed out after {spec.timeout_s:.0f}s")
            reply = None
        except FileNotFoundError as e:
            raise RuntimeUnavailable(f"Copilot CLI could not be started ({e})") from e
        except Exception as e:  # the SDK raises plain Exception for connection/session failures
            if not errors:  # a session.error event usually already explains it; the exception just repeats it
                errors.append(str(e))
            reply = None

        text = reply.data.content if reply is not None and isinstance(reply.data, AssistantMessageData) else ""
        return AgentResult(
            text=text,
            runtime=self.name,
            model=spec.model,
            duration_s=time.monotonic() - start,
            input_tokens=input_tokens if saw_usage else None,
            output_tokens=output_tokens if saw_usage else None,
            cost_usd=None,  # Copilot bills in premium requests, not USD
            error="; ".join(errors) or None,
        )
