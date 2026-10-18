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
- [ ] Tests for `repo.py` (slug parsing for SSH/HTTPS remotes, not-in-repo error) and `init`
- [x] Install `gh` (`brew install gh`) and run `gh auth login`

## M1 — Runtimes (Claude + Copilot)
- [ ] Verify current Python APIs for `claude-agent-sdk` and the GitHub Copilot SDK (install and inspect; don't guess)
- [ ] `runtimes/base.py`: `AgentRuntime` interface → `AgentResult(text, usage, cost, duration)`
- [ ] `runtimes/claude.py` adapter (system prompt, allowed tools, `cwd`, permission mode)
- [ ] `runtimes/copilot.py` adapter (session with system message, tools, working dir)
- [ ] Load `.factory/config.yaml` and `agents/<agent>.md` (frontmatter + prompt); resolve runtime/model overrides
- [ ] Smoke command: run a trivial prompt on both runtimes

## M2 — Project agent (`factory new`)
- [ ] Interview flow in the terminal (vision, users, core features, stack, non-goals); checklist decides when it's done
- [ ] Write `PRODUCT.md` + `TECH.md`
- [ ] Propose a backlog (epics → issues with acceptance criteria); you edit/approve before anything is created
- [ ] Create folder + `git init` + `gh repo create`, run `init`, push scaffold
- [ ] Create factory labels and milestones, open the issues
- [ ] Use it on the real project; capture corrections as the project agent's first skills

## M3 — GitHub layer + run log
- [ ] `github.py`: thin `gh` wrappers (issues, comments, labels, PRs, reviews, checks)
- [ ] `store.py`: SQLite `runs.db` (runs, feedback)
- [ ] Hidden footer on everything agents post: `<!-- factory: agent=… run=… skills=<sha> -->`
- [ ] `--dry-run` / confirmation prompt before any post or push (maturity L0/L1)

## M4 — Build loop agents
- [ ] `implement`: worktree in `../<repo>.worktrees/<issue>`, branch `factory/<issue>`, run tests, open PR "Closes #n"
- [ ] `fix`: read review comments on the PR, push fixes
- [ ] `review`: review diff, post comments, risk flag for human review (default runtime: the other SDK)
- [ ] `triage`: ready / needs-spec / needs-human + labels
- [ ] `spec`: product + tech spec as a PR to `specs/<issue>.md`
- [ ] `status`: issues/PRs by stage, maturity level per agent

## M5 — Self-improvement + metrics
- [ ] Collect feedback: review comments, review state, commits you pushed on top, closed-unmerged PRs, spec edits
- [ ] Treat `rule:`-prefixed comments as general lessons
- [ ] `learn`: group recurring corrections per agent → propose skill edits as a PR
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
