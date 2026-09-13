# Barcelona Match Alert Bot

A public Discord bot that polls FC Barcelona's men's first-team schedule and posts alerts in each server's configured channel.

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

The bot checks every 10 minutes by default. After adding it to a server, a server administrator runs `/setchannel` and selects the announcement channel. Optionally run `/setrole` to choose the role to mention. Server-specific settings and duplicate-announcement state are stored in `state.json`. The schedule source is ESPN's public FC Barcelona schedule endpoint.

## Public-bot invite settings

In OAuth2 URL Generator, select the `bot` and `applications.commands` scopes. Give the bot **View Channels** and **Send Messages** permissions. If you want to configure a role mention later, the role must be mentionable or the bot needs the relevant mention permission.
