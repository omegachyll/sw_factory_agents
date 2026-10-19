# TODO

Build order: the factory grows alongside the first real project. Do a step by hand, write down how, then automate it.

## M0 — Skeleton
- [x] Typer CLI shell with all commands and shared options (`--runtime`, `--model`, `--dry-run`, `--yes`)
- [x] Repo detection from the working directory (git root + GitHub `owner/name` from `origin`)
- [x] `factory init` creates `.factory/` (config, agents/, skills/, .gitignore)
- [x] README + TODO
- [x] License: PolyForm Noncommercial 1.0.0 + COMMERCIAL.md (commercial use by revocable written permission)
- [x] After the first push, add the GitHub repo URL to the LICENSE notice and COMMERCIAL.md
- [x] `git init` this project and push it to GitHub (private: omegachyll/sw_factory_agents)
- [x] Tests for `repo.py` (slug parsing for SSH/HTTPS remotes, not-in-repo error) and `init`
- [x] Install `gh` (`brew install gh`) and run `gh auth login`

## M1 — Runtimes (Claude + Copilot)
- [x] Verify current Python APIs for `claude-agent-sdk` (0.2.168) and `github-copilot-sdk` (1.0.22) by installing and inspecting
- [x] `runtimes/base.py`: `AgentSpec` (prompt, runtime, model, access read|write, timeout) → `AgentResult`
- [x] `runtimes/claude.py`: tools limited by access level, `dontAsk` permissions, no user settings or MCP connectors
- [x] `runtimes/copilot.py`: **experimental.** Type-checked against the SDK and tested with a fake client. Live: it
      reaches GitHub and gets an authorization error (no subscription), reported cleanly
- [ ] Check whether Copilot's bring-your-own-key mode (`provider={"type": "anthropic", "api_key": ...}`) works
      without a Copilot subscription. Needs an `ANTHROPIC_API_KEY`
- [ ] When Copilot can be run for real: check it doesn't load the user's own MCP servers/config (see the Claude finding below)
- [x] Default `review` runtime to `claude` in the `init` config until Copilot is verified end to end
- [x] Load `.factory/config.yaml` (runtime/model/level per agent) and `agents/<agent>.md` (frontmatter `access` + prompt).
      Precedence: CLI option > `agents.<name>` > defaults
- [x] Smoke command: `factory doctor` checks git, `gh` login, and sends a test prompt to each runtime
- [x] Finding: without `strict_mcp_config`, Claude runs loaded every claude.ai connector (~196K tokens, $1.57 for "OK").
      Fixed: ~3.4K tokens, ~$0.001 cached

## M2 — Two-layer agents + project agent (`factory new`)
- [x] Two layers: base agents + general skills ship in the factory (`src/sw_factory/prompts/`); projects add
      `.factory/skills/<agent>/` (applied last, takes precedence) and may override a base prompt in `.factory/agents/`
- [x] `factory init` creates only the project layer (config, skills/README) and creates the factory labels on GitHub
- [x] `access: none` for conversation-only agents (no tools)
- [x] Interview in the terminal driven by a checklist skill; `/done` wraps up, `/quit` aborts
- [x] Plan: PRODUCT.md + TECH.md + backlog (milestones, issues with acceptance criteria) as validated JSON, one retry on bad output
- [x] Review loop: create / revise with feedback / quit; plan saved to `<repo>.plan.json`, resumable with `--from-plan`
- [x] Create: folder + README/PRODUCT/TECH + `.factory/`, first commit, `gh repo create` + push, labels, milestones, issues
      (`factory:new` + allowed labels, hidden footer); checks the repo name is free first; `--dry-run` creates nothing
- [x] Live dry-run on Claude with a sample project (habit tracker): good interview and a 6-issue backlog
- [x] Used for real on hydro-monitor: 14 issues, 4 milestones, labels; all created as planned
- [x] `Depends on: <title>` lines become `#N` issue links; issues needing a decision get `factory:needs-human`
- [ ] Capture further corrections from real use as the project agent's first skills
- [x] `factory project`: revise PRODUCT.md/TECH.md + open backlog from suggestions; questions on conflicts;
      docs via PR from a temp worktree; per-stage issue rules (edit / re-triage / flag / close); live dry run on
      hydro-monitor (30 s interval + temperature compensation: good diff, #13 superseded, $0.39 on Opus)
- [ ] Use `factory project` for real on hydro-monitor
- [ ] Interview answers are single-line; consider multi-line input (e.g. open `$EDITOR`) for long answers

## Model selection
- [x] `-m` accepts aliases (`opus`, `sonnet`, `haiku`, `fable`), also in `config.yaml`
- [x] `-m ?` picks from a numbered list; unknown models are rejected up front (unknown `claude-*` IDs warn but run)
- [x] `factory models`: models per runtime (Copilot via the SDK's `list_models`) and each agent's runtime/model/level
- [ ] Verify Copilot model listing and validation with a real subscription

## M3 — GitHub layer + run log
- [x] `github.py`: `gh` wrapper for repos, labels, milestones and issues (done in M2)
- [x] `github.py`: read issues with comments, post/edit comments, set the stage label
- [ ] `github.py`: PRs, reviews, checks
- [ ] `store.py`: SQLite `runs.db` (runs, feedback)
- [x] Hidden footer on agent comments: `<!-- factory: agent=… run=… factory=<sha> model=… -->` (`footer.py`)
- [ ] Add `project-skills=<sha>` to the footer
- [x] Record the factory version (git SHA of sw-factory) for each run, so shared-layer regressions show up across projects
- [x] `--dry-run` / confirmation prompt before any post (triage); keep it for every agent that posts or pushes

## M4 — Build loop agents
- [ ] `implement`: worktree in `../<repo>.worktrees/<issue>`, branch `factory/<issue>`, run tests, open PR "Closes #n"
- [ ] `fix`: read review comments on the PR, push fixes
- [ ] `review`: review diff, post comments, risk flag for human review (default runtime: the other SDK)
- [x] `triage`: ready (with build plan) / needs-human / needs-spec / blocked + stage label, as an issue comment;
      re-run reads replies and revises; live dry run on hydro-monitor #1 (Opus, $0.19, good plan) and #2 (blocked)
- [ ] `triage`: use it for real on hydro-monitor; capture corrections as triage skills
- [ ] `spec`: product + tech spec as a PR to `specs/<issue>.md`
- [ ] `status`: issues/PRs by stage, maturity level per agent

## M5 — Self-improvement + metrics
- [ ] Collect feedback: review comments, review state, commits you pushed on top, closed-unmerged PRs, spec edits
- [ ] Treat `rule:`-prefixed comments as general lessons
- [ ] `learn`: group recurring corrections per agent and sort them into general (PR on sw-factory) vs
      project-specific (PR on `.factory/skills/`); `rule:` = general, `rule(project):` = project
- [ ] `stats`: correction rate, merged-without-changes %, tokens/cost per run, per skill version
- [ ] Rules for moving an agent up the maturity ladder, based on stats

## Later
- [ ] Label/comment triggers (`/factory implement`) via GitHub Actions for L2 agents
- [ ] Verify agent (CI checks, screenshots for UI)
- [ ] Monitor agent (production signals → new issues)

## Open decisions
- [ ] Worktrees next to the repo (`../<repo>.worktrees`) vs. inside it (`.factory/worktrees`). Leaning: next to it
- [ ] Commit `.factory/` agents/skills in public repos? (your prompts would be visible)
- [ ] Learning loop: manual `factory learn` only, or also triggered by `rule:` comments? Starting with manual
