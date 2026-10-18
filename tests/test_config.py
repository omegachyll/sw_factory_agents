import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from sw_factory.cli import CONFIG_TEMPLATE, app
from sw_factory.config import ConfigError, load_agent, load_config, parse_agent_file, parse_config
from sw_factory.repo import find_repo


@pytest.fixture
def repo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_template_is_valid_config() -> None:
    config = parse_config(yaml.safe_load(CONFIG_TEMPLATE))
    assert config.default_runtime == "claude"
    assert config.agent("review").runtime is None  # Copilot stays opt-in until verified


def test_resolve_precedence() -> None:
    config = parse_config(
        {"default_runtime": "claude", "default_model": "m-default",
         "agents": {"review": {"runtime": "copilot", "model": "m-review"}}}
    )
    assert config.resolve("implement") == ("claude", "m-default")
    assert config.resolve("review") == ("copilot", "m-review")
    assert config.resolve("review", model="m-cli") == ("copilot", "m-cli")
    # Switching runtime on the CLI drops the model configured for the other runtime.
    assert config.resolve("review", runtime="claude") == ("claude", None)
    assert config.resolve("review", runtime="copilot") == ("copilot", "m-review")


@pytest.mark.parametrize(
    "data",
    [
        {"default_runtime": "gpt"},
        {"agents": {"review": {"runtime": "other"}}},
        {"agents": {"review": {"level": "L9"}}},
        ["not", "a", "mapping"],
    ],
)
def test_invalid_config(data: object) -> None:
    with pytest.raises(ConfigError):
        parse_config(data)


def test_parse_agent_file() -> None:
    meta, body = parse_agent_file("---\naccess: write\n---\nYou implement issues.\n", "x.md")
    assert meta == {"access": "write"}
    assert body == "You implement issues."
    assert parse_agent_file("Just a prompt.", "x.md") == ({}, "Just a prompt.")


def test_init_then_load_agent(repo_dir: Path) -> None:
    result = CliRunner().invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (repo_dir / ".factory" / ".gitignore").read_text().strip() == "runs.db"

    repo = find_repo()
    (repo.factory_dir / "agents" / "implement.md").write_text("---\naccess: write\n---\nImplement the issue.\n")
    spec = load_agent(repo, "implement", load_config(repo), runtime="copilot")
    assert (spec.runtime, spec.access, spec.system_prompt) == ("copilot", "write", "Implement the issue.")

    with pytest.raises(ConfigError, match="no agent definition"):
        load_agent(repo, "triage", load_config(repo))

    again = CliRunner().invoke(app, ["init"])
    assert again.exit_code == 1  # refuses to overwrite without --force


def test_agent_commands_require_init(repo_dir: Path) -> None:
    result = CliRunner().invoke(app, ["implement", "3"])
    assert result.exit_code == 1
    assert "factory init" in result.output
