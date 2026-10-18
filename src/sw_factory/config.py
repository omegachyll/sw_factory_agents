"""Build an AgentSpec from the factory's shared agents/skills plus the project's own config and skills.

Two layers:
- Factory (ships with sw-factory, shared by every project): prompts/agents/<name>.md and prompts/skills/<name>/*.md.
  Improving these improves every project.
- Project (.factory/ in the repo): config.yaml, skills/<name>/*.md (project-specific lessons, applied after the
  factory's so they win), and optionally agents/<name>.md to replace the factory's base prompt entirely.

Precedence for runtime/model: CLI option > config.yaml `agents.<name>` > config.yaml defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from sw_factory.repo import RepoContext
from sw_factory.runtimes import RUNTIMES, AgentSpec

LEVELS = ("L0", "L1", "L2", "L3")
ACCESS = ("none", "read", "write")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AgentSettings:
    runtime: str | None = None
    model: str | None = None
    level: str = "L0"


@dataclass(frozen=True)
class FactoryConfig:
    default_runtime: str = "claude"
    default_model: str | None = None
    agents: dict[str, AgentSettings] = field(default_factory=dict)

    def agent(self, name: str) -> AgentSettings:
        return self.agents.get(name, AgentSettings())

    def resolve(self, name: str, runtime: str | None = None, model: str | None = None) -> tuple[str, str | None]:
        settings = self.agent(name)
        resolved_runtime = runtime or settings.runtime or self.default_runtime
        # A model configured for one runtime rarely exists on the other, so drop configured models on a runtime switch.
        configured_model = settings.model or self.default_model
        if runtime and runtime != (settings.runtime or self.default_runtime):
            configured_model = None
        return resolved_runtime, model or configured_model


def _check_runtime(value: Any, where: str) -> None:
    if value is not None and value not in RUNTIMES:
        raise ConfigError(f"{where}: runtime must be one of {', '.join(RUNTIMES)}, got {value!r}")


def parse_config(data: Any, source: str = "config.yaml") -> FactoryConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: expected a mapping at the top level")
    _check_runtime(data.get("default_runtime"), source)
    agents: dict[str, AgentSettings] = {}
    for name, raw in (data.get("agents") or {}).items():
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{source}: agents.{name} must be a mapping")
        _check_runtime(raw.get("runtime"), f"{source}: agents.{name}")
        level = raw.get("level", "L0")
        if level not in LEVELS:
            raise ConfigError(f"{source}: agents.{name}.level must be one of {', '.join(LEVELS)}, got {level!r}")
        agents[name] = AgentSettings(runtime=raw.get("runtime"), model=raw.get("model"), level=level)
    return FactoryConfig(
        default_runtime=data.get("default_runtime") or "claude",
        default_model=data.get("default_model"),
        agents=agents,
    )


def load_config(repo: RepoContext) -> FactoryConfig:
    path = repo.factory_dir / "config.yaml"
    if not path.exists():
        return FactoryConfig()
    try:
        return parse_config(yaml.safe_load(path.read_text()), str(path))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML ({e})") from e


def parse_agent_file(text: str, source: str) -> tuple[dict[str, Any], str]:
    """Split an agent file into its YAML frontmatter and prompt body."""
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ConfigError(f"{source}: unterminated frontmatter")
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{source}: invalid frontmatter ({e})") from e
    if not isinstance(meta, dict):
        raise ConfigError(f"{source}: frontmatter must be a mapping")
    return meta, parts[2].strip()


FACTORY_PROMPTS = Path(str(files("sw_factory") / "prompts"))


def _skill_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def _skills_section(title: str, paths: list[Path]) -> str:
    return "\n\n".join([f"# {title}"] + [f"## {p.stem}\n\n{p.read_text().strip()}" for p in paths])


def load_agent(
    repo: RepoContext | None,
    name: str,
    config: FactoryConfig,
    runtime: str | None = None,
    model: str | None = None,
    factory_prompts: Path = FACTORY_PROMPTS,
) -> AgentSpec:
    """Compose an agent: base prompt (project override or factory) + factory skills + project skills."""
    override = repo.factory_dir / "agents" / f"{name}.md" if repo else None
    base = override if override and override.exists() else factory_prompts / "agents" / f"{name}.md"
    if not base.exists():
        raise ConfigError(f"unknown agent {name!r}: no {base}")
    meta, prompt = parse_agent_file(base.read_text(), str(base))
    if not prompt:
        raise ConfigError(f"{base}: prompt is empty")
    access = meta.get("access", "read")
    if access not in ACCESS:
        raise ConfigError(f"{base}: access must be one of {', '.join(ACCESS)}, got {access!r}")

    sections = [prompt]
    if factory_skills := _skill_files(factory_prompts / "skills" / name):
        sections.append(_skills_section("Skills", factory_skills))
    if repo and (project_skills := _skill_files(repo.factory_dir / "skills" / name)):
        sections.append(_skills_section(
            "Project-specific skills (these take precedence over the general skills above)", project_skills
        ))

    resolved_runtime, resolved_model = config.resolve(name, runtime, model)
    return AgentSpec(
        name=name, system_prompt="\n\n".join(sections), runtime=resolved_runtime, model=resolved_model, access=access
    )
