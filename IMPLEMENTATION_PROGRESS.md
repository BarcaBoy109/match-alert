# Implementation progress

## Iterations 1–2 — complete

- Added bounded, newest-first 30-day completed-result fetching with individual ESPN dates, event deduplication, national-team matching, safe missing-field handling, and explicit exceptional-status exclusion.
- Added ephemeral `/results`, favourite fallback, aliases, bounded output, and provider-error handling.
- Checks: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 6 passed.
- Limitations: lifecycle persistence, per-team routing, and full autocomplete coverage remain for later iterations.

## Iteration 3 — complete

- Added additive `match_lifecycle` JSON/PostgreSQL persistence and schema migration.
- Checks: existing unittest suite and `git diff --check` passed.

## Iteration 4 — partial

- Exceptional ESPN statuses now take precedence over `state=post` in shared status formatting.
- Full poll reconciliation, replacement delivery, and retention-deadline handling remain unimplemented.
- Maintenance now updates live reminders before cleanup, and ordinary announcements persist lifecycle snapshots.

## Next

Iteration 3: add durable lifecycle snapshots and additive database schema support.

## Iteration 5 — complete

- Added JSON/PostgreSQL nullable per-team channel and role overrides, independent reset behavior, inheritance, and `/resetalerts`.
- Extended `/setchannel` and `/setrole` with optional monitored-team overrides and routed ordinary alerts by destination.
- Checks: full existing unittest suite — 6 passed; `git diff --check` passed.

## Iteration 8 — incomplete

- Documentation was updated for results, routing inheritance, reset behavior, status labels, and additive schema application.
- The required cross-feature fake integration scenarios and full transition reconciliation are not yet complete; no final release commit is claimed.

Final verification remains open pending those scenarios.

Latest progress: added a pure lifecycle transition classifier and regression coverage for equivalent timezones, kickoff changes, and exceptional status precedence. The suite now has 9 passing tests.
Added transition notice formatting with old/new Discord timestamps; suite now has 10 passing tests.
Deleted active reminders now attempt one replacement in their original saved channel, with a fresh retention deadline and lifecycle snapshot.
Added additive `alert_deliveries` storage for independent per-guild/event/channel delivery records, with legacy announcement migration and JSON/PostgreSQL store methods.
- Remaining: Discord permission validation and complete status-transition delivery reconciliation require iteration 6.

## Iteration 6 — partial checkpoint

- Ordinary alert candidates are grouped by guild, event, and effective destination channel; role mentions are deduplicated and inherited/overridden routes are respected.
- Known limitation: delivery records remain in the legacy single-row announced-match model, so transition replacement delivery and partial-failure retry are not complete.

## Iteration 7 — complete

- Added reusable local team/alias suggestions with exact, prefix, substring ranking, canonical deduplication, deterministic ordering, and 25-choice bounding.
- Registered team autocomplete on all team-bearing commands, including `/results` and `/resetalerts`.
- Checks: full existing unittest suite — 6 passed; `git diff --check` passed.
