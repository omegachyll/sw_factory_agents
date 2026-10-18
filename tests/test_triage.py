"""Triage agent (`factory triage`): context, decisions, the comment, and publishing, with fake gh and runtime."""

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

import pytest
from typer.testing import CliRunner

from sw_factory.cli import app
from sw_factory.footer import make_footer, parse_footer
from sw_factory.github import Comment, GitHub, GitHubError, IssueData
from sw_factory.runtimes import AgentResult, AgentSpec
from sw_factory.triage import (
    TriageError,
    TriageResult,
    build_prompt,
    dependencies,
    parse_reply,
    previous_triage,
    render_comment,
)

READY = {"decision": "ready", "summary": "Small and clear.", "plan": "### Approach\nDo it.", "questions": []}


def test_footer_round_trip() -> None:
    footer = make_footer(agent="triage", run="r1", factory="abc123", model=None)
    assert footer == "<!-- factory: agent=triage run=r1 factory=abc123 -->"
    assert parse_footer(f"text\n{footer}") == {"agent": "triage", "run": "r1", "factory": "abc123"}
    assert parse_footer("no footer") is None


def test_dependencies() -> None:
    body = "## Notes\nDepends on: #4; #2 and #4.\nSee also #9.\n**Depends on:** #3"
    assert dependencies(body) == [4, 2, 3]  # only Depends-on lines, no duplicates


@pytest.mark.parametrize(
    "change, message",
    [
        ({"decision": "maybe"}, "decision must be one of"),
        ({"plan": ""}, "needs a plan"),
        ({"decision": "needs-human", "questions": []}, "at least one question"),
        ({"summary": " "}, "summary is empty"),
    ],
)
def test_result_validation(change: dict[str, Any], message: str) -> None:
    with pytest.raises(TriageError, match=message):
        TriageResult.from_dict(READY | change)


def test_parse_reply_takes_last_json_block() -> None:
    text = f"Exploring...\n```json\n{{\"x\": 1}}\n```\nFinal:\n```json\n{json.dumps(READY)}\n```"
    assert parse_reply(text).decision == "ready"


def test_render_comment() -> None:
    human = TriageResult("needs-human", "Need the pin.", plan="draft", questions=["Which GPIO pin? (recommend 17)"])
    body = render_comment(human, 4, "claude-opus-5-5", "<!-- factory: agent=triage -->")
    assert body.startswith("**Factory triage: Needs your input** · `claude-opus-5-5`")
    assert "1. Which GPIO pin? (recommend 17)" in body and "## Draft build plan" in body
    assert "factory triage 4" in body and body.endswith("<!-- factory: agent=triage -->")


def test_previous_triage_and_prompt_marks_factory_comments() -> None:
    ours = Comment(1, "me", "plan v1\n<!-- factory: agent=triage run=r1 -->", "t1")
    reply = Comment(2, "me", "Use pin 22, not 17.", "t2")
    assert previous_triage([ours, reply]) == (ours, [reply])
    assert previous_triage([reply]) == (None, [])

    issue = IssueData(4, "Float switch", "## Goal\nRead it", "open", ["factory:new"], comments=[ours, reply])
    prompt = build_prompt(issue, "## TECH.md\n\nstack", [3])
    assert "### factory triage agent (t1)" in prompt and "### me (t2)\n\nUse pin 22" in prompt
    assert "#3 (closed)" in prompt and "## TECH.md" in prompt


# --- CLI ----------------------------------------------------------------------------------------------------


class FakeGh:
    """Issues #1 (closed), #2 (open, depends on #1) and #3 (open, depends on #2)."""

    def __init__(self, comments: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.comments = comments or []
        self.issues = {
            1: {"state": "closed", "body": ""},
            2: {"state": "open", "body": "## Goal\nConfig\n\n## Notes\nDepends on: #1."},
            3: {"state": "open", "body": "Depends on: #2."},
        }

    def __call__(self, args: Sequence[str], cwd: Path | None) -> str:
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["api", "--paginate"]:
            return "\n".join(json.dumps(c) for c in self.comments)
        if args[0] == "api" and args[1].startswith("repos/me/app/issues/") and len(args) == 2:
            number = int(args[1].rsplit("/", 1)[1])
            if number not in self.issues:
                raise GitHubError("gh api failed: Not Found")
            issue = self.issues[number]
            return json.dumps({"number": number, "title": f"Issue {number}", "labels": [{"name": "factory:new"}],
                               **issue})
        if args[0] == "api" and "--jq" in args:
            return "https://github.com/me/app/issues/2#issuecomment-99"
        return ""

    def posts(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "api" and "-f" in c]


class FakeRuntime:
    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies, self.prompts = list(replies), []

    async def run(self, spec: AgentSpec, prompt: str, cwd: Path) -> AgentResult:
        self.prompts.append(prompt)
        return AgentResult(text=self.replies.pop(0), runtime="fake", model=None, duration_s=1,
                           input_tokens=100, output_tokens=10, cost_usd=0.01)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:me/app.git"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init", "--no-labels"])
    (tmp_path / "TECH.md").write_text("# Tech\nPython 3.12")
    return tmp_path


def setup(monkeypatch: pytest.MonkeyPatch, gh: FakeGh, replies: list[str]) -> FakeRuntime:
    runtime = FakeRuntime(replies)
    monkeypatch.setattr("sw_factory.cli.GitHub", lambda: GitHub(gh))
    monkeypatch.setattr("sw_factory.cli.get_runtime", lambda name: runtime)
    return runtime


def test_cli_triage_ready_posts_comment_and_sets_stage(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh()
    runtime = setup(monkeypatch, gh, [f"```json\n{json.dumps(READY)}\n```"])
    result = CliRunner().invoke(app, ["triage", "2", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Factory triage: Ready to build" in result.output and "Posted https://" in result.output
    assert "Python 3.12" in runtime.prompts[0] and "#1 (closed)" in runtime.prompts[0]
    post = gh.posts()[0]
    assert post[1] == "repos/me/app/issues/2/comments"
    assert "agent=triage" in post[post.index("-f") + 1]
    edit = next(c for c in gh.calls if c[:2] == ["issue", "edit"])
    assert edit[edit.index("--add-label") + 1] == "factory:ready"
    assert edit[edit.index("--remove-label") + 1] == "factory:new"


def test_cli_triage_dry_run_posts_nothing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh()
    setup(monkeypatch, gh, [json.dumps(READY)])
    result = CliRunner().invoke(app, ["triage", "2", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "[dry-run] would post this on #2 and set factory:ready" in result.output
    assert gh.posts() == [] and not any(c[:2] == ["issue", "edit"] for c in gh.calls)


def test_cli_triage_blocked_skips_the_agent(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh()
    runtime = setup(monkeypatch, gh, [])
    result = CliRunner().invoke(app, ["triage", "3", "--yes"])
    assert result.exit_code == 0, result.output
    assert runtime.prompts == []
    assert "Factory triage: Blocked" in result.output and "Waiting on #2" in result.output
    edit = next(c for c in gh.calls if c[:2] == ["issue", "edit"])
    assert edit[edit.index("--add-label") + 1] == "factory:blocked"


def test_cli_triage_edits_its_comment_unless_someone_replied(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ours = {"id": 7, "author": "me", "body": "old\n<!-- factory: agent=triage run=r0 -->", "created_at": "t1"}
    gh = FakeGh([ours])
    setup(monkeypatch, gh, [json.dumps(READY)])
    assert CliRunner().invoke(app, ["triage", "2", "--yes"]).exit_code == 0
    assert gh.posts()[0][:4] == ["api", "-X", "PATCH", "repos/me/app/issues/comments/7"]

    reply = {"id": 8, "author": "me", "body": "Use TOML, not YAML.", "created_at": "t2"}
    gh = FakeGh([ours, reply])
    runtime = setup(monkeypatch, gh, [json.dumps(READY)])
    assert CliRunner().invoke(app, ["triage", "2", "--yes"]).exit_code == 0
    assert gh.posts()[0][1] == "repos/me/app/issues/2/comments"  # new comment after the reply
    assert "Use TOML, not YAML." in runtime.prompts[0]


def test_cli_triage_retries_bad_reply_then_declines(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = FakeGh()
    runtime = setup(monkeypatch, gh, ["no json here", json.dumps(READY)])
    result = CliRunner().invoke(app, ["triage", "2"], input="n\n")
    assert result.exit_code == 1 and "Not posted." in result.output
    assert len(runtime.prompts) == 2 and "Problem with your last reply" in runtime.prompts[1]
    assert "(2 runs)" in result.output and gh.posts() == []


def test_cli_triage_rejects_closed_issue(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, FakeGh(), [])
    result = CliRunner().invoke(app, ["triage", "1"])
    assert result.exit_code == 1 and "#1 is closed" in result.output
