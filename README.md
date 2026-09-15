# Match Alert Discord Bot

A Discord bot that monitors soccer fixtures and posts match alerts in a configured channel. Each server can choose a favourite team and save additional teams for alerts. It defaults to FC Barcelona.

[![Invite Match Alert to Discord](https://img.shields.io/badge/Invite%20Match%20Alert-Discord-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1548667872451100682&permissions=19456&integration_type=0&scope=bot%20applications.commands)

## Discord commands

Team names are matched case-insensitively. The supported clubs are from the Premier League, La Liga, Bundesliga, Serie A, and Ligue 1.

| Command | Use |
| --- | --- |
| `/configure team:<name>` | Set the server's favourite team and ensure it receives alerts. Existing alert teams are kept. Requires Manage Server. |
| `/addteam team:<name>` | Add another team to this server's alerts. Requires Manage Server. |
| `/removeteam team:<name>` | Remove a non-favourite team from alerts. Requires Manage Server. |
| `/teams` | List saved alert teams and identify the favourite. |
| `/nextmatch` | Show the favourite team's next match within seven days. |
| `/nextmatch team:<name>` | Check another supported team's next match without changing settings. |
| `/setchannel channel:<channel>` | Choose where alerts are posted. Requires Manage Server. |
| `/setrole role:<role>` | Choose the role mentioned in alerts. Requires Manage Server. |

Example:

```text
/configure team:Real Madrid
/addteam team:Arsenal
/addteam team:PSG
/teams
```

The favourite cannot be removed. Configure a different favourite first if needed. Duplicate teams are ignored, including aliases that resolve to the same club.

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
TEAM_ID=83
TEAM_NAME=FC Barcelona
POLL_MINUTES=10
STATE_FILE=state.json
DATABASE_URL=postgresql://...
PORT=8080
```

`TEAM_ID` and `TEAM_NAME` are fallback values for servers that have not configured a favourite. Never commit `.env`, `DISCORD_TOKEN`, or `DATABASE_URL`.

## Supabase and Railway

The bot requires a long-running process for Discord's Gateway connection. Railway, Render, Fly.io, or another container host can run it.

1. Create a Supabase project.
2. Run [`supabase/schema.sql`](supabase/schema.sql) in the Supabase SQL Editor. For an existing deployment, rerun it to create `guild_teams` and migrate current single-team settings.
3. Copy the Supabase **Session pooler** connection string into `DATABASE_URL`.
4. Deploy the repository and set `DISCORD_TOKEN`, `DATABASE_URL`, and optionally `POLL_MINUTES` in the host's environment settings.

Supabase stores server settings and announced fixtures when `DATABASE_URL` is configured; local `state.json` is not used for those records.
