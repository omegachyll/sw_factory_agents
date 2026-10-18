How to write the build plan for a `ready` issue. The implementation agent follows it step by step, and a human skims
it before work starts, so make it concrete and short.

Format (markdown, these headings in this order):

```
### Approach
<2–4 sentences: the shape of the solution and the key choices, citing TECH.md where it decides something>

### Files
- `path/to/file.py` (new): <one line on its purpose>
- `path/to/other.py` (change): <what changes>

### Steps
1. <small, checkable step>
2. ...

### Tests
- <tests to add, and what each proves>
- Run: `<exact command(s)>`

### Acceptance criteria
- <each criterion from the issue> → step(s) <n> / test <name>

### Assumptions and risks
- <anything you decided that the issue or docs didn't, and anything likely to go wrong>
```

Guidelines:
- **Map every acceptance criterion** in the issue to steps or tests. If a criterion is a manual check (hardware,
  a UI, a phone notification), say what the PR should document so a human can verify it.
- **Follow TECH.md and the existing code.** Use its stack, layout, libraries and naming. Don't introduce a new
  dependency or pattern unless the issue needs it, and say why when you do.
- **Stay in scope.** Plan only this issue. If you notice adjacent work, list it under risks as a possible follow-up
  issue instead of planning it.
- **Interfaces, not code.** Name functions, classes, CLI commands and config keys with their signatures where it
  helps; don't write the implementation.
- **Small decisions: decide.** For low-stakes choices (a file name, a default value, a library already in TECH.md)
  pick a sensible option and record it under Assumptions. Reserve `needs-human` for decisions the person would
  want to make themselves.
- **Keep it proportional.** A one-line fix gets a few lines of plan; a scaffold or new subsystem gets more. Aim for
  something a reviewer can read in two minutes.
