"""`factory` CLI entry point. Every command except `new` runs against the git repo in the working directory."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer

from sw_factory import __version__
from sw_factory.repo import FACTORY_DIR, NotInRepoError, RepoContext, find_repo
from sw_factory.runtimes import RUNTIMES, AgentSpec, RuntimeUnavailable, get_runtime

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
  review:    {level: L0}   # switch to `runtime: copilot` for cross-model review once Copilot is verified
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


DOCTOR_PROMPT = "Reply with exactly the word OK and nothing else."


@app.command()
def doctor(
    runtime: Annotated[
        Optional[Runtime], typer.Option("--runtime", "-r", help="Check only this runtime (default: all).")
    ] = None,
    model: ModelOpt = None,
) -> None:
    """Check the setup: git repo, GitHub CLI login, and a tiny test prompt on each agent runtime."""
    problems = 0

    def report(ok: bool | None, label: str, detail: str) -> None:
        mark, color = {True: ("ok  ", "green"), False: ("FAIL", "red"), None: ("skip", "yellow")}[ok]
        typer.secho(f"[{mark}] ", fg=color, nl=False)
        typer.echo(f"{label}: {detail}")

    try:
        repo: RepoContext | None = find_repo()
        report(True, "git repo", f"{repo.root} ({repo.slug or 'no GitHub origin'})")
    except NotInRepoError:
        repo = None
        report(None, "git repo", "not in a git repo (fine for `factory new`)")

    gh = shutil.which("gh")
    if gh is None:
        problems += 1
        report(False, "GitHub CLI", "gh not installed: brew install gh")
    elif subprocess.run([gh, "auth", "status"], capture_output=True).returncode != 0:
        problems += 1
        report(False, "GitHub CLI", "not logged in: gh auth login")
    else:
        report(True, "GitHub CLI", "logged in")

    cwd = repo.root if repo else Path.cwd()
    for name in [runtime.value] if runtime else list(RUNTIMES):
        spec = AgentSpec(name="doctor", system_prompt="You are a connectivity check.", runtime=name, model=model,
                         access="read", timeout_s=180)
        # Copilot is optional (experimental) unless you explicitly ask to check it.
        optional = runtime is None and name == "copilot"
        try:
            result = asyncio.run(get_runtime(name).run(spec, DOCTOR_PROMPT, cwd))
        except RuntimeUnavailable as e:
            result, error = None, f"unavailable: {e}"
        else:
            error = None if result.ok else f"error: {result.error}"
        if result is None or error:
            problems += not optional
            report(None if optional else False, f"runtime {name}", error or "no result")
            continue
        usage = []
        if result.input_tokens is not None:
            usage.append(f"{result.input_tokens} in / {result.output_tokens} out tokens")
        if result.cost_usd is not None:
            usage.append(f"${result.cost_usd:.4f}")
        usage.append(f"{result.duration_s:.1f}s")
        reply = result.text.strip().replace("\n", " ")[:60]
        report(True, f"runtime {name}", f"{result.model or 'default model'} replied {reply!r} ({', '.join(usage)})")

    raise typer.Exit(1 if problems else 0)


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
