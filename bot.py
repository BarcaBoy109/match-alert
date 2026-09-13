import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
import asyncpg
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("barca-bot")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TEAM_ID = os.getenv("TEAM_ID", "83")
TEAM_NAME = os.getenv("TEAM_NAME", "FC Barcelona")
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))

# ESPN's public scoreboard endpoint. TEAM_ID selects the team to monitor.
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; starting with empty state", STATE_FILE)
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


class StateStore:
    """Use Supabase Postgres in production, with JSON as a local fallback."""

    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None
        self.local_state = load_state()

    async def connect(self) -> None:
        if DATABASE_URL:
            self.pool = await asyncpg.create_pool(DATABASE_URL, ssl="require", min_size=1, max_size=3)

    async def get_guild(self, guild_id: int) -> dict:
        if self.pool:
            row = await self.pool.fetchrow(
                "SELECT channel_id, role_id FROM guild_settings WHERE guild_id = $1", guild_id
            )
            return dict(row) if row else {}
        return self.local_state.get("guilds", {}).get(str(guild_id), {})

    async def set_guild_value(self, guild_id: int, key: str, value: int) -> None:
        if self.pool:
            await self.pool.execute(
                f"""INSERT INTO guild_settings (guild_id, {key}) VALUES ($1, $2)
                ON CONFLICT (guild_id) DO UPDATE SET {key} = EXCLUDED.{key}""",
                guild_id,
                value,
            )
            return
        guild_state = self.local_state.setdefault("guilds", {}).setdefault(str(guild_id), {})
        guild_state[key] = value
        save_state(self.local_state)

    async def has_announced(self, match_id: str) -> bool:
        if self.pool:
            return await self.pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM announced_matches WHERE match_id = $1)", match_id
            )
        return match_id in self.local_state.get("announced_matches", [])

    async def mark_announced(self, match_id: str) -> None:
        if self.pool:
            await self.pool.execute(
                "INSERT INTO announced_matches (match_id) VALUES ($1) ON CONFLICT DO NOTHING", match_id
            )
            return
        matches = self.local_state.setdefault("announced_matches", [])
        matches.append(match_id)
        self.local_state["announced_matches"] = matches[-50:]
        save_state(self.local_state)


def kickoff_unix(event: dict) -> int:
    start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    return int(start.astimezone(timezone.utc).timestamp())


def event_name(event: dict) -> str:
    competition = event["competitions"][0]
    competitors = competition["competitors"]
    names = {item["homeAway"]: item["team"]["displayName"] for item in competitors}
    return f"{names.get('home', TEAM_NAME)} vs {names.get('away', 'opponent')}"


@app_commands.command(
    name="setchannel",
    description="Choose where match alerts are posted.",
)
@app_commands.describe(channel="The text channel for match alerts")
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel_command(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    bot = interaction.client
    if not isinstance(bot, BarcelonaBot):
        return
    await bot.store.set_guild_value(interaction.guild_id, "channel_id", channel.id)
    await interaction.response.send_message(
        f"Match alerts will be posted in {channel.mention}.", ephemeral=True
    )


@setchannel_command.error
async def setchannel_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need the **Manage Server** permission to configure this bot.", ephemeral=True
        )
    else:
        logger.exception("/setchannel failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong.", ephemeral=True)


@app_commands.command(name="setrole", description="Choose the role to mention in match alerts.")
@app_commands.describe(role="The role to mention")
@app_commands.checks.has_permissions(manage_guild=True)
async def setrole_command(interaction: discord.Interaction, role: discord.Role) -> None:
    bot = interaction.client
    if not isinstance(bot, BarcelonaBot):
        return
    await bot.store.set_guild_value(interaction.guild_id, "role_id", role.id)
    await interaction.response.send_message(
        f"I will mention {role.mention} in match alerts.", ephemeral=True
    )


@app_commands.command(name="nextmatch", description="Show the configured team's next match in the next 7 days.")
async def nextmatch_command(interaction: discord.Interaction) -> None:
    match = await fetch_next_match()
    if not match:
        await interaction.response.send_message(
            f"No {TEAM_NAME} match was found in the next 7 days.", ephemeral=True
        )
        return
    timestamp = kickoff_unix(match)
    await interaction.response.send_message(
        f"Next match: **{event_name(match)}**\nKickoff: <t:{timestamp}:t> (<t:{timestamp}:R>)",
        ephemeral=True,
    )


async def fetch_next_match() -> dict | None:
    timeout = aiohttp.ClientTimeout(total=20)
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=7)
    date_range = f"{now:%Y%m%d}-{cutoff:%Y%m%d}"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            SCOREBOARD_URL, params={"limit": 500, "dates": date_range}
        ) as response:
            response.raise_for_status()
            payload = await response.json()

    upcoming = []
    for event in payload.get("events", []):
        try:
            start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        is_configured_team = any(
            str(item.get("team", {}).get("id")) == TEAM_ID for item in competitors
        )
        if now < start <= cutoff and is_configured_team:
            upcoming.append(event)
    return min(upcoming, key=lambda item: item["date"]) if upcoming else None


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "match-alert"})


class BarcelonaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = StateStore()
        self.tree.add_command(setchannel_command)
        self.tree.add_command(setrole_command)
        self.tree.add_command(nextmatch_command)
        self._commands_synced = False
        self.health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        await self.store.connect()
        app = web.Application()
        app.router.add_get("/", health)
        app.router.add_get("/health", health)
        self.health_runner = web.AppRunner(app)
        await self.health_runner.setup()
        await web.TCPSite(self.health_runner, "0.0.0.0", PORT).start()
        await self.tree.sync()
        self.poll_schedule.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)
        if not self._commands_synced:
            for guild in self.guilds:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            self._commands_synced = True
            logger.info("Synced slash commands to %d server(s)", len(self.guilds))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        owner = guild.owner
        if owner:
            try:
                await owner.send(
                    f"Thanks for adding me to **{guild.name}**! "
                    "Use `/setchannel` in a server channel to choose where match alerts should be posted."
                )
            except discord.Forbidden:
                logger.info("Could not DM the owner of %s", guild.name)

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll_schedule(self) -> None:
        try:
            match = await fetch_next_match()
            if not match:
                return

            match_id = str(match["id"])
            if await self.store.has_announced(match_id):
                return

            timestamp = kickoff_unix(match)
            sent_any = False
            for guild in self.guilds:
                guild_state = await self.store.get_guild(guild.id)
                channel_id = guild_state.get("channel_id")
                if not channel_id:
                    continue
                channel = guild.get_channel(channel_id)
                if channel is None:
                    logger.warning("Configured channel is unavailable in %s", guild.name)
                    continue
                role_id = guild_state.get("role_id")
                role_mention = f"<@&{role_id}> " if role_id else ""
                await channel.send(
                    f"{role_mention}{TEAM_NAME} match incoming: **{event_name(match)}**\n"
                    f"Kickoff: <t:{timestamp}:t>"
                )
                sent_any = True

            if sent_any:
                await self.store.mark_announced(match_id)
            logger.info("Announced %s", event_name(match))
        except Exception:
            logger.exception("Schedule poll failed")

    @poll_schedule.before_loop
    async def before_poll_schedule(self) -> None:
        await self.wait_until_ready()


BarcelonaBot().run(DISCORD_TOKEN)
