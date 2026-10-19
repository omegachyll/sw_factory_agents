"""Thin wrapper over the `gh` CLI. All GitHub writes go through here so they can be dry-run and tested."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

# Stage labels the factory moves issues through. (name, color, description)
FACTORY_LABELS: list[tuple[str, str, str]] = [
    ("factory:new", "ededed", "New issue, waiting for triage"),
    ("factory:needs-spec", "fbca04", "Needs a product/tech spec before implementation"),
    ("factory:spec-review", "f9d0c4", "Spec written, waiting for human review"),
    ("factory:blocked", "c5def5", "Waiting on another issue to be closed first"),
    ("factory:ready", "0e8a16", "Ready to implement"),
    ("factory:in-progress", "1d76db", "An agent is implementing this"),
    ("factory:in-review", "5319e7", "PR open, in review"),
    ("factory:needs-human", "d93f0b", "Blocked on a human answer or decision"),
    ("epic", "3e4b9e", "Tracking issue that groups other issues"),
]

# Labels the project agent may put on issues; anything else it suggests is dropped.
ISSUE_LABELS = {"enhancement", "bug", "documentation", "epic"}


class GitHubError(Exception):
    pass


@dataclass
class Comment:
    id: int
    author: str
    body: str
    created_at: str


@dataclass
class IssueData:
    number: int
    title: str
    body: str
    state: str  # "open" | "closed"
    labels: list[str]
    is_pull_request: bool = False
    comments: list[Comment] = field(default_factory=list)
    milestone: str | None = None


Runner = Callable[[Sequence[str], Path | None], str]


def run_gh(args: Sequence[str], cwd: Path | None = None) -> str:
    result = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


@dataclass
class GitHub:
    runner: Runner = run_gh

    def current_user(self) -> str:
        return self.runner(["api", "user", "--jq", ".login"], None)

    def create_repo(self, name: str, *, source: Path, private: bool, description: str) -> str:
        """Create the GitHub repo from a local git repo and push it. Returns 'owner/name'."""
        self.runner(
            ["repo", "create", name, "--private" if private else "--public", "--source", str(source),
             "--remote", "origin", "--push", "--description", description],
            source,
        )
        return self.runner(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], source)

    def ensure_labels(self, slug: str, labels: list[tuple[str, str, str]] = FACTORY_LABELS) -> None:
        for name, color, description in labels:
            self.runner(["label", "create", name, "-R", slug, "--color", color, "--description", description,
                         "--force"], None)

    def create_milestone(self, slug: str, title: str, description: str = "") -> None:
        self.runner(["api", f"repos/{slug}/milestones", "-f", f"title={title}", "-f", f"description={description}"],
                    None)

    def create_issue(
        self, slug: str, title: str, body: str, labels: list[str], milestone: str | None = None
    ) -> str:
        """Create an issue and return its URL."""
        args = ["issue", "create", "-R", slug, "--title", title, "--body", body]
        for label in labels:
            args += ["--label", label]
        if milestone:
            args += ["--milestone", milestone]
        return self.runner(args, None)

    def repo_exists(self, slug: str) -> bool:
        try:
            json.loads(self.runner(["repo", "view", slug, "--json", "name"], None))
            return True
        except GitHubError:
            return False

    def issue(self, slug: str, number: int, with_comments: bool = True) -> IssueData:
        data = json.loads(self.runner(["api", f"repos/{slug}/issues/{number}"], None))
        issue = IssueData(
            number=data["number"], title=data["title"], body=data.get("body") or "", state=data["state"],
            labels=[label["name"] for label in data.get("labels", [])], is_pull_request="pull_request" in data,
            milestone=(data.get("milestone") or {}).get("title"),
        )
        if with_comments:
            lines = self.runner(
                ["api", "--paginate", f"repos/{slug}/issues/{number}/comments",
                 "--jq", ".[] | {id, author: .user.login, body, created_at}"], None
            )
            issue.comments = [Comment(**json.loads(line)) for line in lines.splitlines() if line.strip()]
        return issue

    def comment(self, slug: str, number: int, body: str) -> str:
        """Post a comment on an issue or PR and return its URL."""
        return self.runner(
            ["api", f"repos/{slug}/issues/{number}/comments", "-f", f"body={body}", "--jq", ".html_url"], None
        )

    def edit_comment(self, slug: str, comment_id: int, body: str) -> str:
        return self.runner(
            ["api", "-X", "PATCH", f"repos/{slug}/issues/comments/{comment_id}", "-f", f"body={body}",
             "--jq", ".html_url"], None
        )

    def set_stage(self, slug: str, number: int, stage: str, current_labels: list[str]) -> None:
        """Move an issue to a factory stage: add that label and remove any other `factory:` stage label."""
        known = {name: (color, description) for name, color, description in FACTORY_LABELS}
        if stage in known:  # the repo may predate this label
            self.ensure_labels(slug, [(stage, *known[stage])])
        args = ["issue", "edit", str(number), "-R", slug, "--add-label", stage]
        for label in current_labels:
            if label.startswith("factory:") and label != stage:
                args += ["--remove-label", label]
        self.runner(args, None)

    def list_issues(self, slug: str, state: str = "all") -> list[IssueData]:
        """Issues (not PRs) with labels, milestone and comments. Comment ids aren't included in this listing."""
        raw = self.runner(
            ["issue", "list", "-R", slug, "--state", state, "--limit", "500",
             "--json", "number,title,body,state,labels,milestone,comments"], None
        )
        return [
            IssueData(
                number=i["number"], title=i["title"], body=i.get("body") or "", state=i["state"].lower(),
                labels=[label["name"] for label in i.get("labels", [])],
                milestone=(i.get("milestone") or {}).get("title"),
                comments=[Comment(0, (c.get("author") or {}).get("login", ""), c.get("body", ""), c.get("createdAt", ""))
                          for c in i.get("comments", [])],
            )
            for i in sorted(json.loads(raw or "[]"), key=lambda i: i["number"])
        ]

    def milestones(self, slug: str) -> list[str]:
        out = self.runner(["api", f"repos/{slug}/milestones?state=all", "--jq", ".[].title"], None)
        return [line for line in out.splitlines() if line]

    def edit_issue(
        self, slug: str, number: int, *, title: str | None = None, body: str | None = None, milestone: str | None = None
    ) -> None:
        args = ["issue", "edit", str(number), "-R", slug]
        if title:
            args += ["--title", title]
        if body is not None:
            args += ["--body", body]
        if milestone:
            args += ["--milestone", milestone]
        self.runner(args, None)

    def close_issue(self, slug: str, number: int, comment: str) -> None:
        self.runner(["issue", "close", str(number), "-R", slug, "--reason", "not planned", "--comment", comment], None)

    def default_branch(self, slug: str) -> str:
        return self.runner(["repo", "view", slug, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"], None)

    def create_pr(self, slug: str, *, head: str, base: str, title: str, body: str) -> str:
        """Open a pull request and return its URL."""
        return self.runner(
            ["pr", "create", "-R", slug, "--head", head, "--base", base, "--title", title, "--body", body], None
        )
