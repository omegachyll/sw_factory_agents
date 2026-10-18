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


@pytest.fixture
def factory_prompts(tmp_path: Path) -> Path:
    root = tmp_path / "factory-prompts"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "implement.md").write_text("---\naccess: write\n---\nYou implement issues.\n")
    (root / "skills" / "implement").mkdir(parents=True)
    (root / "skills" / "implement" / "b-testing.md").write_text("Run the tests.")
    (root / "skills" / "implement" / "a-git.md").write_text("Small commits.")
    return root


def test_init_creates_project_layer_only(repo_dir: Path) -> None:
    result = CliRunner().invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    fdir = repo_dir / ".factory"
    assert (fdir / ".gitignore").read_text().strip() == "runs.db"
    assert (fdir / "skills" / "README.md").exists()
    assert not (fdir / "agents").exists()  # base agents live in the factory, not copied per project

    again = CliRunner().invoke(app, ["init"])
    assert again.exit_code == 1  # refuses to overwrite without --force


def test_load_agent_layers_factory_and_project_skills(repo_dir: Path, factory_prompts: Path) -> None:
    CliRunner().invoke(app, ["init"])
    repo = find_repo()
    project_skills = repo.factory_dir / "skills" / "implement"
    project_skills.mkdir(parents=True)
    (project_skills / "conventions.md").write_text("Use pnpm.")

    spec = load_agent(repo, "implement", load_config(repo), runtime="copilot", factory_prompts=factory_prompts)

    assert (spec.runtime, spec.access) == ("copilot", "write")
    prompt = spec.system_prompt
    # Base prompt, then factory skills in file order, then project skills last so they take precedence.
    order = [prompt.index(s) for s in ("You implement issues.", "## a-git", "## b-testing", "Project-specific", "Use pnpm.")]
    assert order == sorted(order)


def test_project_can_override_base_prompt(repo_dir: Path, factory_prompts: Path) -> None:
    CliRunner().invoke(app, ["init"])
    repo = find_repo()
    (repo.factory_dir / "agents").mkdir()
    (repo.factory_dir / "agents" / "implement.md").write_text("---\naccess: read\n---\nCustom base.\n")

    spec = load_agent(repo, "implement", load_config(repo), factory_prompts=factory_prompts)
    assert spec.access == "read"
    assert spec.system_prompt.startswith("Custom base.")
    assert "Small commits." in spec.system_prompt  # factory skills still apply


def test_load_agent_without_repo_and_unknown_agent(factory_prompts: Path) -> None:
    from sw_factory.config import FactoryConfig

    spec = load_agent(None, "implement", FactoryConfig(), factory_prompts=factory_prompts)
    assert "Project-specific" not in spec.system_prompt
    with pytest.raises(ConfigError, match="unknown agent"):
        load_agent(None, "triage", FactoryConfig(), factory_prompts=factory_prompts)


def test_shipped_project_agent_loads() -> None:
    from sw_factory.config import FactoryConfig

    spec = load_agent(None, "project", FactoryConfig())
    assert spec.access == "none"
    assert "## interview" in spec.system_prompt and "## backlog" in spec.system_prompt


def test_agent_commands_require_init(repo_dir: Path) -> None:
    result = CliRunner().invoke(app, ["implement", "3"])
    assert result.exit_code == 1
    assert "factory init" in result.output
