"""`factory` CLI entry point. Every command except `new` runs against the git repo in the working directory."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Annotated, NoReturn, Optional

import typer

from sw_factory import __version__
from sw_factory.config import ConfigError, FactoryConfig, load_agent, load_config
from sw_factory.github import GitHub, GitHubError
from sw_factory.models import DEFAULT_MODELS, ModelChoice, ModelError, list_models, resolve_model
from sw_factory.project import (
    Aborted,
    Milestone,
    ProjectError,
    ProjectPlan,
    create_project,
    interview,
    load_plan,
    make_plan,
    save_plan,
)
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
ModelOpt = Annotated[
    Optional[str],
    typer.Option("--model", "-m", help="Model for this run: an ID, an alias (opus, sonnet, haiku, fable), or '?' to "
                                       "pick from a list. See `factory models`."),
]
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

SKILLS_README = """\
# Project-specific skills

Lessons that apply only to this project, one folder per agent: `skills/<agent>/<topic>.md`.
They are added after the factory's shared skills for that agent and take precedence over them.

General lessons that would help every project belong in the factory (sw-factory's `prompts/skills/`) instead.
To replace an agent's base prompt for this project only, add `.factory/agents/<agent>.md`.
"""


def write_factory_dir(root: Path) -> Path:
    """Create .factory/ in a repo root: project config and a place for project-specific skills."""
    fdir = root / FACTORY_DIR
    (fdir / "skills").mkdir(parents=True, exist_ok=True)
    (fdir / "config.yaml").write_text(CONFIG_TEMPLATE)
    (fdir / ".gitignore").write_text(GITIGNORE_TEMPLATE)
    (fdir / "skills" / "README.md").write_text(SKILLS_README)
    return fdir


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg="red", err=True)
    raise typer.Exit(1)


def _config(repo: RepoContext) -> FactoryConfig:
    try:
        return load_config(repo)
    except ConfigError as e:
        _fail(str(e))


def _pick_model(runtime: str, models: list[ModelChoice]) -> str:
    default = DEFAULT_MODELS.get(runtime)
    typer.secho(f"{runtime} models:", bold=True)
    for n, m in enumerate(models, 1):
        aliases = f" ({', '.join(m.aliases)})" if m.aliases else ""
        description = f"  {m.description}" if m.description else ""
        typer.echo(f"  {n}. {m.id}{aliases}{description}{'  [default]' if m.id == default else ''}")
    default_n = next((n for n, m in enumerate(models, 1) if m.id == default), 1)
    choice = typer.prompt("Model number", default=str(default_n))
    while not (choice.strip().isdigit() and 1 <= int(choice) <= len(models)):
        choice = typer.prompt(f"{choice!r} is not a number from 1 to {len(models)}. Model number", default=str(default_n))
    return models[int(choice) - 1].id


def _choose_model(runtime: str, model: str | None, cwd: Path) -> str | None:
    """Expand aliases, validate, or run the picker for `-m ?`. Exits with the valid choices on an unknown model."""
    try:
        resolved, warnings = asyncio.run(resolve_model(runtime, model, cwd, lambda ms: _pick_model(runtime, ms)))
    except ModelError as e:
        _fail(str(e))
    for warning in warnings:
        typer.secho(f"Warning: {warning}", fg="yellow", err=True)
    return resolved


def _model_for(
    agent: str, runtime: Runtime | None, model: str | None, config: FactoryConfig, cwd: Path
) -> tuple[str, str | None]:
    """The runtime and model an agent will run on, after CLI options, config.yaml and the model checks."""
    resolved_runtime, resolved_model = config.resolve(agent, runtime.value if runtime else None, model)
    return resolved_runtime, _choose_model(resolved_runtime, resolved_model, cwd)


def _show_plan(plan: ProjectPlan, plan_file: Path) -> None:
    typer.secho(f"\nPlan for {plan.repo_name} ({'private' if plan.private else 'public'})", bold=True)
    typer.echo(f"  {plan.description}")
    typer.echo(f"  PRODUCT.md: {len(plan.product_md.splitlines())} lines, TECH.md: {len(plan.tech_md.splitlines())} lines")
    for milestone in plan.milestones or [Milestone("(no milestone)")]:
        issues = [i for i in plan.issues if (i.milestone or "(no milestone)") == milestone.title]
        typer.secho(f"\n  {milestone.title}", bold=True)
        for issue in issues:
            labels = f"  [{', '.join(issue.labels)}]" if issue.labels else ""
            decision = "  (needs your decision)" if issue.needs_human else ""
            typer.echo(f"    - {issue.title}{labels}{decision}")
    unassigned = [i for i in plan.issues if i.milestone is None] if plan.milestones else []
    for issue in unassigned:
        typer.echo(f"    - {issue.title}  (no milestone)")
    typer.echo(f"\nFull plan (docs and issue bodies): {plan_file}")


@app.command()
def new(
    idea: Annotated[Optional[str], typer.Argument(help="Your idea in a sentence, to start the interview.")] = None,
    from_plan: Annotated[
        Optional[Path], typer.Option("--from-plan", help="Skip the interview and create from a saved plan JSON file.")
    ] = None,
    directory: Annotated[Path, typer.Option("--dir", help="Where to create the project folder.")] = Path("."),
    runtime: RuntimeOpt = None,
    model: ModelOpt = None,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
) -> None:
    """Project agent: interview you, plan PRODUCT.md/TECH.md and a backlog, then create the repo and issues."""
    parent = directory.resolve()
    try:
        find_repo(parent)
        typer.secho(f"Warning: {parent} is inside a git repo; the new project will be nested in it.", fg="yellow")
        if not yes and not typer.confirm("Continue?", default=False):
            raise typer.Exit(1)
    except NotInRepoError:
        pass

    try:
        if from_plan:
            plan, plan_file = load_plan(from_plan), from_plan
        else:
            plan, plan_file = _plan_interactively(idea, parent, runtime, model)
    except (Aborted, KeyboardInterrupt):
        typer.echo("\nStopped. Nothing was created.")
        raise typer.Exit(1)
    except (ProjectError, ConfigError) as e:
        _fail(str(e))
    except RuntimeUnavailable as e:
        _fail(f"runtime unavailable: {e}")

    if dry_run:
        if from_plan:  # the interactive path has already shown it
            _show_plan(plan, plan_file)
    elif not yes and not typer.confirm(
        f"\nCreate {plan.repo_name} locally and on GitHub with {len(plan.issues)} issues?", default=True
    ):
        typer.echo(f"Not created. Resume later with: factory new --from-plan {plan_file}")
        raise typer.Exit(1)

    try:
        slug = create_project(plan, parent, GitHub(), write_factory_dir, typer.echo, dry_run=dry_run)
    except (ProjectError, GitHubError) as e:
        _fail(f"{e}\nThe plan is saved; fix the problem and resume with: factory new --from-plan {plan_file}")
    if slug:
        typer.secho(f"\nDone: https://github.com/{slug}", fg="green")
        typer.echo(f"Next: cd {parent / plan.repo_name} && factory status")


def _plan_interactively(
    idea: str | None, parent: Path, runtime: Runtime | None, model: str | None
) -> tuple[ProjectPlan, Path]:
    resolved_runtime, resolved_model = _model_for("project", runtime, model, FactoryConfig(), parent)
    spec = load_agent(None, "project", FactoryConfig(), resolved_runtime, resolved_model)
    if resolved_model:
        typer.secho(f"Using {resolved_runtime} / {resolved_model}", dim=True)
    agent = get_runtime(spec.runtime)
    typer.secho("Project interview. Answer each question; type /done to wrap up early, /quit to stop.\n", dim=True)

    def ask(question: str) -> str:
        typer.secho(f"\n{question}", fg="cyan")
        return typer.prompt("you", prompt_suffix="> ")

    turns = asyncio.run(interview(agent, spec, parent, ask, idea))
    typer.secho(f"\nSummary:\n{turns[-1].text}\n", fg="cyan")
    typer.echo("Writing the plan...")
    plan = asyncio.run(make_plan(agent, spec, parent, turns))
    while True:
        plan_file = parent / f"{plan.repo_name}.plan.json"
        save_plan(plan, plan_file)
        _show_plan(plan, plan_file)
        choice = typer.prompt("\n[c]reate, [r]evise with feedback, or [q]uit (plan stays saved)", default="c")
        while (choice := choice.strip().lower()) not in ("c", "r", "q"):
            choice = typer.prompt(f"{choice!r} is not one of c, r, q. Choose", default="c")
        if choice == "c":
            return plan, plan_file
        if choice == "q":
            typer.echo(f"Saved. Edit it if you like, then: factory new --from-plan {plan_file}")
            raise typer.Exit(0)
        feedback = typer.prompt("What should change?")
        typer.echo("Revising the plan...")
        plan = asyncio.run(make_plan(agent, spec, parent, turns, previous=plan, feedback=feedback))


@app.command()
def init(
    force: Annotated[bool, typer.Option(help="Overwrite an existing config.yaml.")] = False,
    labels: Annotated[bool, typer.Option(help="Create the factory labels on the GitHub repo.")] = True,
) -> None:
    """Add .factory/ (project config + project-specific skills) to the current repo and create GitHub labels."""
    repo = _repo(require_factory=False)
    config = repo.factory_dir / "config.yaml"
    if config.exists() and not force:
        typer.secho(f"{config} already exists (use --force to overwrite).", fg="yellow")
        raise typer.Exit(1)
    typer.secho(f"Initialized {write_factory_dir(repo.root)}", fg="green")
    if labels and repo.slug:
        try:
            GitHub().ensure_labels(repo.slug)
            typer.echo(f"Created factory labels on {repo.slug}")
        except (GitHubError, FileNotFoundError) as e:
            typer.secho(f"Couldn't create GitHub labels ({e}); run `factory init --force` later.", fg="yellow")


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
        # Copilot is optional (experimental) unless you explicitly ask to check it.
        optional = runtime is None and name == "copilot"
        try:
            resolved_model, warnings = asyncio.run(
                resolve_model(name, model, cwd, lambda ms, name=name: _pick_model(name, ms))
            )
        except ModelError as e:
            problems += not optional
            report(None if optional else False, f"runtime {name}", str(e))
            continue
        for warning in warnings:
            typer.secho(f"Warning: {warning}", fg="yellow")
        spec = AgentSpec(name="doctor", system_prompt="You are a connectivity check.", runtime=name,
                         model=resolved_model, access="read", timeout_s=180)
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
def models(
    runtime: Annotated[Optional[Runtime], typer.Option("--runtime", "-r", help="Only this runtime.")] = None,
) -> None:
    """List the models each runtime offers (with aliases) and which model each agent uses in this repo."""
    try:
        repo: RepoContext | None = find_repo()
    except NotInRepoError:
        repo = None
    cwd = repo.root if repo else Path.cwd()

    for name in [runtime.value] if runtime else list(RUNTIMES):
        typer.secho(f"{name}", bold=True)
        try:
            available = asyncio.run(list_models(name, cwd))
        except RuntimeUnavailable as e:
            typer.secho(f"  unavailable: {e}", fg="yellow")
            continue
        default = DEFAULT_MODELS.get(name)
        for m in available:
            aliases = f"  ({', '.join(m.aliases)})" if m.aliases else ""
            description = f"  {m.description}" if m.description else ""
            typer.echo(f"  {m.id}{aliases}{description}{'  [default]' if m.id == default else ''}")

    if repo and repo.has_factory:
        config = _config(repo)
        typer.secho(f"\nAgents in {repo.root.name} (.factory/config.yaml)", bold=True)
        for agent, settings in config.agents.items():
            agent_runtime, agent_model = config.resolve(agent)
            shown = agent_model or f"default ({DEFAULT_MODELS.get(agent_runtime) or 'runtime picks'})"
            typer.echo(f"  {agent:<10} {agent_runtime:<8} {shown:<40} {settings.level}")
    typer.secho("\nPick per run with -m <id or alias>, or -m ? for a list.", dim=True)


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
    repo = _repo()
    runtime_name, model_id = _model_for("triage", runtime, model, _config(repo), repo.root)
    _not_implemented("triage", repo, issue=issue, runtime=runtime_name, model=model_id, dry_run=dry_run, yes=yes)


@app.command()
def spec(
    issue: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Write a product spec + tech spec for an issue and open it as a PR for your review."""
    repo = _repo()
    runtime_name, model_id = _model_for("spec", runtime, model, _config(repo), repo.root)
    _not_implemented("spec", repo, issue=issue, runtime=runtime_name, model=model_id, dry_run=dry_run, yes=yes)


@app.command()
def implement(
    issue: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Implement an issue in its own worktree on branch factory/<issue> and open a PR."""
    repo = _repo()
    runtime_name, model_id = _model_for("implement", runtime, model, _config(repo), repo.root)
    _not_implemented("implement", repo, issue=issue, runtime=runtime_name, model=model_id, dry_run=dry_run, yes=yes)


@app.command()
def fix(
    pr: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Fix loop: read the review comments on a PR and push fixes."""
    repo = _repo()
    runtime_name, model_id = _model_for("fix", runtime, model, _config(repo), repo.root)
    _not_implemented("fix", repo, pr=pr, runtime=runtime_name, model=model_id, dry_run=dry_run, yes=yes)


@app.command()
def review(
    pr: int, runtime: RuntimeOpt = None, model: ModelOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Review a PR, post comments, and flag whether it needs human review."""
    repo = _repo()
    runtime_name, model_id = _model_for("review", runtime, model, _config(repo), repo.root)
    _not_implemented("review", repo, pr=pr, runtime=runtime_name, model=model_id, dry_run=dry_run, yes=yes)


# --- Self-improvement & metrics -----------------------------------------------------------------------------


@app.command()
def learn(
    agent: Annotated[Optional[str], typer.Option(help="Only learn for this agent.")] = None,
    runtime: RuntimeOpt = None,
    model: ModelOpt = None,
    dry_run: DryRunOpt = False,
) -> None:
    """Learning loop: mine PR reviews/comments for recurring corrections and propose skill edits as a PR."""
    repo = _repo()
    runtime_name, model_id = _model_for("learn", runtime, model, _config(repo), repo.root)
    _not_implemented("learn", repo, agent=agent, runtime=runtime_name, model=model_id, dry_run=dry_run)


@app.command()
def stats(agent: Annotated[Optional[str], typer.Option(help="Only show this agent.")] = None) -> None:
    """Per-agent metrics: correction rate, merged-without-changes %, cost per run."""
    _not_implemented("stats", _repo(), agent=agent)
