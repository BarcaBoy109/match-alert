# Match Alert Discord Bot

A public Discord bot that polls a configured soccer team's schedule and posts alerts in each server's configured channel. It defaults to FC Barcelona.

## Configure another team

The monitored team is configured with environment variables near the top of `bot.py`:

```env
TEAM_ID=83
TEAM_NAME=FC Barcelona
```

`TEAM_ID` is the team's ESPN ID. `TEAM_NAME` is the display name used in command responses and fallback text. The bot checks ESPN's soccer scoreboard for events involving that team ID.

To switch teams:

1. Find the team on ESPN's website and open its team page.
2. Copy the numeric ID from the team page URL or ESPN API data. For example, Barcelona uses ID `83`.
3. Set `TEAM_ID` and `TEAM_NAME` in `.env` locally or in your hosting provider's environment variables.
4. Restart or redeploy the bot.

The bot is currently designed for soccer teams because it uses ESPN's soccer scoreboard endpoint. Cup and league fixtures are included when ESPN lists them in the scoreboard feed.

The kickoff is formatted with Discord's short time timestamp, for example:

```text
Kickoff: <t:1760000000:t>
```

Discord displays that timestamp in each member's local timezone.

## Setup

1. Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable the bot's **Message Content Intent** only if you later add message commands; this bot does not need it.
3. Invite the bot with the `bot` scope and permission to **View Channel** and **Send Messages**.
4. Copy `.env.example` to `.env` and fill in the bot token.
5. Install dependencies and run:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

The bot checks every 10 minutes by default. After adding it to a server, a server administrator runs `/setchannel` and selects the announcement channel. Optionally run `/setrole` to choose the role to mention. Server-specific settings and duplicate-announcement state are stored in `state.json` when no database is configured. The schedule source is ESPN's public soccer scoreboard endpoint.

## Remote hosting with Supabase and Railway

The bot needs a long-running process for Discord's Gateway connection. Deploy the worker to Railway, Render, Fly.io, or another container host. Vercel Functions are request-based and time-limited, so use Vercel only for an optional web dashboard.

1. Create a Supabase project and run [`supabase/schema.sql`](supabase/schema.sql) in the SQL Editor.
2. In Supabase, open **Connect**, choose the **Session pooler**, and copy the PostgreSQL connection string. Use it as `DATABASE_URL`; replace the password placeholder.
3. Push this repository to GitHub and create a Railway service from the repository. Railway will detect the `Dockerfile`.
4. Add these Railway variables:

   - `DISCORD_TOKEN` — your bot token
   - `DATABASE_URL` — the Supabase session-pooler URL
   - `POLL_MINUTES` — usually `10`

5. Deploy. The bot will keep server settings and announced fixtures in Supabase instead of local disk.

Never commit `.env`, `DISCORD_TOKEN`, or `DATABASE_URL`. Supabase recommends choosing the connection method based on whether the application is a persistent backend or serverless function; this bot is a persistent backend.

## Public-bot invite settings

In OAuth2 URL Generator, select the `bot` and `applications.commands` scopes. Give the bot **View Channels** and **Send Messages** permissions. If you want to configure a role mention later, the role must be mentionable or the bot needs the relevant mention permission.
