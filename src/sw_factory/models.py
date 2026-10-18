"""Model selection: aliases, the list of models per runtime, and validating a model name before a run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from sw_factory.runtimes import RuntimeUnavailable
from sw_factory.runtimes.claude import DEFAULT_MODEL as CLAUDE_DEFAULT

PICK = "?"  # `-m ?` opens the picker


class ModelError(Exception):
    pass


@dataclass
class ModelChoice:
    id: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)


# The Claude Agent SDK has no API to list models, so the factory keeps the list.
CLAUDE_MODELS = [
    ModelChoice("claude-opus-5-5", "most capable; best for planning and review", ["opus"]),
    ModelChoice("claude-sonnet-5-5", "faster and cheaper; good for most implementation", ["sonnet"]),
    ModelChoice("claude-fable-5-1", "", ["fable"]),
    ModelChoice("claude-haiku-4-5-20251001", "fastest and cheapest; simple checks", ["haiku"]),
]

DEFAULT_MODELS: dict[str, str | None] = {"claude": CLAUDE_DEFAULT, "copilot": None}  # None: Copilot picks


def alias_target(runtime: str, name: str) -> str:
    """Expand an alias such as `sonnet` to a full model ID; other names are returned unchanged."""
    if runtime == "claude":
        for model in CLAUDE_MODELS:
            if name.lower() in model.aliases:
                return model.id
    return name


async def list_models(runtime: str, cwd: Path) -> list[ModelChoice]:
    """Models available on a runtime. Raises RuntimeUnavailable if they can't be listed (e.g. Copilot not set up)."""
    if runtime == "claude":
        return list(CLAUDE_MODELS)
    if runtime == "copilot":
        from sw_factory.runtimes.copilot import CopilotRuntime

        return [ModelChoice(id, name if name != id else "") for id, name in await CopilotRuntime().list_models(cwd)]
    raise ModelError(f"unknown runtime {runtime!r}")


Lister = Callable[[str, Path], Awaitable[list[ModelChoice]]]


async def resolve_model(
    runtime: str,
    name: str | None,
    cwd: Path,
    pick: Callable[[list[ModelChoice]], str],
    lister: Lister = list_models,
) -> tuple[str | None, list[str]]:
    """Turn what the person typed (or config says) into a model ID for `runtime`. Returns (model, warnings).

    None means the runtime's default. `?` asks `pick` to choose from the list. Unknown names raise ModelError.
    """
    if name is None:
        return None, []
    if name == PICK:
        try:
            return pick(await lister(runtime, cwd)), []
        except RuntimeUnavailable as e:
            raise ModelError(f"can't list {runtime} models to pick from: {e}") from e

    model = alias_target(runtime, name)
    if runtime == "claude":
        if any(m.id == model for m in CLAUDE_MODELS):
            return model, []
        if model.startswith("claude-"):  # new models ship before the factory's list is updated
            return model, [f"{model!r} isn't in the factory's list of Claude models; using it anyway"]
        raise ModelError(f"unknown Claude model {name!r}. {describe(CLAUDE_MODELS)}")

    try:
        models = await lister(runtime, cwd)
    except RuntimeUnavailable as e:
        return model, [f"couldn't check {model!r} against the {runtime} model list ({e}); using it anyway"]
    if any(m.id == model for m in models):
        return model, []
    raise ModelError(f"unknown {runtime} model {name!r}. {describe(models)}")


def describe(models: list[ModelChoice]) -> str:
    names = [f"{m.id} ({', '.join(m.aliases)})" if m.aliases else m.id for m in models]
    return "Choose one of: " + ", ".join(names) + ". See `factory models`."
