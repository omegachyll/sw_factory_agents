# sw-factory

A **self-improving software factory** as a CLI. You run `factory` inside a git repo, and AI agents work that repo's
GitHub issues: triage, spec, implement, review and fix. They learn from your PR reviews and comments.

Inspired by Zach Lloyd's talk [*Self-improving software factories*](https://youtu.be/tUPPVhBBcoM). The talk's idea is that
software engineering becomes *factory engineering*: you build the thing that builds the product.

> **Status: early.** The CLI shell, `factory init`, `factory doctor` and the agent runtime layer work: agents can
> run on the Claude Agent SDK, and on the Copilot SDK (experimental, see below). The agent commands themselves are
> still stubs. See [TODO.md](TODO.md).

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
- **The factory lives in the product repo.** Agents, skills and config are stored in `.factory/` and versioned with the
  code, so every project ends up with a factory tuned to it.

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
factory new                       # project agent: interview → new repo + GitHub repo → .factory/ → starting issues
cd my-app
factory init                      # add .factory/ to an existing repo
factory doctor [--runtime ...]    # check setup: git, gh login, a test prompt on each runtime
factory status                    # issues/PRs by stage, agent maturity levels

factory triage    <issue>
factory spec      <issue>
factory implement <issue>         # worktree + branch factory/<issue>, opens a PR
factory review    <pr>
factory fix       <pr>            # apply your review comments

factory learn [--agent implement] # propose skill edits from accumulated feedback
factory stats [--agent ...]
```

Options shared by every agent command: `--runtime claude|copilot`, `--model`, `--dry-run` (show what would be posted or
pushed without doing it) and `--yes` (skip confirmation).

Every command except `new` acts on the git repo containing the current directory. The GitHub repo is read from the
`origin` remote.

## Per-repo layout

```
my-app/
  .factory/
    config.yaml        # default runtime/model, per-agent overrides + maturity level
    agents/<agent>.md  # role prompt + frontmatter `access: read|write`
    skills/<agent>/    # skill/context files, edited over time by `factory learn`
    runs.db            # gitignored: run log + collected feedback
../my-app.worktrees/   # one checkout per issue the agents work in
```

## Feedback the agents learn from

The signals, ordered from strongest to weakest:

1. Commits you push on top of an agent's branch (the gap between what the agent wrote and what was merged)
2. PR review comments and review state (approved / changes requested)
3. PRs closed without merging
4. Your edits to agent-written specs
5. Your answers to agent questions on issues

Everything an agent produces carries a hidden footer, for example
`<!-- factory: agent=implement run=42 skills=<sha> -->`, so feedback can be traced back to the agent and the skill
version that produced it. Start a comment with `rule:` to mark it as a general lesson rather than a fix for that
one PR.

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
  config.py    # load .factory/config.yaml + agents/<name>.md into an AgentSpec
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
