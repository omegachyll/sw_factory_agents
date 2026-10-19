# sw-factory

A **self-improving software factory** as a CLI. You run `factory` inside a git repo, and AI agents work that repo's
GitHub issues: triage, spec, implement, review and fix. They learn from your PR reviews and comments.

Inspired by Zach Lloyd's talk [*Self-improving software factories*](https://youtu.be/tUPPVhBBcoM). The talk's idea is that
software engineering becomes *factory engineering*: you build the thing that builds the product.

> **Status: early.** `factory new` and `factory project` (the project agent), `factory triage`, `factory init`, `factory models` and
> `factory doctor` work, and agents run on the Claude Agent SDK and, experimentally, the Copilot SDK. The other
> build-loop agents (`spec`, `implement`, `review`, `fix`) and `learn`/`stats` are still stubs. See [TODO.md](TODO.md).

## How it works

```
GitHub issue ─► triage ─► spec (you review) ─► implement ─► review ─► you merge
                                                   ▲            │
                                                   └── fix ◄────┘  (your PR comments)

                     your corrections ─► learn ─► skill-edit PR ─► better agents next run
```

- **Agents are triggered manually** to start with. Each agent moves up its own maturity ladder independently as it
  proves itself (see below).
- **Two feedback loops.** *Fix* corrects a single PR from your review comments. *Learn* mines feedback across many
  PRs for recurring corrections and proposes edits to the agents' skill files. You approve those edits as a normal PR.
- **Two agent runtimes.** Each agent can run on the **Claude Agent SDK** or the **GitHub Copilot SDK**. You can pick
  per agent in config or per run with `--runtime`. A useful setup is to build with one runtime and review with the
  other. **The Copilot runtime is experimental:** it's type-checked against the SDK and tested with a fake client,
  but hasn't yet been run end to end against a real Copilot subscription.
- **Two layers: the factory and the project.** The agents and their general skills ship with sw-factory, so improving
  them improves every project. Each project's `.factory/` holds its config and *project-specific* skills, which are
  added after the factory's and take precedence. See [Agents and skills](#agents-and-skills).

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), git and the [GitHub CLI](https://cli.github.com/) (`gh auth login`).

```bash
uv tool install -e /path/to/sw_factory_agents                # puts `factory` on your PATH (Claude runtime)
uv tool install -e '/path/to/sw_factory_agents[copilot]'     # ...plus the experimental Copilot runtime
factory doctor                                               # check git, gh login, and each runtime
```

- **Claude runtime:** uses your Claude Code login, or `ANTHROPIC_API_KEY`.
- **Copilot runtime:** needs a GitHub Copilot subscription. The Copilot CLI downloads itself on first use.

Reinstall with `--force` after changing `pyproject.toml`.

For development: `uv sync --all-extras`, then `uv run pytest` and `uv run pyright`.

## Usage

```bash
factory new ["my idea"]           # project agent: interview → plan → new repo + GitHub repo → starting issues
factory new --from-plan my-app.plan.json   # create from a saved (and optionally hand-edited) plan
cd my-app
factory init                      # add .factory/ to an existing repo
factory project ["suggestions"]   # project agent: revise PRODUCT.md, TECH.md and the open backlog
factory doctor [--runtime ...]    # check setup: git, gh login, a test prompt on each runtime
factory status                    # issues/PRs by stage, agent maturity levels
factory models [--runtime ...]    # models per runtime (with aliases) and which model each agent uses

factory triage    <issue>         # ready (with a build plan) / needs-human / needs-spec / blocked, as an issue comment
factory spec      <issue>
factory implement <issue>         # worktree + branch factory/<issue>, opens a PR
factory review    <pr>
factory fix       <pr>            # apply your review comments

factory learn [--agent implement] # propose skill edits from accumulated feedback
factory stats [--agent ...]
```

Options shared by every agent command: `--runtime claude|copilot`, `--model`, `--dry-run` (show what would be posted or
pushed without doing it) and `--yes` (skip confirmation).

Picking a model: `--model`/`-m` takes a full ID, an alias (`opus`, `sonnet`, `haiku`, `fable`) or `?` to choose from a
numbered list, e.g. `factory triage 3 -m sonnet` or `factory new -m ?`. Unknown names are rejected before anything runs.
Set a lasting choice per agent in `.factory/config.yaml` (`triage: {level: L0, model: sonnet}`); `-m` overrides it.

Every command except `new` acts on the git repo containing the current directory. The GitHub repo is read from the
`origin` remote.

### Triage: `factory triage <issue>`

Decides whether an issue can be built now and, if so, writes the build plan the implement agent will follow:
approach, files, steps, tests, how each acceptance criterion is met, and assumptions. The agent reads the issue, its
comments, PRODUCT.md/TECH.md and the repository (read-only).

- Issues whose `Depends on: #N` issues are still open are marked `factory:blocked` without calling a model.
- You see the comment first; it's posted (and the stage label set) only when you confirm. `--dry-run` posts nothing.
- To correct it, reply on the issue and run triage again. It reads your reply, revises, and edits its own comment
  in place, or posts a new one if there are replies after it.

### Changing the plan: `factory project`

Tell the project agent what you'd like to change, in your own words. It asks follow-up questions when something is
ambiguous or conflicts with an earlier decision (in the docs or in an issue comment), then proposes doc changes as a
diff and backlog changes issue by issue. You apply, revise with feedback, or quit; `--dry-run` changes nothing.

On apply, the doc changes go to a PR (made in a temporary worktree, so your checkout isn't touched) and each issue
change is applied according to the issue's stage:

| Issue | What happens |
|---|---|
| Not started | Body updated, with a comment explaining why |
| Triaged (`factory:ready`) | Body updated and moved back to `factory:new`: its build plan is out of date |
| In progress / in review | Not edited. Flagged `factory:needs-human` with the proposed change, for you to decide |
| No longer needed | Closed as superseded, with a comment |
| Closed (already built) | Never touched; changes to built code become new issues |

### Starting a project: `factory new`

1. **Interview.** The project agent asks one question at a time: problem and users, v1 scope, non-goals, platform,
   stack, data, auth, constraints, what "done" means, and the repo name. Answer `/done` to wrap up early or `/quit`
   to stop.
2. **Plan.** It writes PRODUCT.md, TECH.md and a backlog of small issues in build order, with acceptance criteria and
   milestones. The plan is saved to `<repo>.plan.json` next to where you ran the command. You can **create** it,
   **revise** it with feedback, or **quit** and hand-edit the JSON, then resume with `--from-plan`.
3. **Create.** It makes the folder with README/PRODUCT/TECH and `.factory/`, commits, creates the GitHub repo
   (private unless you chose public), pushes, creates the factory labels and milestones, and opens every issue
   labelled `factory:new`. `--dry-run` shows all of this without doing any of it.

## Agents and skills

```
sw-factory (shared by every project)          my-app/.factory/ (this project only)
  src/sw_factory/prompts/                       config.yaml          runtime, model, maturity level per agent
    agents/<agent>.md   base role prompt        skills/<agent>/*.md  project-specific lessons (win over general)
    skills/<agent>/*.md general skills          agents/<agent>.md    optional: replace the base prompt here
                                                runs.db              gitignored: run log + feedback
my-app.worktrees/  (next to the repo)          one checkout per issue the agents work in
```

An agent's prompt is its base prompt, then the factory's skills for it, then the project's skills for it. The
`agents/<agent>.md` frontmatter sets its access: `none` (conversation only), `read` (read and search files) or
`write` (also edit files and run commands).

Lessons go to one of the two layers. A general lesson ("keep PRs small") becomes a PR on sw-factory and reaches every
project. A project lesson ("this app uses pnpm") becomes a PR on that project's `.factory/skills/`. Because a change to
the shared layer reaches every project at once, each agent output records which factory version produced it, so
regressions show up in `factory stats`.

## Feedback the agents learn from

The signals, ordered from strongest to weakest:

1. Commits you push on top of an agent's branch (the gap between what the agent wrote and what was merged)
2. PR review comments and review state (approved / changes requested)
3. PRs closed without merging
4. Your edits to agent-written specs
5. Your answers to agent questions on issues

Everything an agent produces carries a hidden footer, for example
`<!-- factory: agent=implement run=42 factory=<sha> project-skills=<sha> -->`, so feedback can be traced back to the
agent and the skill versions that produced it. Start a comment with `rule:` to mark a general lesson for the factory,
or `rule(project):` for a lesson that only applies to this project. Comments without either are treated as fixes for
that one PR.

## Maturity ladder (per agent)

| Level | How it runs | Move up when… |
|---|---|---|
| L0 Manual | you run it and check every output | its output usually needs only small fixes |
| L1 Approve each action | shows what it will post or push; you confirm | e.g. the last 10 runs needed no rework |
| L2 Auto + human gate | triggered by a label or comment; you approve the merge | correction rate stays low for weeks |
| L3 Fully automatic | runs end to end; you only see what fails | — |

## Project layout

```
src/sw_factory/
  cli.py       # Typer app and commands
  repo.py      # find the repo root + GitHub owner/name from the working directory
  config.py    # compose an agent from factory prompts/skills + project config/skills
  project.py   # project agent: interview, plan, create repo/labels/milestones/issues
  github.py    # thin `gh` wrapper; factory labels
  prompts/     # the shared layer: agents/<agent>.md and skills/<agent>/*.md
  runtimes/
    base.py    # AgentSpec / AgentResult / AgentRuntime: the runtime-neutral interface
    claude.py  # Claude Agent SDK adapter
    copilot.py # GitHub Copilot SDK adapter (experimental)
tests/         # pytest; SDK adapters tested against stand-ins built from the SDKs' real types
```

## License

**Source-available, noncommercial.** Copyright (c) 2026 Ajay Gowda.

sw-factory is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify and share it for
noncommercial purposes. **Commercial use requires prior written permission**, which can be revoked at any time. See
[COMMERCIAL.md](COMMERCIAL.md) for how to ask.

Because the license restricts commercial use, sw-factory is not "open source" as the Open Source Initiative defines
it.
