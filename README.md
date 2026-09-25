# Match Alert Discord Bot

A Discord bot that monitors soccer fixtures and posts match alerts in a configured channel. Each server can choose a favourite team and save additional teams for alerts. It defaults to FC Barcelona.

**[View the showcase →](https://barcaboy109.github.io/match-alert/)**

[![Invite Match Alert to Discord](https://img.shields.io/badge/Invite%20Match%20Alert-Discord-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1548667872451100682&permissions=19456&integration_type=0&scope=bot%20applications.commands)

## Showcase site

The static showcase lives in [`docs/`](docs/) and deploys through [`.github/workflows/pages.yml`](.github/workflows/pages.yml) whenever site files change on `main`. For the first deployment, set **Settings → Pages → Source** to **GitHub Actions** in the repository.

Preview it locally with:

```powershell
python -m http.server 8000 --directory docs
```

## Discord commands

Team names are matched case-insensitively against the catalog in [`teams.py`](teams.py). Every team entry uses its numeric ESPN team ID; unsupported names are rejected. Supported teams include clubs from the Premier League, La Liga, Bundesliga, Serie A, and Ligue 1; every current team from the five second divisions, Primeira Liga, Eredivisie, Süper Lig, MLS, and the Saudi Pro League; plus the top 50 men's national teams in the FIFA World Ranking (20 July 2026). Competition lookups also support the UEFA Champions League, UEFA Europa League, UEFA Conference League, and the five domestic cups.

| Command | Use |
| --- | --- |
| `/configure team:<name>` | Set the server's favourite team and ensure it receives alerts. Existing alert teams are kept. Requires Manage Server. |
| `/addteam team:<name>` | Add another team to this server's alerts. Requires Manage Server. |
| `/removeteam team:<name>` | Remove a non-favourite team from alerts. Requires Manage Server. |
| `/teams` | List saved alert teams and identify the favourite. |
| `/nextmatch` | Show the favourite team's next match within seven days. |
| `/nextmatch team:<name>` | Check another supported team's next match without changing settings. |
| `/nextmatch competition:<name>` | Show the next match in a supported competition within seven days. |
| `/results [team:<name>] [limit:<1-10>]` | Show completed results from the trailing 30 days. |
| `/setchannel channel:<channel> [team:<name>]` | Choose the server default or a monitored team's alert channel. Requires Manage Server. |
| `/setrole role:<role> [team:<name>]` | Choose the server default or a monitored team's alert role. Requires Manage Server. |
| `/resetalerts team:<name> setting:<channel\|role\|both>` | Reset a team's override so it inherits the server default. |

Example:

```text
/configure team:Real Madrid
/addteam team:Arsenal
/addteam team:PSG
/addteam team:Japan
/teams
/nextmatch competition:La Liga
/nextmatch competition:UEFA Champions League
/nextmatch competition:UEL
/nextmatch competition:UECL
/nextmatch competition:FA Cup
/nextmatch competition:Championship
/nextmatch competition:MLS
/nextmatch competition:Saudi League
```

The favourite cannot be removed. Configure a different favourite first if needed. Duplicate teams are ignored, including aliases that resolve to the same club. `/nextmatch` accepts either a team or a competition, but not both.

Team options provide local autocomplete for canonical names and aliases. Per-team routing inherits the server channel and role independently unless an override is set. Exceptional ESPN statuses are labelled separately from final results. The complete `supabase/full_schema.sql` is the current database baseline and includes team routing, lifecycle tracking, delivery records, backfills, and maintenance indexes without requiring separate migrations. The schema was verified against a disposable local PostgreSQL 17 cluster. The user's Supabase instance was not independently inspected.

## Local setup

1. Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Invite it with the `bot` and `applications.commands` scopes and **View Channels** and **Send Messages** permissions.
3. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
4. Install and run the bot:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

Run `/setchannel` after adding the bot to a server. The bot polls every 10 minutes by default and uses ESPN's public soccer scoreboard endpoint. Discord timestamps are displayed in each member's local timezone.

Without `DATABASE_URL`, server settings and announced-match state are stored in `state.json`.

## Environment variables

```env
DISCORD_TOKEN=your-bot-token
DEFAULT_TEAM_ID=83
DEFAULT_TEAM_NAME=FC Barcelona
POLL_MINUTES=10
ESPN_MAX_CONCURRENCY=4
ESPN_LIVE_CACHE_SECONDS=60
ESPN_FUTURE_CACHE_SECONDS=600
ESPN_RECENT_CACHE_SECONDS=3600
ESPN_HISTORICAL_CACHE_SECONDS=86400
ESPN_STALE_IF_ERROR_SECONDS=1800
STATE_FILE=state.json
DATABASE_URL=postgresql://...
PORT=8080
```

`DEFAULT_TEAM_ID` and `DEFAULT_TEAM_NAME` are fallback values for servers that have not configured a favourite. Existing deployments using `TEAM_ID` and `TEAM_NAME` remain supported. Never commit `.env`, `DISCORD_TOKEN`, or `DATABASE_URL`.

`REMINDER_RETENTION_HOURS` controls when sent reminders are deleted after kickoff. It defaults to `3`, allowing time for a normal match to finish.

ESPN scoreboard responses are cached in memory by competition and UTC date. Today's data is cached for 60 seconds, upcoming fixtures for 10 minutes, yesterday's data for one hour, and older results for one day. Independent dates are fetched concurrently with a default limit of four requests. During a temporary ESPN failure, recently expired data can be reused for up to 30 minutes. These values can be tuned with the `ESPN_*` variables above. The cache belongs to the running bot process and resets when it restarts.

## Deployment health and Discord rate limits

Configure Render's **Health Check Path** as `/live` (or retain its TCP port check).
Do this before deploying this version if Render currently checks `/health`.
`/` and `/live` return HTTP 200 while the process is alive, including during a
Discord cooldown. This prevents the host from restarting the process and making
another premature login attempt.

Use `/health` or `/ready` for external availability monitoring, such as
UptimeRobot. They return HTTP 503 while Discord is disconnected, command setup
is incomplete, or an IP/global cooldown is active. The JSON response includes
`discord_connected`, `discord_ready`, `status`, and `retry_after_seconds`.
When the bot client exists, it also includes cumulative `espn_cache` hit, miss,
upstream-request, stale-response, and entry counts. They return HTTP 200 once the bot is ready. Do not use these readiness endpoints
as a host restart trigger.

Discord global and edge/IP 429 responses pause subsequent Discord HTTP requests
across login, reminders, and interaction responses. Ordinary per-route limits
remain managed by discord.py. Retries honor Discord's delay plus a small safety
margin; failed startup clients close their background tasks and database pool
before waiting. Cooldowns survive client recreation in the same process, but
not a process restart, so avoid manual redeploys during an active cooldown.
If deployment is necessary during a cooldown, set `DISCORD_NOT_BEFORE` to the
Unix timestamp of the existing retry deadline before deploying. The new process
serves health checks immediately but delays its first Discord request until that
time. A timestamp in the past has no effect and can be removed later.
Diagnostics log the rate-limit scope, delay, and Cloudflare request identifier
without logging credential-bearing request URLs.

Startup compares registered slash commands with the current definitions and
updates them only when changed. Existing per-server command registrations are
kept up to date for compatibility; new servers use the global commands.
`POLL_MINUTES` must be a positive integer.

While a saved alert is active, the bot updates its message with the live score and then the final result before deleting it after the retention period. See [`FUTURE_PLAN.md`](FUTURE_PLAN.md) for planned improvements.

## Supabase and Railway

The bot requires a long-running process for Discord's Gateway connection. Railway, Render, Fly.io, or another container host can run it.

1. Create a Supabase project.
2. Run [`supabase/full_schema.sql`](supabase/full_schema.sql) in the Supabase SQL Editor. It contains the complete current schema and is safe to run repeatedly.
3. Copy the Supabase **Session pooler** connection string into `DATABASE_URL`.
4. Deploy the repository and set `DISCORD_TOKEN`, `DATABASE_URL`, and optionally `POLL_MINUTES` in the host's environment settings.

Supabase stores server settings, announced fixtures, and reminder message IDs when `DATABASE_URL` is configured; local `state.json` is not used for those records. The bot needs permission to view and delete messages in the alert channel.

[`supabase/schema.sql`](supabase/schema.sql) is retained as an additive, repeatable compatibility script for an older local database. New deployments only need `supabase/full_schema.sql`. Verify database changes before starting the bot, and do not apply them to production without a backup and change approval.
