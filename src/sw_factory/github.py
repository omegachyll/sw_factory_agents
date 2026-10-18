"""Thin wrapper over the `gh` CLI. All GitHub writes go through here so they can be dry-run and tested."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# Stage labels the factory moves issues through. (name, color, description)
FACTORY_LABELS: list[tuple[str, str, str]] = [
    ("factory:new", "ededed", "New issue, waiting for triage"),
    ("factory:needs-spec", "fbca04", "Needs a product/tech spec before implementation"),
    ("factory:spec-review", "f9d0c4", "Spec written, waiting for human review"),
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
