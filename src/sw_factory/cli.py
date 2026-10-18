"""`factory` CLI entry point. Every command except `new` runs against the git repo in the working directory."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Optional

import typer

from sw_factory import __version__
from sw_factory.repo import FACTORY_DIR, NotInRepoError, RepoContext, find_repo

app = typer.Typer(
    help="Self-improving software factory: AI agents that work the GitHub issues of the repo you run it in.",
    no_args_is_help=True,
)


class Runtime(str, Enum):
    claude = "claude"
    copilot = "copilot"


# Options shared by every agent command.
RuntimeOpt = Annotated[
    Optional[Runtime], typer.Option("--runtime", "-r", help="Agent SDK to run on (default: from .factory/config.yaml).")
]
ModelOpt = Annotated[Optional[str], typer.Option("--model", "-m", help="Model override for this run.")]
DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Do the work but only show what would be posted/pushed.")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation before posting/pushing.")]

CONFIG_TEMPLATE = """\
# sw-factory configuration for this repo.
default_runtime: claude      # claude | copilot
default_model: null          # null = runtime's default

# Per-agent overrides and maturity level (L0 manual, L1 approve each action, L2 auto + human gate, L3 fully auto).
agents:
  project:   {level: L0}
  triage:    {level: L0}
  spec:      {level: L0}
  implement: {level: L0}
  fix:       {level: L0}
  review:    {level: L0, runtime: copilot}   # cross-model review: build on one SDK, review on the other
  learn:     {level: L0}

worktrees_dir: ../{repo}.worktrees
"""

GITIGNORE_TEMPLATE = """\
runs.db
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"sw-factory {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit.")
    ] = False,
) -> None:
    pass


def _repo(require_factory: bool = True) -> RepoContext:
    try:
        repo = find_repo()
    except NotInRepoError as e:
        typer.secho(f"Error: {e}. Run factory inside a repo (or use `factory new`).", fg="red", err=True)
        raise typer.Exit(1)
    if require_factory and not repo.has_factory:
        typer.secho(f"Error: no {FACTORY_DIR}/ in {repo.root}. Run `factory init` first.", fg="red", err=True)
        raise typer.Exit(1)
    return repo


def _not_implemented(command: str, repo: RepoContext | None = None, **details: object) -> None:
    typer.secho(f"`factory {command}` is not implemented yet.", fg="yellow")
    if repo:
        typer.echo(f"  repo:   {repo.root}")
        typer.echo(f"  github: {repo.slug or '(no GitHub origin remote)'}")
    for key, value in details.items():
        if value is not None and value is not False:
            typer.echo(f"  {key}: {value.value if isinstance(value, Enum) else value}")
    raise typer.Exit(2)


# --- Setup --------------------------------------------------------------------------------------------------


@app.command()
def new(runtime: RuntimeOpt = None, model: ModelOpt = None) -> None:
    """Project agent: interview you, create the repo + GitHub repo, run init, and open the starting issues."""
    _not_implemented("new", runtime=runtime, model=model)


@app.command()
def init(force: Annotated[bool, typer.Option(help="Overwrite an existing config.yaml.")] = False) -> None:
    """Add a .factory/ folder (config, agents, skills) to the current repo."""
    repo = _repo(require_factory=False)
    fdir = repo.factory_dir
    config = fdir / "config.yaml"
    if config.exists() and not force:
        typer.secho(f"{config} already exists (use --force to overwrite).", fg="yellow")
        raise typer.Exit(1)
    for sub in ("agents", "skills"):
        (fdir / sub).mkdir(parents=True, exist_ok=True)
    config.write_text(CONFIG_TEMPLATE)
    (fdir / ".gitignore").write_text(GITIGNORE_TEMPLATE)
    typer.secho(f"Initialized {fdir}", fg="green")
    typer.echo("TODO: copy default agent/skill templates and create factory labels on GitHub.")


@app.command()
def status() -> None:
    """Show issues/PRs by factory stage and each agent's maturity level."""
    _not_implemented("status", _repo())


# --- Agents -------------------------------------------------------------------------------------------------


@app.command()
def triage(
    issue: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Classify an issue: ready to implement, needs a spec, or needs a human answer."""
    _not_implemented("triage", _repo(), issue=issue, runtime=runtime, model=model, dry_run=dry_run, yes=yes)


@app.command()
def spec(
    issue: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Write a product spec + tech spec for an issue and open it as a PR for your review."""
    _not_implemented("spec", _repo(), issue=issue, runtime=runtime, model=model, dry_run=dry_run, yes=yes)


@app.command()
def implement(
    issue: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Implement an issue in its own worktree on branch factory/<issue> and open a PR."""
    _not_implemented("implement", _repo(), issue=issue, runtime=runtime, model=model, dry_run=dry_run, yes=yes)


@app.command()
def fix(
    pr: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Fix loop: read the review comments on a PR and push fixes."""
    _not_implemented("fix", _repo(), pr=pr, runtime=runtime, model=model, dry_run=dry_run, yes=yes)


@app.command()
def review(
    pr: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Review a PR, post comments, and flag whether it needs human review."""
    _not_implemented("review", _repo(), pr=pr, runtime=runtime, model=model, dry_run=dry_run, yes=yes)


# --- Self-improvement & metrics -----------------------------------------------------------------------------


@app.command()
def learn(
    agent: Annotated[Optional[str], typer.Option(help="Only learn for this agent.")] = None,
    runtime: RuntimeOpt = None,
    model: ModelOpt = None,
    dry_run: DryRunOpt = False,
) -> None:
    """Learning loop: mine PR reviews/comments for recurring corrections and propose skill edits as a PR."""
    _not_implemented("learn", _repo(), agent=agent, runtime=runtime, model=model, dry_run=dry_run)


@app.command()
def stats(agent: Annotated[Optional[str], typer.Option(help="Only show this agent.")] = None) -> None:
    """Per-agent metrics: correction rate, merged-without-changes %, cost per run."""
    _not_implemented("stats", _repo(), agent=agent)
