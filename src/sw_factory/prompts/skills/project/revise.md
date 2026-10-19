How to revise an existing project's docs and backlog from the person's suggestions.

Understand first:
- Ask follow-up questions only when it matters: when a suggestion is ambiguous, has a cost the
  person may not have seen (hardware, money, security, a lot of rework), or **conflicts with a decision recorded
  earlier**: in PRODUCT.md or TECH.md, or in a comment on an issue (for example an answer the person gave on a
  needs-human issue). Quote the earlier decision and ask which one wins. Never silently override a recorded decision.
- Ask at most 3–5 short questions at a time, each with your recommended answer.

Then propose:
- **Docs.** Return the full new PRODUCT.md and/or TECH.md. Change only what the suggestions require; keep everything
  else word for word, including structure and headings. Move settled items out of "Open questions"; add new ones that
  the change raises. Return null for a doc that doesn't change.
- **Open issues.** Check every open issue against the new docs: its body, its acceptance criteria, and any triage
  build plan in its comments. For each one that no longer matches, propose:
  - `update`: a new body (and title or milestone if needed) that matches the new docs. Keep the same format (Goal /
    Acceptance criteria / Notes) and keep `Depends on: #N` lines correct.
  - `close`: when the change makes the issue unnecessary. Say what supersedes it.
  Leave unaffected issues out. You don't need to handle work in progress specially; the factory applies its own rules
  for issues that are being implemented or already have a build plan.
- **New issues** for new work, in the same format as the starting backlog, in build order. Work that changes code
  that is already built (closed issues) is always a new issue; never reopen or edit closed issues.
- Keep the backlog shape: small vertical slices, milestones that still make sense, nothing bloating v1 that the
  person didn't ask for.
