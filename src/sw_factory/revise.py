"""`factory project`: the project agent revises an existing project's PRODUCT.md, TECH.md and open backlog.

The agent proposes; the factory decides how each issue change is applied, based on the issue's stage:
not started → edited; triaged (factory:ready) → edited and sent back to factory:new because its build plan is stale;
in progress or in review → not edited, flagged factory:needs-human with an explanation. Closed issues are never
touched; changes to built code become new issues.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sw_factory.github import ISSUE_LABELS, GitHub, IssueData
from sw_factory.project import JSON_BLOCK, Issue, Milestone, Turn, link_dependencies, transcript
from sw_factory.runtimes import AgentResult, AgentRuntime, AgentSpec

DOCS = ("PRODUCT.md", "TECH.md")
IN_FLIGHT = {"factory:in-progress", "factory:in-review"}
TRIAGED = "factory:ready"
MAX_ROUNDS = 3


class ReviseError(Exception):
    pass


@dataclass
class IssueChange:
    number: int
    action: Literal["update", "close"]
    reason: str
    title: str | None = None
    body: str | None = None
    milestone: str | None = None


@dataclass
class Proposal:
    summary: str
    docs: dict[str, str]  # only the docs that change: name → full new content
    changes: list[IssueChange] = field(default_factory=list)
    new_issues: list[Issue] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)  # new milestones

    @property
    def empty(self) -> bool:
        return not (self.docs or self.changes or self.new_issues)

    @classmethod
    def from_dict(cls, data: Any, open_issues: dict[int, IssueData], milestones: list[str]) -> "Proposal":
        if not isinstance(data, dict):
            raise ReviseError("proposal must be a JSON object")
        try:
            docs = {name: str(data[key]) for name, key in (("PRODUCT.md", "product_md"), ("TECH.md", "tech_md"))
                    if data.get(key)}
            changes = [
                IssueChange(int(c["number"]), c["action"], str(c.get("reason") or "").strip(), c.get("title") or None,
                            c.get("body"), c.get("milestone") or None)
                for c in data.get("issue_changes") or []
            ]
            new_issues = [
                Issue(str(i["title"]), str(i["body"]), [str(label) for label in i.get("labels", [])],
                      i.get("milestone") or None, bool(i.get("needs_human", False)))
                for i in data.get("new_issues") or []
            ]
            new_milestones = [Milestone(str(m["title"]), str(m.get("description", "")))
                              for m in data.get("milestones") or []]
        except (KeyError, TypeError, ValueError) as e:
            raise ReviseError(f"proposal is missing or has a malformed field: {e}") from e
        proposal = cls(str(data.get("summary") or "").strip(), docs, changes, new_issues, new_milestones)
        proposal.validate(open_issues, milestones)
        return proposal

    def validate(self, open_issues: dict[int, IssueData], milestones: list[str]) -> None:
        known = set(milestones) | {m.title for m in self.milestones}
        seen: set[int] = set()
        for c in self.changes:
            if c.number not in open_issues:
                raise ReviseError(f"#{c.number} is not an open issue; closed work needs a new issue instead")
            if c.number in seen:
                raise ReviseError(f"#{c.number} has more than one change")
            seen.add(c.number)
            if c.action not in ("update", "close"):
                raise ReviseError(f"#{c.number}: action must be update or close, got {c.action!r}")
            if not c.reason:
                raise ReviseError(f"#{c.number}: reason is empty")
            if c.action == "update" and not (c.title or c.body or c.milestone):
                raise ReviseError(f"#{c.number}: an update needs a new title, body or milestone")
            if c.milestone and c.milestone not in known:
                raise ReviseError(f"#{c.number} uses unknown milestone {c.milestone!r}")
        for issue in self.new_issues:
            if issue.milestone and issue.milestone not in known:
                raise ReviseError(f"new issue {issue.title!r} uses unknown milestone {issue.milestone!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "product_md": self.docs.get("PRODUCT.md"),
            "tech_md": self.docs.get("TECH.md"),
            "issue_changes": [c.__dict__ for c in self.changes],
            "new_issues": [i.__dict__ for i in self.new_issues],
            "milestones": [m.__dict__ for m in self.milestones],
        }


# --- How each change is applied -----------------------------------------------------------------------------

Kind = Literal["edit", "retriage", "flag", "close"]


@dataclass
class Action:
    kind: Kind
    issue: IssueData
    change: IssueChange


def plan_actions(proposal: Proposal, open_issues: dict[int, IssueData]) -> list[Action]:
    actions = []
    for change in proposal.changes:
        issue = open_issues[change.number]
        if IN_FLIGHT & set(issue.labels):
            kind: Kind = "flag"  # never change an issue under someone's feet
        elif change.action == "close":
            kind = "close"
        elif TRIAGED in issue.labels:
            kind = "retriage"
        else:
            kind = "edit"
        actions.append(Action(kind, issue, change))
    return actions


ACTION_LABELS: dict[Kind, str] = {
    "edit": "update",
    "retriage": "update + back to factory:new (its build plan is out of date)",
    "flag": "in progress: not edited, flagged factory:needs-human",
    "close": "close as superseded",
}


# --- Talking to the agent -----------------------------------------------------------------------------------

SCHEMA = """Either questions:

```json
{"questions": ["question, with your recommended answer", "..."]}
```

or the proposal:

```json
{
  "summary": "2-4 sentences: what changes and why",
  "product_md": "full new PRODUCT.md, or null if unchanged",
  "tech_md": "full new TECH.md, or null if unchanged",
  "issue_changes": [{"number": 2, "action": "update | close", "reason": "one or two sentences for the issue thread",
                     "title": "new title or null", "body": "full new body or null", "milestone": "or null"}],
  "new_issues": [{"title": "...", "body": "markdown body", "labels": ["enhancement"], "milestone": "...",
                  "needs_human": false}],
  "milestones": [{"title": "only milestones that don't exist yet", "description": "..."}]
}
```"""


def _issue_block(issue: IssueData) -> str:
    labels = ", ".join(issue.labels) or "none"
    comments = "\n\n".join(f"**{c.author}** ({c.created_at}):\n{c.body.strip()}" for c in issue.comments)
    return (f"### #{issue.number} {issue.title}\nLabels: {labels}. Milestone: {issue.milestone or 'none'}.\n\n"
            f"{issue.body.strip()}" + (f"\n\n#### Comments\n\n{comments}" if comments else ""))


def build_prompt(
    docs: dict[str, str | None],
    issues: list[IssueData],
    milestones: list[str],
    turns: list[Turn],
    force_proposal: bool = False,
    previous: Proposal | None = None,
    feedback: str | None = None,
) -> str:
    open_issues = [i for i in issues if i.state == "open"]
    closed = [i for i in issues if i.state != "open"]
    parts = [
        "Phase: revise.",
        *(f"# Current {name}\n\n{text.strip() if text else '(missing)'}" for name, text in docs.items()),
        f"# Milestones\n\n{', '.join(milestones) or 'none'}",
        "# Open issues\n\n" + ("\n\n".join(_issue_block(i) for i in open_issues) or "none"),
        "# Closed issues (already done; don't change them)\n\n"
        + ("\n".join(f"- #{i.number} {i.title}" for i in closed) or "none"),
        f"# Conversation\n\n{transcript(turns)}",
    ]
    if previous and feedback:
        parts.append(f"# Your previous proposal\n\n```json\n{json.dumps(previous.to_dict(), indent=2)}\n```")
        parts.append(f"# Requested changes\n\n{feedback}\n\nRevise the proposal accordingly; keep everything else.")
    task = ("Reply with the proposal now; record anything still undecided as open questions in the docs."
            if force_proposal or previous else
            "If you need answers before proposing, reply with questions. Otherwise reply with the proposal.")
    parts.append(f"# Your task\n\n{task} Use one ```json fenced block and nothing after it.\n\n{SCHEMA}")
    return "\n\n".join(parts)


def parse_reply(text: str, open_issues: dict[int, IssueData], milestones: list[str]) -> list[str] | Proposal:
    """Questions for the person, or a validated proposal."""
    matches = JSON_BLOCK.findall(text)
    try:
        data = json.loads(matches[-1] if matches else text)
    except json.JSONDecodeError as e:
        raise ReviseError(f"reply is not valid JSON ({e})") from e
    if isinstance(data, dict) and data.get("questions") and "summary" not in data:
        return [str(q) for q in data["questions"]]
    return Proposal.from_dict(data, open_issues, milestones)


async def ask_agent(
    runtime: AgentRuntime, spec: AgentSpec, cwd: Path, prompt: str, open_issues: dict[int, IssueData],
    milestones: list[str], runs: list[AgentResult],
) -> list[str] | Proposal:
    """One agent turn, retried once with the problem explained if the reply doesn't parse or validate."""
    for attempt in range(2):
        result = await runtime.run(spec, prompt, cwd)
        runs.append(result)
        if not result.ok:
            raise ReviseError(f"project agent failed: {result.error}")
        try:
            return parse_reply(result.text, open_issues, milestones)
        except ReviseError as e:
            if attempt:
                raise
            prompt = (f"{prompt}\n\n# Problem with your last reply\n\n{e}\n\nYour last reply was:\n\n{result.text}\n\n"
                      "Reply again with only the corrected JSON block.")
    raise AssertionError("unreachable")


# --- Showing and applying -----------------------------------------------------------------------------------


def doc_diff(name: str, old: str | None, new: str) -> str:
    def lines(text: str) -> list[str]:  # difflib joins a last line that has no newline onto the next one
        return [line + "\n" for line in text.splitlines()]

    return "".join(difflib.unified_diff(lines(old or ""), lines(new), f"a/{name}", f"b/{name}", n=2))


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReviseError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def read_remote_docs(root: Path, base: str, remote: str = "origin") -> dict[str, str | None]:
    """The docs as they are on the remote base branch, which is what the PR will change."""
    _git(["fetch", "-q", remote, base], root)
    docs: dict[str, str | None] = {}
    for name in DOCS:
        result = subprocess.run(["git", "show", f"{remote}/{base}:{name}"], cwd=root, capture_output=True, text=True)
        docs[name] = result.stdout if result.returncode == 0 else None
    return docs


def push_docs_branch(root: Path, docs: dict[str, str], branch: str, message: str, base: str,
                     remote: str = "origin") -> None:
    """Commit the new docs on a branch off the remote base, in a temporary worktree, and push it.

    Your own checkout (its branch, uncommitted changes) is never touched.
    """
    with tempfile.TemporaryDirectory(prefix="factory-docs-") as tmp:
        worktree = Path(tmp) / "wt"
        _git(["worktree", "add", "-q", "-b", branch, str(worktree), f"{remote}/{base}"], root)
        try:
            for name, text in docs.items():
                (worktree / name).write_text(text.strip() + "\n")
            _git(["add", *docs], worktree)
            _git(["commit", "-q", "-m", message], worktree)
            _git(["push", "-q", "-u", remote, branch], worktree)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, capture_output=True)


def issue_comment(action: Action, pr_url: str | None, footer: str) -> str:
    because = f"{action.change.reason}" + (f"\n\nDocs change: {pr_url}" if pr_url else "")
    if action.kind == "flag":
        proposed = action.change.body or "(no body change)"
        text = (f"**Factory project: this issue conflicts with a planned change**\n\n{because}\n\n"
                "Work on this issue is already under way, so it was not edited. Decide whether to finish it as is "
                "and open a follow-up issue, or update the work in progress.\n\n"
                f"<details><summary>Proposed new issue body</summary>\n\n{proposed}\n\n</details>")
    elif action.kind == "close":
        text = f"**Factory project: closing as superseded**\n\n{because}"
    else:
        text = f"**Factory project: issue updated to match the revised plan**\n\n{because}"
        if action.kind == "retriage":
            text += (f"\n\nThe build plan above is out of date, so this issue is back to `factory:new`. "
                     f"Re-run `factory triage {action.issue.number}`.")
    return f"{text}\n\n{footer}"


def apply_proposal(
    proposal: Proposal,
    actions: list[Action],
    slug: str,
    github: GitHub,
    pr_url: str | None,
    all_issues: list[IssueData],
    footer: str,
    log: Any,
) -> None:
    for milestone in proposal.milestones:
        github.create_milestone(slug, milestone.title, milestone.description)
        log(f"created milestone {milestone.title!r}")
    for action in actions:
        change, number = action.change, action.issue.number
        comment = issue_comment(action, pr_url, footer)
        if action.kind == "close":
            github.close_issue(slug, number, comment)
        else:
            if action.kind != "flag":
                github.edit_issue(slug, number, title=change.title, body=_with_footer(change.body, action.issue.body),
                                  milestone=change.milestone)
            github.comment(slug, number, comment)
            if action.kind in ("retriage", "flag"):
                stage = "factory:new" if action.kind == "retriage" else "factory:needs-human"
                github.set_stage(slug, number, stage, action.issue.labels)
        log(f"#{number}: {ACTION_LABELS[action.kind]}")
    numbers = {i.title: i.number for i in all_issues}
    for issue in proposal.new_issues:
        stage = "factory:needs-human" if issue.needs_human else "factory:new"
        labels = [stage, *(label for label in issue.labels if label in ISSUE_LABELS)]
        url = github.create_issue(slug, issue.title, f"{link_dependencies(issue.body, numbers)}\n\n{footer}", labels,
                                  issue.milestone)
        if match := re.search(r"/issues/(\d+)\s*$", url):
            numbers[issue.title] = int(match.group(1))
        log(f"opened {url}  {issue.title}")


def _with_footer(new_body: str | None, old_body: str) -> str | None:
    """Keep the original factory footer on an edited issue body."""
    if new_body is None:
        return None
    footer = re.search(r"\n*<!-- factory: [^>]*-->\s*$", old_body)
    return new_body.rstrip() + (footer.group(0) if footer and "<!-- factory:" not in new_body else "")
