"""`factory project`: revising docs and the open backlog, and the per-stage rules for applying issue changes."""

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

import pytest
from typer.testing import CliRunner

from sw_factory.cli import app
from sw_factory.github import Comment, GitHub, IssueData
from sw_factory.project import Turn
from sw_factory.revise import (
    Proposal,
    ReviseError,
    apply_proposal,
    build_prompt,
    parse_reply,
    plan_actions,
    push_docs_branch,
    read_remote_docs,
)
from sw_factory.runtimes import AgentResult, AgentSpec

FOOTER = "<!-- factory: agent=project -->"


def issue(number: int, labels: list[str], state: str = "open", body: str = "## Goal\nx") -> IssueData:
    return IssueData(number, f"Issue {number}", f"{body}\n\n{FOOTER}", state, labels, milestone="v1: core")


ISSUES = [
    issue(1, ["enhancement"], state="closed"),
    issue(2, ["factory:new"]),
    issue(3, ["factory:ready"]),
    issue(4, ["factory:in-progress"]),
    issue(5, ["factory:new"]),
]
OPEN = {i.number: i for i in ISSUES if i.state == "open"}
MILESTONES = ["v1: core"]


def change(number: int, action: str = "update", **kw: Any) -> dict[str, Any]:
    return {"number": number, "action": action, "reason": f"because {number}", "body": "## Goal\nnew"} | kw


PROPOSAL = {
    "summary": "Use TOML for config.",
    "product_md": None,
    "tech_md": "# Tech\nTOML\n",
    "issue_changes": [change(2), change(3), change(4), change(5, "close", body=None)],
    "new_issues": [{"title": "Migrate config", "body": "Depends on: Issue 2", "labels": ["enhancement", "x"],
                    "milestone": "v1: polish"}],
    "milestones": [{"title": "v1: polish", "description": "Polish"}],
}


# --- Proposal and rules -------------------------------------------------------------------------------------


def test_plan_actions_follow_issue_stage() -> None:
    proposal = Proposal.from_dict(PROPOSAL, OPEN, MILESTONES)
    kinds = {a.issue.number: a.kind for a in plan_actions(proposal, OPEN)}
    # not started → edit; triaged → retriage; in progress → flag (even for a close); close → close
    assert kinds == {2: "edit", 3: "retriage", 4: "flag", 5: "close"}
    in_progress_close = Proposal.from_dict(PROPOSAL | {"issue_changes": [change(4, "close")]}, OPEN, MILESTONES)
    assert plan_actions(in_progress_close, OPEN)[0].kind == "flag"


@pytest.mark.parametrize(
    "changes, message",
    [
        ([change(1)], "#1 is not an open issue"),
        ([change(2), change(2)], "more than one change"),
        ([change(2, body=None)], "needs a new title, body or milestone"),
        ([change(2, milestone="v9")], "unknown milestone"),
        ([change(2, action="reopen")], "action must be update or close"),
        ([change(2, reason="")], "reason is empty"),
    ],
)
def test_proposal_validation(changes: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(ReviseError, match=message):
        Proposal.from_dict(PROPOSAL | {"issue_changes": changes}, OPEN, MILESTONES)


def test_parse_reply_questions_or_proposal() -> None:
    assert parse_reply('```json\n{"questions": ["Pin? (recommend 17)"]}\n```', OPEN, MILESTONES) == [
        "Pin? (recommend 17)"
    ]
    proposal = parse_reply(json.dumps(PROPOSAL), OPEN, MILESTONES)
    assert isinstance(proposal, Proposal) and list(proposal.docs) == ["TECH.md"]


def test_build_prompt_includes_comments_and_closed_issues() -> None:
    with_comment = issue(2, ["factory:needs-human"])
    with_comment.comments = [Comment(0, "me", "No temperature compensation for v1.", "t1")]
    prompt = build_prompt({"PRODUCT.md": "# Product", "TECH.md": None}, [ISSUES[0], with_comment], MILESTONES,
                          [Turn("person", "add temperature compensation")])
    assert "No temperature compensation for v1." in prompt
    assert "- #1 Issue 1" in prompt and "# Current TECH.md\n\n(missing)" in prompt
    assert "If you need answers before proposing" in prompt


# --- Git: the docs branch never touches your checkout -------------------------------------------------------


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_docs_branch_in_temporary_worktree(tmp_path: Path) -> None:
    remote, local = tmp_path / "remote.git", tmp_path / "local"
    git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    git("clone", "-q", str(remote), str(local), cwd=tmp_path)
    git("config", "user.email", "t@example.com", cwd=local)
    git("config", "user.name", "T", cwd=local)
    (local / "PRODUCT.md").write_text("# Product\n")
    git("add", ".", cwd=local)
    git("commit", "-q", "-m", "init", cwd=local)
    git("push", "-q", "origin", "HEAD:main", cwd=local)
    (local / "PRODUCT.md").write_text("# Product\nmy uncommitted edit\n")

    assert read_remote_docs(local, "main") == {"PRODUCT.md": "# Product\n", "TECH.md": None}
    push_docs_branch(local, {"TECH.md": "# Tech\nTOML"}, "factory/project-1", "Revise TECH.md", "main")

    assert git("show", "factory/project-1:TECH.md", cwd=remote) == "# Tech\nTOML\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=local).strip() == "main"
    assert (local / "PRODUCT.md").read_text() == "# Product\nmy uncommitted edit\n"  # checkout untouched
    assert "factory-docs-" not in git("worktree", "list", cwd=local)


# --- Applying -----------------------------------------------------------------------------------------------


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], cwd: Path | None) -> str:
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return json.dumps([
                {"number": i.number, "title": i.title, "body": i.body, "state": i.state.upper(),
                 "labels": [{"name": n} for n in i.labels], "milestone": {"title": i.milestone}, "comments": []}
                for i in ISSUES
            ])
        if args[:2] == ["repo", "view"]:
            return "main"
        if args[0] == "api" and args[1].endswith("milestones?state=all"):
            return "\n".join(MILESTONES)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/me/app/issues/6"
        if args[:2] == ["pr", "create"]:
            return "https://github.com/me/app/pull/7"
        return ""

    def find(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def test_apply_proposal(tmp_path: Path) -> None:
    gh = FakeGh()
    proposal = Proposal.from_dict(PROPOSAL, OPEN, MILESTONES)
    log: list[str] = []
    apply_proposal(proposal, plan_actions(proposal, OPEN), "me/app", GitHub(gh), "https://pr/7", ISSUES,
                   "<!-- factory: agent=project run=r1 -->", log.append)

    edits = {c[2]: c for c in gh.find("issue", "edit") if "--body" in c}
    assert set(edits) == {"2", "3"}  # #4 is in progress: not edited
    assert edits["2"][edits["2"].index("--body") + 1] == f"## Goal\nnew\n\n{FOOTER}"  # original footer kept
    stages = {c[2]: c[c.index("--add-label") + 1] for c in gh.find("issue", "edit") if "--add-label" in c}
    assert stages == {"3": "factory:new", "4": "factory:needs-human"}
    comments = {c[1].split("/")[4]: c[c.index("-f") + 1] for c in gh.find("api") if c[1].endswith("/comments")}
    assert "Docs change: https://pr/7" in comments["2"]
    assert "Re-run `factory triage 3`" in comments["3"]
    assert "was not edited" in comments["4"] and "Proposed new issue body" in comments["4"]
    close = gh.find("issue", "close")[0]
    assert close[2] == "5" and "superseded" in close[close.index("--comment") + 1]
    assert gh.find("api", "repos/me/app/milestones")
    new = gh.find("issue", "create")[0]
    assert new[new.index("--body") + 1].startswith("Depends on: #2")
    assert [new[i + 1] for i, a in enumerate(new) if a == "--label"] == ["factory:new", "enhancement"]


# --- CLI ----------------------------------------------------------------------------------------------------


class FakeRuntime:
    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies, self.prompts = list(replies), []

    async def run(self, spec: AgentSpec, prompt: str, cwd: Path) -> AgentResult:
        self.prompts.append(prompt)
        return AgentResult(text=self.replies.pop(0), runtime="fake", model=None, duration_s=1)


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:me/app.git"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init", "--no-labels"])
    gh, pushed = FakeGh(), []
    monkeypatch.setattr("sw_factory.cli.GitHub", lambda: GitHub(gh))
    monkeypatch.setattr("sw_factory.cli.read_remote_docs",
                        lambda root, base: {"PRODUCT.md": "# Product\n", "TECH.md": "# Tech\nYAML\n"})
    monkeypatch.setattr("sw_factory.cli.push_docs_branch", lambda *a, **kw: pushed.append(a))

    def runtime(replies: list[str]) -> FakeRuntime:
        fake = FakeRuntime(replies)
        monkeypatch.setattr("sw_factory.cli.get_runtime", lambda name: fake)
        return fake

    return {"gh": gh, "pushed": pushed, "runtime": runtime}


def test_cli_project_questions_then_apply(cli: dict[str, Any]) -> None:
    runtime = cli["runtime"](['{"questions": ["TOML or JSON? (recommend TOML)"]}', json.dumps(PROPOSAL)])
    result = CliRunner().invoke(app, ["project", "use a different config format"], input="TOML\na\n")

    assert result.exit_code == 0, result.output
    assert "Person: 1. TOML" in runtime.prompts[1]
    assert "+TOML" in result.output and "-YAML" in result.output  # doc diff
    assert "#4 Issue 4: in progress: not edited" in result.output
    assert "      -x" in result.output and "      +new" in result.output  # issue body diff
    assert cli["pushed"][0][1] == {"TECH.md": "# Tech\nTOML\n"}
    pr = cli["gh"].find("pr", "create")[0]
    assert pr[pr.index("--base") + 1] == "main" and "agent=project" in pr[pr.index("--body") + 1]
    assert "Merge the PR soon" in result.output


def test_cli_project_dry_run_and_quit_change_nothing(cli: dict[str, Any]) -> None:
    cli["runtime"]([json.dumps(PROPOSAL)])
    dry = CliRunner().invoke(app, ["project", "use TOML", "--dry-run"])
    assert dry.exit_code == 0 and "[dry-run] nothing was changed" in dry.output

    cli["runtime"]([json.dumps(PROPOSAL)])
    quit_ = CliRunner().invoke(app, ["project", "use TOML"], input="q\n")
    assert quit_.exit_code == 1 and "Nothing was changed." in quit_.output

    writes = [c for c in cli["gh"].calls if c[0] in ("pr",) or c[:2] in (["issue", "edit"], ["issue", "close"])]
    assert writes == [] and cli["pushed"] == []


def test_cli_project_revise_with_feedback(cli: dict[str, Any]) -> None:
    smaller = PROPOSAL | {"issue_changes": [change(2)], "new_issues": [], "milestones": [], "tech_md": None}
    runtime = cli["runtime"]([json.dumps(PROPOSAL), json.dumps(smaller)])
    result = CliRunner().invoke(app, ["project", "use TOML"], input="r\ndon't add issues\na\n")
    assert result.exit_code == 0, result.output
    assert "don't add issues" in runtime.prompts[1] and "Your previous proposal" in runtime.prompts[1]
    assert cli["pushed"] == []  # revised proposal changes no docs, so no PR
    assert len(cli["gh"].find("issue", "edit")) == 1
