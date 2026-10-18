"""Model selection: aliases, validation, the `-m ?` picker and `factory models`."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sw_factory.cli import app
from sw_factory.models import CLAUDE_MODELS, ModelChoice, ModelError, alias_target, resolve_model
from sw_factory.runtimes import RuntimeUnavailable


def never_pick(models: list[ModelChoice]) -> str:
    raise AssertionError("picker should not run")


async def copilot_models(runtime: str, cwd: Path) -> list[ModelChoice]:
    return [ModelChoice("gpt-x"), ModelChoice("claude-sonnet-x")]


async def copilot_unavailable(runtime: str, cwd: Path) -> list[ModelChoice]:
    raise RuntimeUnavailable("Copilot is not authenticated")


def test_alias_target() -> None:
    assert alias_target("claude", "sonnet") == "claude-sonnet-5-5"
    assert alias_target("claude", "HAIKU") == "claude-haiku-4-5-20251001"
    assert alias_target("copilot", "sonnet") == "sonnet"  # aliases are Claude-only


@pytest.mark.parametrize(
    "name, expected, warned",
    [
        (None, None, False),
        ("opus", "claude-opus-5-5", False),
        ("claude-sonnet-5-5", "claude-sonnet-5-5", False),
        ("claude-future-9", "claude-future-9", True),  # unknown claude-* passes with a warning
    ],
)
async def test_resolve_claude(tmp_path: Path, name: str | None, expected: str | None, warned: bool) -> None:
    model, warnings = await resolve_model("claude", name, tmp_path, never_pick)
    assert model == expected and bool(warnings) == warned


async def test_resolve_claude_rejects_unknown(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="claude-opus-5-5 \\(opus\\)"):
        await resolve_model("claude", "gpt-9", tmp_path, never_pick)


async def test_resolve_copilot(tmp_path: Path) -> None:
    assert await resolve_model("copilot", "gpt-x", tmp_path, never_pick, copilot_models) == ("gpt-x", [])
    with pytest.raises(ModelError, match="gpt-x"):
        await resolve_model("copilot", "nope", tmp_path, never_pick, copilot_models)
    # Without a working Copilot setup the name can't be checked, so it passes through with a warning.
    model, warnings = await resolve_model("copilot", "nope", tmp_path, never_pick, copilot_unavailable)
    assert model == "nope" and "couldn't check" in warnings[0]


async def test_resolve_pick(tmp_path: Path) -> None:
    model, _ = await resolve_model("claude", "?", tmp_path, lambda models: models[1].id)
    assert model == CLAUDE_MODELS[1].id
    with pytest.raises(ModelError, match="can't list copilot models"):
        await resolve_model("copilot", "?", tmp_path, never_pick, copilot_unavailable)


@pytest.fixture
def factory_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init", "--no-labels"])
    return tmp_path


def test_cli_picker_reprompts_until_valid(factory_repo: Path) -> None:
    result = CliRunner().invoke(app, ["implement", "1", "-r", "claude", "-m", "?"], input="x\n9\n2\n")
    assert "1. claude-opus-5-5 (opus)" in result.output and "[default]" in result.output
    assert result.output.count("is not a number from 1 to 4") == 2
    assert "model: claude-sonnet-5-5" in result.output  # resolved model reaches the command


def test_cli_alias_and_unknown_model(factory_repo: Path) -> None:
    result = CliRunner().invoke(app, ["implement", "1", "-r", "claude", "-m", "haiku"])
    assert "model: claude-haiku-4-5-20251001" in result.output
    bad = CliRunner().invoke(app, ["implement", "1", "-r", "claude", "-m", "gpt-9"])
    assert bad.exit_code == 1 and "unknown Claude model 'gpt-9'" in bad.output


def test_cli_alias_in_config(factory_repo: Path) -> None:
    config = factory_repo / ".factory" / "config.yaml"
    config.write_text(config.read_text().replace("implement: {level: L0}", "implement: {level: L0, model: sonnet}"))
    result = CliRunner().invoke(app, ["implement", "1"])
    assert "model: claude-sonnet-5-5" in result.output


def test_cli_models(factory_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sw_factory.cli.list_models", copilot_unavailable_for_copilot)
    result = CliRunner().invoke(app, ["models"])
    assert result.exit_code == 0, result.output
    assert "claude-sonnet-5-5  (sonnet)" in result.output
    assert "unavailable: Copilot is not authenticated" in result.output
    assert "triage" in result.output and "default (claude-opus-5-5)" in result.output  # agent table in a factory repo


def test_cli_models_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["models", "-r", "claude"])
    assert result.exit_code == 0 and "claude-opus-5-5" in result.output and "Agents in" not in result.output


async def copilot_unavailable_for_copilot(runtime: str, cwd: Path) -> list[ModelChoice]:
    if runtime == "copilot":
        raise RuntimeUnavailable("Copilot is not authenticated")
    return list(CLAUDE_MODELS)
