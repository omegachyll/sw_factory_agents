"""Project agent (`factory new`): interview loop, plan parsing, and creation, with a fake runtime and fake gh."""

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

import pytest
from typer.testing import CliRunner

from sw_factory.cli import app, write_factory_dir
from sw_factory.github import GitHub
from sw_factory.project import (
    DONE_MARKER,
    Aborted,
    ProjectError,
    ProjectPlan,
    create_project,
    interview,
    link_dependencies,
    make_plan,
    parse_plan_reply,
)
from sw_factory.runtimes import AgentResult, AgentSpec

SPEC = AgentSpec(name="project", system_prompt="sys", runtime="fake", access="none")

PLAN = {
    "repo_name": "todo-app",
    "description": "A tiny todo app",
    "private": True,
    "product_md": "# Product\n",
    "tech_md": "# Tech\n",
    "milestones": [{"title": "v1: core", "description": "Core"}],
    "issues": [
        {"title": "Project scaffold", "body": "## Goal\nSet up", "labels": ["enhancement"], "milestone": "v1: core"},
        {"title": "Add todos", "body": "## Goal\nAdd\n\n## Notes\nDepends on: Project scaffold.",
         "labels": ["enhancement", "made-up"], "milestone": "v1: core"},
        {"title": "Sync todos", "body": "Pick a sync service.\nDepends on: Add todos; Project scaffold",
         "milestone": "v1: core", "needs_human": True},
    ],
}


class FakeRuntime:
    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies, self.prompts = list(replies), []

    async def run(self, spec: AgentSpec, prompt: str, cwd: Path) -> AgentResult:
        self.prompts.append(prompt)
        return AgentResult(text=self.replies.pop(0), runtime="fake", model=None, duration_s=0)


def answers(*values: str):
    queue = list(values)
    return lambda question: queue.pop(0)


# --- Interview ----------------------------------------------------------------------------------------------


async def test_interview_until_agent_completes(tmp_path: Path) -> None:
    runtime = FakeRuntime(["Who are the users?", "Which stack?", f"{DONE_MARKER}\nSummary: todo app for me."])
    turns = await interview(runtime, SPEC, tmp_path, answers("Just me", "Python"), idea="a todo app")

    assert [t.role for t in turns] == ["person", "agent", "person", "agent", "person", "agent"]
    assert turns[-1].text == "Summary: todo app for me."
    # Each call carries the whole conversation so far.
    assert "Person: Just me" in runtime.prompts[2] and "Person: My idea: a todo app" in runtime.prompts[2]


async def test_interview_done_forces_summary(tmp_path: Path) -> None:
    runtime = FakeRuntime(["Who are the users?", "Summary without the marker"])
    turns = await interview(runtime, SPEC, tmp_path, answers("/done"))
    assert turns[-1].text == "Summary without the marker"
    assert "wants to stop here" in runtime.prompts[1]


async def test_interview_quit(tmp_path: Path) -> None:
    with pytest.raises(Aborted):
        await interview(FakeRuntime(["Q1?"]), SPEC, tmp_path, answers("/quit"))


async def test_interview_question_limit(tmp_path: Path) -> None:
    runtime = FakeRuntime(["Another question?"] * 3)
    with pytest.raises(ProjectError, match="didn't finish"):
        await interview(runtime, SPEC, tmp_path, answers("a", "b", "c"), max_questions=2)


# --- Planning -----------------------------------------------------------------------------------------------


def test_parse_plan_reply_fenced_and_bare() -> None:
    assert parse_plan_reply(f"Here you go:\n```json\n{json.dumps(PLAN)}\n```").repo_name == "todo-app"
    assert parse_plan_reply(json.dumps(PLAN)).issues[0].title == "Project scaffold"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"repo_name": "bad name!"}, "invalid repo_name"),
        ({"issues": []}, "no issues"),
        ({"milestones": []}, "unknown milestone"),
        ({"tech_md": None, "issues": None}, "malformed"),
    ],
)
def test_plan_validation(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ProjectError, match=message):
        ProjectPlan.from_dict(PLAN | change)


async def test_make_plan_retries_once_on_bad_json(tmp_path: Path) -> None:
    runtime = FakeRuntime(["not json at all", f"```json\n{json.dumps(PLAN)}\n```"])
    plan = await make_plan(runtime, SPEC, tmp_path, [])
    assert plan.repo_name == "todo-app"
    assert "Problem with your last reply" in runtime.prompts[1]


async def test_make_plan_revision_includes_previous_and_feedback(tmp_path: Path) -> None:
    previous = ProjectPlan.from_dict(PLAN)
    runtime = FakeRuntime([json.dumps(PLAN | {"private": False})])
    plan = await make_plan(runtime, SPEC, tmp_path, [], previous=previous, feedback="make it public")
    assert plan.private is False
    assert "make it public" in runtime.prompts[0] and '"repo_name": "todo-app"' in runtime.prompts[0]


# --- Creation -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Depends on: Add todos.", "Depends on: #2."),
        ("**Depends on:** `Add todos`, Project scaffold", "**Depends on:** #2, #1"),
        ("- Depends on: Add todos with tags", "- Depends on: #3"),  # the longer title wins over "Add todos"
        ("Depends on: Later issue", "Depends on: Later issue"),  # no number yet: left as text
        ("Mentions Add todos elsewhere", "Mentions Add todos elsewhere"),  # only Depends-on lines change
    ],
)
def test_link_dependencies(line: str, expected: str) -> None:
    numbers = {"Project scaffold": 1, "Add todos": 2, "Add todos with tags": 3}
    assert link_dependencies(line, numbers) == expected


class FakeGh:
    def __init__(self, existing: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.existing = existing

    def __call__(self, args: Sequence[str], cwd: Path | None) -> str:
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["api", "user"]:
            return "me"
        if args[:2] == ["repo", "view"] and "nameWithOwner" in args:
            return "me/todo-app"
        if args[:2] == ["repo", "view"]:
            if self.existing:
                return '{"name": "todo-app"}'
            from sw_factory.github import GitHubError

            raise GitHubError("not found")
        if args[:2] == ["issue", "create"]:
            return f"https://github.com/me/todo-app/issues/{sum(c[:2] == ['issue', 'create'] for c in self.calls)}"
        return ""


def test_create_project(tmp_path: Path) -> None:
    gh = FakeGh()
    log: list[str] = []
    slug = create_project(ProjectPlan.from_dict(PLAN), tmp_path, GitHub(gh), write_factory_dir, log.append)

    assert slug == "me/todo-app"
    target = tmp_path / "todo-app"
    assert (target / "PRODUCT.md").read_text() == "# Product\n"
    assert (target / ".factory" / "config.yaml").exists()
    commits = subprocess.run(["git", "log", "--oneline"], cwd=target, capture_output=True, text=True).stdout
    assert "Initial project plan" in commits

    create = next(c for c in gh.calls if c[:2] == ["repo", "create"])
    assert "--private" in create and "--push" in create
    issues = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    assert len(issues) == 3
    second, third = issues[1], issues[2]
    labels = [second[i + 1] for i, a in enumerate(second) if a == "--label"]
    assert labels == ["factory:new", "enhancement"]  # unknown "made-up" label dropped
    assert second[second.index("--milestone") + 1] == "v1: core"
    body = second[second.index("--body") + 1]
    assert "Depends on: #1." in body and body.endswith("<!-- factory: agent=project -->")
    # Issues that need a decision skip triage; dependency titles become issue links.
    assert third[third.index("--label") + 1] == "factory:needs-human"
    assert "Depends on: #2; #1" in third[third.index("--body") + 1]
    assert any(c[:2] == ["label", "create"] for c in gh.calls)
    assert any(c[0] == "api" and c[1].endswith("/milestones") for c in gh.calls)


def test_create_project_dry_run_touches_nothing(tmp_path: Path) -> None:
    gh = FakeGh()
    log: list[str] = []
    assert create_project(ProjectPlan.from_dict(PLAN), tmp_path, GitHub(gh), write_factory_dir, log.append,
                          dry_run=True) is None
    assert gh.calls == [] and not (tmp_path / "todo-app").exists()
    assert any("open issue 'Add todos'" in line for line in log)


def test_create_project_refuses_taken_name(tmp_path: Path) -> None:
    gh = FakeGh(existing=True)
    with pytest.raises(ProjectError, match="already have a GitHub repo"):
        create_project(ProjectPlan.from_dict(PLAN), tmp_path, GitHub(gh), write_factory_dir, lambda _: None)
    assert not (tmp_path / "todo-app").exists()


def test_cli_new_from_plan_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_file = tmp_path / "todo-app.plan.json"
    plan_file.write_text(json.dumps(PLAN))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["new", "--from-plan", str(plan_file), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Plan for todo-app (private)" in result.output
    assert "[dry-run] would open issue 'Project scaffold'" in result.output
    assert not (tmp_path / "todo-app").exists()


def test_cli_new_interactive_rejects_bad_choice_and_quits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime(["Who is it for?", f"{DONE_MARKER}\nSummary.", json.dumps(PLAN)])
    monkeypatch.setattr("sw_factory.cli.get_runtime", lambda name: runtime)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["new", "a todo app"], input="just me\n/done\nq\n")

    assert result.exit_code == 0, result.output
    assert "is not one of" in result.output  # "/done" isn't a valid menu choice; re-prompted instead of revising
    assert len(runtime.prompts) == 3  # no revision call was made
    assert (tmp_path / "todo-app.plan.json").exists()
    assert "factory new --from-plan" in result.output
