"""Runtime adapters, exercised against stand-ins built from each SDK's real types (no network, no login)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from sw_factory.runtimes import AgentSpec, RuntimeUnavailable, get_runtime
from sw_factory.runtimes.claude import TOOLS, ClaudeRuntime
from sw_factory.runtimes.copilot import ALLOWED_KINDS, CopilotRuntime, _short_error


def spec(runtime: str, access: str = "read", **kw: Any) -> AgentSpec:
    return AgentSpec(name="t", system_prompt="sys", runtime=runtime, access=access, **kw)  # type: ignore[arg-type]


def test_get_runtime() -> None:
    assert get_runtime("claude").name == "claude"
    assert get_runtime("copilot").name == "copilot"
    with pytest.raises(ValueError):
        get_runtime("nope")


# --- Claude -----------------------------------------------------------------------------------------------


def result_message(**kw: Any) -> ResultMessage:
    base = dict(subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1, session_id="s")
    return ResultMessage(**(base | kw))


async def test_claude_success(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any):
        seen["prompt"], seen["options"] = prompt, options
        yield AssistantMessage(content=[TextBlock(text="working...")], model="claude-opus-5-5")
        usage = {"input_tokens": 20, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 400,
                 "output_tokens": 2}
        yield result_message(result="OK", total_cost_usd=0.012, usage=usage)

    result = await ClaudeRuntime(fake_query).run(spec("claude", "write"), "do it", tmp_path)

    assert result.ok and result.text == "OK"
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (3420, 2, 0.012)
    opts = seen["options"]
    assert seen["prompt"] == "do it"
    assert (opts.system_prompt, opts.cwd, opts.model) == ("sys", tmp_path, "claude-opus-5-5")
    assert opts.tools == opts.allowed_tools == TOOLS["write"]
    assert opts.permission_mode == "dontAsk"
    # No user MCP servers/connectors leak into agent runs.
    assert opts.mcp_servers == {} and opts.strict_mcp_config is True
    assert opts.setting_sources == []


async def test_claude_read_access_has_no_write_tools(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any):
        seen["options"] = options
        yield result_message(result="done")

    await ClaudeRuntime(fake_query).run(spec("claude", "read"), "p", tmp_path)
    assert not {"Write", "Edit", "Bash"} & set(seen["options"].tools)


async def test_claude_error_result(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: Any):
        yield result_message(subtype="error_max_turns", is_error=True, errors=["too many turns"])

    result = await ClaudeRuntime(fake_query).run(spec("claude"), "p", tmp_path)
    assert not result.ok and result.error == "too many turns"


async def test_claude_timeout(tmp_path: Path) -> None:
    import asyncio

    async def fake_query(*, prompt: str, options: Any):
        await asyncio.sleep(5)
        yield result_message()

    result = await ClaudeRuntime(fake_query).run(spec("claude", timeout_s=0.05), "p", tmp_path)
    assert result.error and "timed out" in result.error


# --- Copilot (experimental) -------------------------------------------------------------------------------


def test_copilot_permission_kinds_match_sdk() -> None:
    """Our access levels refer to the SDK's real permission request kinds; fail loudly if the SDK renames them."""
    from copilot.generated import session_events as se

    sdk_kinds = {se.PermissionRequestRead.kind, se.PermissionRequestWrite.kind, se.PermissionRequestShell.kind}
    assert ALLOWED_KINDS["write"] == sdk_kinds
    assert ALLOWED_KINDS["read"] == {se.PermissionRequestRead.kind}


def test_copilot_permission_handler() -> None:
    from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

    read = CopilotRuntime()._permission_handler(spec("copilot", "read"))
    write = CopilotRuntime()._permission_handler(spec("copilot", "write"))
    assert isinstance(read(SimpleNamespace(kind="read"), {}), PermissionDecisionApproveOnce)
    assert isinstance(read(SimpleNamespace(kind="shell"), {}), PermissionDecisionReject)
    assert isinstance(write(SimpleNamespace(kind="shell"), {}), PermissionDecisionApproveOnce)
    assert isinstance(write(SimpleNamespace(kind="url"), {}), PermissionDecisionReject)


class FakeSession:
    def __init__(self, events: list[Any], reply: Any) -> None:
        self.events, self.reply, self.handlers = events, reply, []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    def on(self, handler: Any) -> Any:
        self.handlers.append(handler)
        return lambda: None

    async def send_and_wait(self, prompt: str, *, timeout: float) -> Any:
        self.prompt, self.timeout = prompt, timeout
        for event in self.events:
            for handler in self.handlers:
                handler(event)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class FakeClient:
    def __init__(self, session: FakeSession, authenticated: bool = True) -> None:
        self.session, self.authenticated = session, authenticated

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def get_auth_status(self) -> dict[str, Any]:
        return {"isAuthenticated": self.authenticated, "statusMessage": "no Copilot subscription"}

    async def create_session(self, **kwargs: Any) -> FakeSession:
        self.session_kwargs = kwargs
        return self.session

    async def list_models(self) -> list[Any]:  # stands in for copilot.client.ModelInfo (.id, .name)
        return [SimpleNamespace(id="gpt-x", name="GPT X")]


def usage_event(input_tokens: int, output_tokens: int) -> Any:
    from copilot.session_events import SessionEventType

    return SimpleNamespace(
        type=SessionEventType.ASSISTANT_USAGE,
        data=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def reply_event(text: str) -> Any:
    from copilot.session_events import AssistantMessageData

    return SimpleNamespace(data=AssistantMessageData(content=text, message_id="m1"))


async def test_copilot_success(tmp_path: Path) -> None:
    session = FakeSession([usage_event(30, 3), usage_event(10, 1)], reply_event("OK"))
    client = FakeClient(session)
    result = await CopilotRuntime(lambda cwd: client).run(spec("copilot", model="gpt-x", timeout_s=99), "hi", tmp_path)

    assert result.ok and result.text == "OK"
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (40, 4, None)
    kw = client.session_kwargs
    assert kw["system_message"] == {"mode": "replace", "content": "sys"}
    assert (kw["model"], kw["working_directory"]) == ("gpt-x", str(tmp_path))
    assert (session.prompt, session.timeout) == ("hi", 99)


async def test_copilot_not_authenticated(tmp_path: Path) -> None:
    client = FakeClient(FakeSession([], None), authenticated=False)
    with pytest.raises(RuntimeUnavailable, match="no Copilot subscription"):
        await CopilotRuntime(lambda cwd: client).run(spec("copilot"), "hi", tmp_path)


async def test_copilot_session_error_and_timeout(tmp_path: Path) -> None:
    from copilot.session_events import SessionEventType

    error_event = SimpleNamespace(type=SessionEventType.SESSION_ERROR, data=SimpleNamespace(message="quota exceeded"))
    client = FakeClient(FakeSession([error_event], TimeoutError()))
    result = await CopilotRuntime(lambda cwd: client).run(spec("copilot", timeout_s=5), "hi", tmp_path)
    assert result.error == "quota exceeded; timed out after 5s"


async def test_copilot_exception_after_error_event_is_not_repeated(tmp_path: Path) -> None:
    from copilot.session_events import SessionEventType

    error_event = SimpleNamespace(type=SessionEventType.SESSION_ERROR, data=SimpleNamespace(message="Authorization error"))
    client = FakeClient(FakeSession([error_event], Exception("Session error: Authorization error")))
    result = await CopilotRuntime(lambda cwd: client).run(spec("copilot"), "hi", tmp_path)
    assert result.error == "Authorization error"


async def test_copilot_list_models(tmp_path: Path) -> None:
    client = FakeClient(FakeSession([], None))
    assert await CopilotRuntime(lambda cwd: client).list_models(tmp_path) == [("gpt-x", "GPT X")]

    logged_out = FakeClient(FakeSession([], None), authenticated=False)
    with pytest.raises(RuntimeUnavailable, match="no Copilot subscription"):
        await CopilotRuntime(lambda cwd: logged_out).list_models(tmp_path)


def test_copilot_short_error() -> None:
    raw = ('JSON-RPC Error -32603: Request models.list failed with message: GenericFailure, {"kind":"http",'
           '"status":403,"body":"unauthorized: not authorized to use this Copilot feature\\n","headers":[{"name":"x"}]}')
    assert _short_error(Exception(raw)) == "HTTP 403: unauthorized: not authorized to use this Copilot feature"
    assert _short_error(Exception("plain")) == "plain"
