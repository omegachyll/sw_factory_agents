import subprocess
from pathlib import Path

import pytest

from sw_factory.repo import NotInRepoError, find_repo, parse_github_slug


@pytest.mark.parametrize(
    "url, slug",
    [
        ("git@github.com:owner/name.git", "owner/name"),
        ("git@github.com:owner/name", "owner/name"),
        ("https://github.com/owner/name.git", "owner/name"),
        ("https://github.com/owner/name/", "owner/name"),
        ("ssh://git@github.com/owner/my.repo.git", "owner/my.repo"),
        ("https://gitlab.com/owner/name.git", None),
    ],
)
def test_parse_github_slug(url: str, slug: str | None) -> None:
    assert parse_github_slug(url) == slug


def test_find_repo_from_subdirectory(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:me/app.git"], cwd=tmp_path, check=True)
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    repo = find_repo(sub)
    assert repo.root.resolve() == tmp_path.resolve()
    assert repo.slug == "me/app"
    assert not repo.has_factory


def test_find_repo_without_remote(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert find_repo(tmp_path).slug is None


def test_not_in_repo(tmp_path: Path) -> None:
    with pytest.raises(NotInRepoError):
        find_repo(tmp_path)
