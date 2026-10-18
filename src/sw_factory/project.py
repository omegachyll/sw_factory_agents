"""The project agent behind `factory new`: interview → plan (docs + backlog) → create the repo, labels and issues."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from sw_factory.github import ISSUE_LABELS, GitHub
from sw_factory.runtimes import AgentRuntime, AgentSpec

DONE_MARKER = "INTERVIEW COMPLETE"
MAX_QUESTIONS = 25
ISSUE_FOOTER = "\n\n<!-- factory: agent=project -->"
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


class ProjectError(Exception):
    pass


class Aborted(Exception):
    pass


# --- Plan model ---------------------------------------------------------------------------------------------


@dataclass
class Milestone:
    title: str
    description: str = ""


@dataclass
class Issue:
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    milestone: str | None = None
    needs_human: bool = False


@dataclass
class ProjectPlan:
    repo_name: str
    description: str
    private: bool
    product_md: str
    tech_md: str
    milestones: list[Milestone]
    issues: list[Issue]

    @classmethod
    def from_dict(cls, data: Any) -> "ProjectPlan":
        if not isinstance(data, dict):
            raise ProjectError("plan must be a JSON object")
        try:
            plan = cls(
                repo_name=str(data["repo_name"]).strip(),
                description=str(data["description"]).strip(),
                private=bool(data.get("private", True)),
                product_md=str(data["product_md"]),
                tech_md=str(data["tech_md"]),
                milestones=[Milestone(str(m["title"]), str(m.get("description", ""))) for m in data.get("milestones", [])],
                issues=[
                    Issue(str(i["title"]), str(i["body"]), [str(label) for label in i.get("labels", [])],
                          i.get("milestone") or None, bool(i.get("needs_human", False)))
                    for i in data["issues"]
                ],
            )
        except (KeyError, TypeError) as e:
            raise ProjectError(f"plan is missing or has a malformed field: {e}") from e
        plan.validate()
        return plan

    def validate(self) -> None:
        if not REPO_NAME.match(self.repo_name):
            raise ProjectError(f"invalid repo_name {self.repo_name!r}: use letters, digits, '.', '-' or '_'")
        if not self.issues:
            raise ProjectError("plan has no issues")
        titles = {m.title for m in self.milestones}
        for issue in self.issues:
            if issue.milestone and issue.milestone not in titles:
                raise ProjectError(f"issue {issue.title!r} uses unknown milestone {issue.milestone!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_plan(plan: ProjectPlan, path: Path) -> None:
    path.write_text(json.dumps(plan.to_dict(), indent=2) + "\n")


def load_plan(path: Path) -> ProjectPlan:
    try:
        return ProjectPlan.from_dict(json.loads(path.read_text()))
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path}: invalid JSON ({e})") from e


# --- Interview ----------------------------------------------------------------------------------------------


@dataclass
class Turn:
    role: Literal["agent", "person"]
    text: str


def transcript(turns: list[Turn]) -> str:
    return "\n\n".join(f"{'Agent' if t.role == 'agent' else 'Person'}: {t.text}" for t in turns)


async def _ask_agent(runtime: AgentRuntime, spec: AgentSpec, prompt: str, cwd: Path) -> str:
    result = await runtime.run(spec, prompt, cwd)
    if not result.ok:
        raise ProjectError(f"project agent failed: {result.error}")
    return result.text.strip()


async def interview(
    runtime: AgentRuntime,
    spec: AgentSpec,
    cwd: Path,
    ask: Callable[[str], str],
    idea: str | None = None,
    max_questions: int = MAX_QUESTIONS,
) -> list[Turn]:
    """Run the interview. `ask` shows the agent's question and returns the person's answer.

    The person can answer `/done` to wrap up early or `/quit` to abort. Returns the turns, ending with the agent's
    summary.
    """
    turns: list[Turn] = [Turn("person", f"My idea: {idea}")] if idea else []
    wrapping_up = False
    for _ in range(max_questions + 1):
        if wrapping_up:
            instruction = (f"The person wants to stop here. Reply with a line `{DONE_MARKER}` followed by a concise "
                           "summary of everything agreed, listing anything still undecided as open questions.")
        else:
            instruction = (f"Reply with your next question only. When the interview checklist is covered, reply with "
                           f"a line `{DONE_MARKER}` followed by a concise summary of everything agreed.")
        history = transcript(turns) if turns else "(The conversation hasn't started yet. Open with your first question.)"
        reply = await _ask_agent(runtime, spec, f"Phase: interview.\n\n## Conversation so far\n\n{history}\n\n"
                                                f"## Your task\n\n{instruction}", cwd)
        if DONE_MARKER in reply or wrapping_up:
            turns.append(Turn("agent", reply.replace(DONE_MARKER, "").strip()))
            return turns
        turns.append(Turn("agent", reply))
        answer = ask(reply).strip()
        if answer == "/quit":
            raise Aborted()
        if answer == "/done":
            wrapping_up = True
            turns.append(Turn("person", "That's all I want to cover. Please wrap up."))
        else:
            turns.append(Turn("person", answer or "(no answer)"))
    raise ProjectError(f"interview didn't finish within {max_questions} questions")


# --- Planning -----------------------------------------------------------------------------------------------

PLAN_SCHEMA = """{
  "repo_name": "short-kebab-case-name",
  "description": "one-line description for the GitHub repo",
  "private": true,
  "product_md": "full markdown content of PRODUCT.md",
  "tech_md": "full markdown content of TECH.md",
  "milestones": [{"title": "v1: core", "description": "..."}],
  "issues": [{"title": "...", "body": "markdown body", "labels": ["enhancement"], "milestone": "v1: core",
              "needs_human": false}]
}"""


def parse_plan_reply(text: str) -> ProjectPlan:
    match = JSON_BLOCK.search(text)
    raw = match.group(1) if match else text
    try:
        return ProjectPlan.from_dict(json.loads(raw))
    except json.JSONDecodeError as e:
        raise ProjectError(f"reply is not valid JSON ({e})") from e


async def make_plan(
    runtime: AgentRuntime,
    spec: AgentSpec,
    cwd: Path,
    turns: list[Turn],
    previous: ProjectPlan | None = None,
    feedback: str | None = None,
) -> ProjectPlan:
    parts = ["Phase: plan.", f"## Interview\n\n{transcript(turns)}"]
    if previous and feedback:
        parts.append(f"## Your previous plan\n\n```json\n{json.dumps(previous.to_dict(), indent=2)}\n```")
        parts.append(f"## Requested changes\n\n{feedback}\n\nRevise the plan accordingly; keep everything else.")
    parts.append(
        "## Your task\n\nWrite the project plan as a single JSON object in one ```json fenced block, and nothing else. "
        f"Schema:\n\n```json\n{PLAN_SCHEMA}\n```\n\nList issues in build order. Every milestone an issue references "
        "must be in `milestones`."
    )
    prompt = "\n\n".join(parts)
    reply = await _ask_agent(runtime, spec, prompt, cwd)
    try:
        return parse_plan_reply(reply)
    except ProjectError as e:  # one retry, telling the agent what was wrong
        reply = await _ask_agent(runtime, spec, f"{prompt}\n\n## Problem with your last reply\n\n{e}\n\n"
                                                 "Reply again with only the corrected JSON block.", cwd)
        return parse_plan_reply(reply)


# --- Creation -----------------------------------------------------------------------------------------------

DEPENDS_ON = re.compile(r"^(?P<prefix>\s*(?:[-*]\s*)?\**Depends on:?\**:?\s*)(?P<rest>.+)$", re.IGNORECASE | re.MULTILINE)
ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)\s*$")


def link_dependencies(body: str, numbers: dict[str, int]) -> str:
    """Rewrite issue titles on `Depends on:` lines as `#N` links. Titles without a number yet are left as text."""
    titles = sorted(numbers, key=len, reverse=True)  # longest first, so a title containing another wins

    def link(match: re.Match[str]) -> str:
        rest = match.group("rest")
        for title in titles:
            rest = re.sub(rf"`?{re.escape(title)}`?", f"#{numbers[title]}", rest, flags=re.IGNORECASE)
        return match.group("prefix") + rest

    return DEPENDS_ON.sub(link, body)



def _git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProjectError(f"git {args[0]} failed: {result.stderr.strip()}")


def create_project(
    plan: ProjectPlan,
    parent: Path,
    github: GitHub,
    init_factory: Callable[[Path], object],
    log: Callable[[str], None],
    dry_run: bool = False,
) -> str | None:
    """Create the local repo, the GitHub repo, labels, milestones and issues. Returns 'owner/name'."""
    target = parent / plan.repo_name
    if target.exists():
        raise ProjectError(f"{target} already exists")

    issue_labels = [[label for label in i.labels if label in ISSUE_LABELS] for i in plan.issues]
    steps = [
        f"create {target} with README.md, PRODUCT.md, TECH.md and .factory/, and make the first commit",
        f"create {'private' if plan.private else 'public'} GitHub repo {plan.repo_name!r} and push",
        "create factory labels",
        *(f"create milestone {m.title!r}" for m in plan.milestones),
        *(f"open issue {i.title!r}{' (needs a human decision)' if i.needs_human else ''}" for i in plan.issues),
    ]
    if dry_run:
        for step in steps:
            log(f"[dry-run] would {step}")
        return None

    # Fail before touching anything if the GitHub repo name is taken.
    if github.repo_exists(f"{github.current_user()}/{plan.repo_name}"):
        raise ProjectError(f"you already have a GitHub repo named {plan.repo_name!r}; change repo_name in the plan")

    target.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], target)
    (target / "README.md").write_text(
        f"# {plan.repo_name}\n\n{plan.description}\n\nSee [PRODUCT.md](PRODUCT.md) and [TECH.md](TECH.md).\n"
    )
    (target / "PRODUCT.md").write_text(plan.product_md.strip() + "\n")
    (target / "TECH.md").write_text(plan.tech_md.strip() + "\n")
    init_factory(target)
    _git(["add", "-A"], target)
    _git(["commit", "-q", "-m", "Initial project plan from factory new"], target)
    log(f"created {target}")

    slug = github.create_repo(plan.repo_name, source=target, private=plan.private, description=plan.description)
    log(f"created and pushed https://github.com/{slug}")
    github.ensure_labels(slug)
    log("created factory labels")
    for milestone in plan.milestones:
        github.create_milestone(slug, milestone.title, milestone.description)
        log(f"created milestone {milestone.title!r}")
    numbers: dict[str, int] = {}
    for issue, labels in zip(plan.issues, issue_labels):
        stage = "factory:needs-human" if issue.needs_human else "factory:new"
        body = link_dependencies(issue.body, numbers) + ISSUE_FOOTER
        url = github.create_issue(slug, issue.title, body, [stage, *labels], issue.milestone)
        if match := ISSUE_URL_NUMBER.search(url):
            numbers[issue.title] = int(match.group(1))
        log(f"opened {url}  {issue.title}")
    return slug
