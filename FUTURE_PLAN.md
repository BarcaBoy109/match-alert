# Future Plan

## Current release

- Monitor multiple teams per Discord server.
- Keep a separate favourite team for `/nextmatch`.
- Prevent duplicate teams and duplicate match alerts.
- Update sent reminders with live scores and final results.
- Delete reminders after the configured retention period.
- Maintain the verified ESPN-ID catalog for supported clubs and national teams.
- Support fixture lookups across the five second divisions, Primeira Liga, Eredivisie, Süper Lig, MLS, and Saudi Pro League.
- Show recent completed results independently of deleted reminders.
- Handle postponed, abandoned, cancelled, suspended, and rescheduled matches with dedicated notices.
- Support per-team alert channels and roles with inherited guild defaults.
- Autocomplete supported team names, aliases, and competition names.

## Planned improvements

- Add richer match details, including competition, venue, and lineups when ESPN provides them.
- Add automated integration tests for Discord message updates and Supabase migrations.
- Add configurable reminder timing and notification preferences per team.
