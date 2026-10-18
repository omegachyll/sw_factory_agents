How to choose between ready, needs-human and needs-spec.

- **ready** if one person could build it in one focused pull request (roughly a few hours) from the issue, the docs
  and your plan, with only small assumptions.
- **needs-human** if the answer to an open question would change what gets built, and the issue, the comments and
  the project docs don't settle it. Typical cases: an "Open questions" item in PRODUCT.md or TECH.md that this issue
  depends on, an issue that says it needs the owner's decision, hardware details only the owner knows, security or
  cost trade-offs. Ask at most 3–5 questions. Write each so it can be answered in a line, and give your recommended
  answer. If you can still outline the work, include a draft plan as well.
- **needs-spec** if it would take several pull requests, mixes unrelated changes, or is too vague to write
  acceptance criteria for. Propose the split as a short list of smaller issue titles, in build order.

Re-triage: when a previous triage comment from the factory is followed by replies from the person, treat those
replies as answers and corrections. Apply them, move to `ready` if nothing important is left open, and start the
summary with what changed since the last run.
