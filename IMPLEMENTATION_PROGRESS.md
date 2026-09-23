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

## Next

Iteration 3: add durable lifecycle snapshots and additive database schema support.

## Iteration 5 — complete

- Added JSON/PostgreSQL nullable per-team channel and role overrides, independent reset behavior, inheritance, and `/resetalerts`.
- Extended `/setchannel` and `/setrole` with optional monitored-team overrides and routed ordinary alerts by destination.
- Checks: full existing unittest suite — 6 passed; `git diff --check` passed.
- Remaining: Discord permission validation and complete status-transition delivery reconciliation require iteration 6.
