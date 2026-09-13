# Barcelona Match Alert Bot

A Discord bot that polls FC Barcelona's men's first-team schedule and mentions a configured role once for each upcoming match.

The kickoff is formatted with Discord's short time timestamp, for example:

```text
Kickoff: <t:1760000000:t>
```

Discord displays that timestamp in each member's local timezone.

## Setup

1. Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable the bot's **Message Content Intent** only if you later add message commands; this bot does not need it.
3. Invite the bot with the `bot` scope and permission to **View Channel** and **Send Messages**.
4. Copy `.env.example` to `.env` and fill in the bot token, announcement channel ID, and role ID.
5. Install dependencies and run:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

The bot checks every 10 minutes by default. `state.json` prevents duplicate announcements after restarts. The schedule source is ESPN's public FC Barcelona schedule endpoint.
