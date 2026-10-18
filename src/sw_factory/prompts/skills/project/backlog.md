How to write PRODUCT.md, TECH.md and the starting backlog.

PRODUCT.md:
- Sections: Problem, Users, v1 scope (what it does), Non-goals, Success criteria, Open questions.
- Plain language a non-engineer could follow. No implementation details.

TECH.md:
- Sections: Stack, Architecture overview (components and how they talk), Data model, External services,
  Repository layout, Testing approach, Deployment, Open questions.
- Concrete enough that an implementation agent can make consistent choices without asking.

Backlog issues. Each issue is picked up by an AI implementation agent and reviewed by a human, so:
- **Small and independent.** One issue is roughly one focused pull request, a few hours of work for a person. Split
  anything bigger.
- **Vertical slices.** Prefer "user can sign up with email" over "build the database layer". Each slice should leave
  the app working.
- **First issue: project scaffold.** Set up the stack, a hello-world entry point, linting, a test runner with one
  passing test, and CI. Every later issue depends on it.
- **Order matters.** List issues in the order they should be built. Say so in the body when one issue depends on
  another, on its own line in Notes: `Depends on: <exact title>` (several titles separated by `; `). Only depend on
  issues listed earlier. Use the exact title so the factory can turn it into an issue link.
- **Body format:**
  ```
  ## Goal
  <one or two sentences on what and why>

  ## Acceptance criteria
  - [ ] <observable, testable outcome>
  - [ ] ...

  ## Notes
  <relevant decisions from TECH.md, edge cases, dependencies>
  ```
- **Milestones** group issues into meaningful stages, for example "v1: core" and "v1: polish". Usually 1–3.
- **Labels:** `enhancement` for features, `documentation` for docs-only work, `epic` only for a tracking issue that
  links to the others. Don't create epics unless the backlog has more than about 12 issues.
- **Needs a decision:** set `"needs_human": true` on an issue that can't be built until the person decides
  something (an open question you couldn't settle). Say in the body what has to be decided. These issues skip
  triage until a human answers.
- Aim for 5–15 issues for v1. Put larger ideas the person mentioned under Open questions or a "Later" milestone
  instead of bloating v1.
