"""The triage agent behind `factory triage`: decide whether an issue can be built now, and write its build plan."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sw_factory.footer import parse_footer
from sw_factory.github import Comment, GitHub, IssueData
from sw_factory.runtimes import AgentResult, AgentRuntime, AgentSpec

AGENT = "triage"
DECISIONS = ("ready", "needs-human", "needs-spec")
STAGES = {
    "ready": "factory:ready",
    "needs-human": "factory:needs-human",
    "needs-spec": "factory:needs-spec",
    "blocked": "factory:blocked",
}
HEADLINES = {
    "ready": "Ready to build",
    "needs-human": "Needs your input",
    "needs-spec": "Needs a spec or a split",
    "blocked": "Blocked",
}
DEPENDS_ON = re.compile(r"^.*Depends on.*$", re.IGNORECASE | re.MULTILINE)
ISSUE_REF = re.compile(r"(?<![\w/])#(\d+)\b")
JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
DOC_LIMIT = 40_000  # characters per project doc included in the prompt
FILE_LIST_LIMIT = 300


class TriageError(Exception):
    pass


@dataclass
class TriageResult:
    decision: str  # one of DECISIONS, or "blocked"
    summary: str
    plan: str = ""
    questions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "TriageResult":
        if not isinstance(data, dict):
            raise TriageError("reply must be a JSON object")
        decision = str(data.get("decision", "")).strip()
        if decision not in DECISIONS:
            raise TriageError(f"decision must be one of {', '.join(DECISIONS)}, got {decision!r}")
        result = cls(
            decision=decision,
            summary=str(data.get("summary") or "").strip(),
            plan=str(data.get("plan") or "").strip(),
            questions=[str(q).strip() for q in data.get("questions") or [] if str(q).strip()],
        )
        if not result.summary:
            raise TriageError("summary is empty")
        if decision == "ready" and not result.plan:
            raise TriageError("a ready decision needs a plan")
        if decision == "needs-human" and not result.questions:
            raise TriageError("a needs-human decision needs at least one question")
        return result


# --- Context ------------------------------------------------------------------------------------------------


def dependencies(body: str) -> list[int]:
    """Issue numbers on `Depends on:` lines, in order, without duplicates."""
    numbers = [int(n) for line in DEPENDS_ON.findall(body) for n in ISSUE_REF.findall(line)]
    return list(dict.fromkeys(numbers))


def open_dependencies(github: GitHub, slug: str, issue: IssueData) -> list[int]:
    return [n for n in dependencies(issue.body) if github.issue(slug, n, with_comments=False).state == "open"]


def previous_triage(comments: list[Comment]) -> tuple[Comment | None, list[Comment]]:
    """The latest triage comment from the factory, and the comments posted after it."""
    for i in range(len(comments) - 1, -1, -1):
        if (parse_footer(comments[i].body) or {}).get("agent") == AGENT:
            return comments[i], comments[i + 1:]
    return None, []


def repo_context(root: Path) -> str:
    parts = []
    for name in ("PRODUCT.md", "TECH.md"):
        path = root / name
        if path.exists():
            text = path.read_text()
            if len(text) > DOC_LIMIT:
                text = text[:DOC_LIMIT] + f"\n\n[... truncated; read {name} for the rest]"
            parts.append(f"## {name}\n\n{text.strip()}")
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    listing = "\n".join(files[:FILE_LIST_LIMIT])
    if len(files) > FILE_LIST_LIMIT:
        listing += f"\n[... {len(files) - FILE_LIST_LIMIT} more files]"
    parts.append(f"## Tracked files\n\n{listing or '(none yet)'}")
    return "\n\n".join(parts)


def _comment_author(comment: Comment) -> str:
    footer = parse_footer(comment.body)
    return f"factory {footer.get('agent', 'agent')} agent" if footer else comment.author


def build_prompt(issue: IssueData, context: str, closed_dependencies: list[int]) -> str:
    comments = "\n\n".join(
        f"### {_comment_author(c)} ({c.created_at})\n\n{c.body.strip()}" for c in issue.comments
    ) or "(no comments)"
    deps = ", ".join(f"#{n} (closed)" for n in closed_dependencies) or "none"
    return "\n\n".join([
        f"# Issue #{issue.number}: {issue.title}",
        f"Labels: {', '.join(issue.labels) or 'none'}. Dependencies: {deps}.",
        f"## Body\n\n{issue.body.strip() or '(empty)'}",
        f"## Comments, oldest first\n\n{comments}",
        f"# Project context\n\n{context}",
        "# Your task\n\nTriage this issue. Explore the repository as much as you need, then reply with a single JSON "
        "object in one ```json fenced block, and nothing after it:\n\n```json\n"
        '{"decision": "ready | needs-human | needs-spec",\n'
        ' "summary": "2-4 sentences for the issue thread: your decision and why",\n'
        ' "plan": "markdown build plan (required for ready; optional draft otherwise)",\n'
        ' "questions": ["for needs-human: each question with your recommended answer"]}\n```',
    ])


def parse_reply(text: str) -> TriageResult:
    matches = JSON_BLOCK.findall(text)
    raw = matches[-1] if matches else text
    try:
        return TriageResult.from_dict(json.loads(raw))
    except json.JSONDecodeError as e:
        raise TriageError(f"reply is not valid JSON ({e})") from e


async def triage_issue(
    runtime: AgentRuntime, spec: AgentSpec, cwd: Path, issue: IssueData, closed_dependencies: list[int]
) -> tuple[TriageResult, list[AgentResult]]:
    """Run the agent. Retries once, with the problem explained, if the reply can't be parsed."""
    prompt = build_prompt(issue, repo_context(cwd), closed_dependencies)
    runs = [await runtime.run(spec, prompt, cwd)]
    if not runs[-1].ok:
        raise TriageError(f"triage agent failed: {runs[-1].error}")
    try:
        return parse_reply(runs[-1].text), runs
    except TriageError as e:
        retry = (f"{prompt}\n\n# Problem with your last reply\n\n{e}\n\nYour last reply was:\n\n{runs[-1].text}\n\n"
                 "Reply again with only the corrected JSON block.")
        runs.append(await runtime.run(spec, retry, cwd))
        if not runs[-1].ok:
            raise TriageError(f"triage agent failed: {runs[-1].error}")
        return parse_reply(runs[-1].text), runs


# --- Output -------------------------------------------------------------------------------------------------


def render_comment(result: TriageResult, issue_number: int, model: str | None, footer: str) -> str:
    by = f" · `{model}`" if model else ""
    parts = [f"**Factory triage: {HEADLINES[result.decision]}**{by}", result.summary]
    if result.questions:
        parts.append("### Questions\n\n" + "\n".join(f"{n}. {q}" for n, q in enumerate(result.questions, 1)))
    if result.plan:
        title = "Build plan" if result.decision == "ready" else "Draft build plan"
        parts.append(f"## {title}\n\n{result.plan}")
    parts.append(f"---\n<sub>Reply on this issue with answers or corrections, then re-run "
                 f"`factory triage {issue_number}`.</sub>\n{footer}")
    return "\n\n".join(parts)


def publish(github: GitHub, slug: str, issue: IssueData, body: str, stage: str) -> tuple[str, bool]:
    """Post the triage comment and set the stage label. Returns (comment URL, edited).

    The previous triage comment is edited in place unless someone has replied since, in which case a new comment
    keeps the conversation readable.
    """
    previous, later = previous_triage(issue.comments)
    if previous and not later:
        url, edited = github.edit_comment(slug, previous.id, body), True
    else:
        url, edited = github.comment(slug, issue.number, body), False
    github.set_stage(slug, issue.number, stage, issue.labels)
    return url, edited
