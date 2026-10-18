"""Locate the git repo the CLI is running in and its GitHub identity."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

FACTORY_DIR = ".factory"

# Matches git@github.com:owner/name(.git) and https://github.com/owner/name(.git)
_GITHUB_REMOTE = re.compile(r"github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$")


class NotInRepoError(Exception):
    pass


@dataclass(frozen=True)
class RepoContext:
    root: Path
    slug: str | None  # "owner/name", or None when there is no GitHub origin remote

    @property
    def factory_dir(self) -> Path:
        return self.root / FACTORY_DIR

    @property
    def has_factory(self) -> bool:
        return self.factory_dir.is_dir()


def _git(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def parse_github_slug(remote_url: str) -> str | None:
    match = _GITHUB_REMOTE.search(remote_url.strip())
    return match.group("slug") if match else None


def find_repo(cwd: Path | None = None) -> RepoContext:
    cwd = cwd or Path.cwd()
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if root is None:
        raise NotInRepoError(f"{cwd} is not inside a git repository")
    remote = _git(cwd, "remote", "get-url", "origin")
    return RepoContext(root=Path(root), slug=parse_github_slug(remote) if remote else None)
