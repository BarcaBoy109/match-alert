# Future Plan

## Current release

- Monitor multiple teams per Discord server.
- Keep a separate favourite team for `/nextmatch`.
- Prevent duplicate teams and duplicate match alerts.
- Update sent reminders with live scores and final results.
- Delete reminders after the configured retention period.
- Maintain the verified ESPN-ID catalog for supported clubs and national teams.
- Support fixture lookups across the five second divisions, Primeira Liga, Eredivisie, Süper Lig, MLS, and Saudi Pro League.

## Planned improvements

- Add a command to show recent results after reminder messages are deleted.
- Handle postponed, abandoned, and rescheduled matches with dedicated messages.
- Add per-team alert channels and roles.
- Add autocomplete for supported team names and aliases.
- Add richer match details, including competition, venue, and lineups when ESPN provides them.
- Add automated integration tests for Discord message updates and Supabase migrations.
- Add configurable reminder timing and notification preferences per team.
