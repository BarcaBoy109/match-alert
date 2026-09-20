# Match Alert Discord Bot

A Discord bot that monitors soccer fixtures and posts match alerts in a configured channel. Each server can choose a favourite team and save additional teams for alerts. It defaults to FC Barcelona.

[![Invite Match Alert to Discord](https://img.shields.io/badge/Invite%20Match%20Alert-Discord-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1548667872451100682&permissions=19456&integration_type=0&scope=bot%20applications.commands)

## Discord commands

Team names are matched case-insensitively. The built-in catalog includes clubs from the Premier League, La Liga, Bundesliga, Serie A, and Ligue 1, plus the top 50 men's national teams in the FIFA World Ranking (20 July 2026). Teams not yet in that catalog, including clubs from the five second divisions, Primeira Liga, Eredivisie, Süper Lig, MLS, and the Saudi Pro League, can also be added by their ESPN fixture name. Competition lookups support the UEFA Champions League, the five domestic cups, and all of those leagues.

| Command | Use |
| --- | --- |
| `/configure team:<name>` | Set the server's favourite team and ensure it receives alerts. Existing alert teams are kept. Requires Manage Server. |
| `/addteam team:<name>` | Add another team to this server's alerts. Requires Manage Server. |
| `/removeteam team:<name>` | Remove a non-favourite team from alerts. Requires Manage Server. |
| `/teams` | List saved alert teams and identify the favourite. |
| `/nextmatch` | Show the favourite team's next match within seven days. |
| `/nextmatch team:<name>` | Check another supported team's next match without changing settings. |
| `/nextmatch competition:<name>` | Show the next match in a supported competition within seven days. |
| `/setchannel channel:<channel>` | Choose where alerts are posted. Requires Manage Server. |
| `/setrole role:<role>` | Choose the role mentioned in alerts. Requires Manage Server. |

Example:

```text
/configure team:Real Madrid
/addteam team:Arsenal
/addteam team:PSG
/addteam team:Japan
/teams
/nextmatch competition:La Liga
/nextmatch competition:UEFA Champions League
/nextmatch competition:FA Cup
/nextmatch competition:Championship
/nextmatch competition:MLS
```

The favourite cannot be removed. Configure a different favourite first if needed. Duplicate teams are ignored, including aliases that resolve to the same club. `/nextmatch` accepts either a team or a competition, but not both.

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
STATE_FILE=state.json
DATABASE_URL=postgresql://...
PORT=8080
```

`DEFAULT_TEAM_ID` and `DEFAULT_TEAM_NAME` are fallback values for servers that have not configured a favourite. Existing deployments using `TEAM_ID` and `TEAM_NAME` remain supported. Never commit `.env`, `DISCORD_TOKEN`, or `DATABASE_URL`.

`REMINDER_RETENTION_HOURS` controls when sent reminders are deleted after kickoff. It defaults to `3`, allowing time for a normal match to finish.

While a saved alert is active, the bot updates its message with the live score and then the final result before deleting it after the retention period. See [`FUTURE_PLAN.md`](FUTURE_PLAN.md) for planned improvements.

## Supabase and Railway

The bot requires a long-running process for Discord's Gateway connection. Railway, Render, Fly.io, or another container host can run it.

1. Create a Supabase project.
2. Run [`supabase/schema.sql`](supabase/schema.sql) in the Supabase SQL Editor. For an existing deployment, rerun it to create `guild_teams` and migrate current single-team settings.
3. Copy the Supabase **Session pooler** connection string into `DATABASE_URL`.
4. Deploy the repository and set `DISCORD_TOKEN`, `DATABASE_URL`, and optionally `POLL_MINUTES` in the host's environment settings.

Supabase stores server settings, announced fixtures, and reminder message IDs when `DATABASE_URL` is configured; local `state.json` is not used for those records. The bot needs permission to view and delete messages in the alert channel.
