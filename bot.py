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
from teams import find_team

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
    """Load persisted local state, returning an empty state if unavailable."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s; starting with empty state", STATE_FILE)
        return {}


def save_state(state: dict) -> None:
    """Persist local fallback state as formatted JSON."""
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


class StateStore:
    """Use Supabase Postgres in production, with JSON as a local fallback."""

    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None
        self.local_state = load_state()

    async def connect(self) -> None:
        """Open the Postgres pool when a database URL is configured."""
        if DATABASE_URL:
            self.pool = await asyncpg.create_pool(DATABASE_URL, ssl="require", min_size=1, max_size=3)

    async def get_guild(self, guild_id: int) -> dict:
        """Return channel, role, and team settings for a Discord guild."""
        if self.pool:
            row = await self.pool.fetchrow(
                "SELECT channel_id, role_id, team_id, team_name FROM guild_settings WHERE guild_id = $1", guild_id
            )
            return dict(row) if row else {}
        return self.local_state.get("guilds", {}).get(str(guild_id), {})

    async def set_guild_value(self, guild_id: int, key: str, value: int) -> None:
        """Set one supported guild setting in Postgres or local JSON state."""
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

    async def has_announced(self, match_id: str, guild_id: int) -> bool:
        """Check whether a match alert was already sent in a guild."""
        match_id = f"{guild_id}:{match_id}"
        if self.pool:
            return await self.pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM announced_matches WHERE match_id = $1)", match_id
            )
        return match_id in self.local_state.get("announced_matches", [])

    async def mark_announced(self, match_id: str, guild_id: int) -> None:
        """Record that a match alert was sent in a guild."""
        match_id = f"{guild_id}:{match_id}"
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
    """Convert an ESPN event date to a UTC Unix timestamp."""
    start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    return int(start.astimezone(timezone.utc).timestamp())


def event_name(event: dict) -> str:
    """Format the home and away team names from an ESPN event."""
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
    """Configure the guild channel used for match alerts."""
    bot = interaction.client
    if not isinstance(bot, BarcelonaBot):
        return
    await interaction.response.defer(ephemeral=True)
    await bot.store.set_guild_value(interaction.guild_id, "channel_id", channel.id)
    await interaction.edit_original_response(
        content=f"Match alerts will be posted in {channel.mention}."
    )


@setchannel_command.error
async def setchannel_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Respond to permission or unexpected errors from ``/setchannel``."""
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
    """Configure the guild role mentioned in match alerts."""
    bot = interaction.client
    if not isinstance(bot, BarcelonaBot):
        return
    await interaction.response.defer(ephemeral=True)
    await bot.store.set_guild_value(interaction.guild_id, "role_id", role.id)
    await interaction.edit_original_response(
        content=f"I will mention {role.mention} in match alerts."
    )

@app_commands.command(name="configure", description="Choose the team to monitor.")
@app_commands.describe(team="Club name, for example Real Madrid")
@app_commands.checks.has_permissions(manage_guild=True)
async def configure_command(interaction: discord.Interaction, team: str) -> None:
    """Configure the guild's monitored team using the ESPN lookup table."""
    bot = interaction.client
    selected = find_team(team)
    if not isinstance(bot, BarcelonaBot):
        return
    await interaction.response.defer(ephemeral=True)
    if not selected:
        await interaction.edit_original_response(
            content="That team is not in the supported top-five leagues lookup."
        )
        return
    display_name, team_id = selected
    await bot.store.set_guild_value(interaction.guild_id, "team_id", team_id)
    await bot.store.set_guild_value(interaction.guild_id, "team_name", display_name)
    await interaction.edit_original_response(
        content=f"This server will now follow **{display_name}** (ESPN ID `{team_id}`)."
    )


@app_commands.command(name="nextmatch", description="Show a team's next match in the next 7 days.")
@app_commands.describe(team="Optional club name, for example Real Madrid")
async def nextmatch_command(interaction: discord.Interaction, team: str | None = None) -> None:
    """Show the configured or requested team's next match within seven days."""
    await interaction.response.defer(ephemeral=True)
    try:
        if team is not None:
            selected = find_team(team)
            if not selected:
                await interaction.edit_original_response(
                    content="That team is not in the supported top-five leagues lookup."
                )
                return
            team_name, team_id = selected
        else:
            settings = await interaction.client.store.get_guild(interaction.guild_id)
            team_id = str(settings.get("team_id", TEAM_ID))
            team_name = settings.get("team_name", TEAM_NAME)

        match = await fetch_next_match(team_id, team_name)
        if not match:
            content = f"No {team_name} match was found in the next 7 days."
        else:
            timestamp = kickoff_unix(match)
            content = (
                f"Next match: **{event_name(match)}**\n"
                f"Kickoff: <t:{timestamp}:f> (<t:{timestamp}:R>)"
            )
        await interaction.edit_original_response(content=content)
    except Exception:
        logger.exception("/nextmatch failed")
        await interaction.edit_original_response(
            content="I could not fetch the fixture right now. Check the bot logs for details."
        )


async def fetch_next_match(team_id: str = TEAM_ID, team_name: str = TEAM_NAME) -> dict | None:
    """Fetch the configured team's next upcoming ESPN fixture."""
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
            str(item.get("team", {}).get("id")) == team_id for item in competitors
        )
        if now < start <= cutoff and is_configured_team:
            upcoming.append(event)
    return min(upcoming, key=lambda item: item["date"]) if upcoming else None


async def health(request: web.Request) -> web.Response:
    """Return the lightweight HTTP health-check response."""
    return web.json_response({"ok": True, "service": "match-alert"})


class BarcelonaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = StateStore()
        self.tree.add_command(setchannel_command)
        self.tree.add_command(setrole_command)
        self.tree.add_command(nextmatch_command)
        self.tree.add_command(configure_command)
        self._commands_synced = False
        self.health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        """Connect storage, start health serving, and sync Discord commands."""
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
        """Log readiness and synchronize commands for joined guilds."""
        logger.info("Logged in as %s", self.user)
        if not self._commands_synced:
            for guild in self.guilds:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            self._commands_synced = True
            logger.info("Synced slash commands to %d server(s)", len(self.guilds))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Welcome a newly joined guild owner with initial setup guidance."""
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
        """Poll each guild's configured team and send new match alerts."""
        try:
            for guild in self.guilds:
                guild_state = await self.store.get_guild(guild.id)
                team_id = str(guild_state.get("team_id", TEAM_ID))
                team_name = guild_state.get("team_name", TEAM_NAME)
                match = await fetch_next_match(team_id, team_name)
                if not match or await self.store.has_announced(str(match["id"]), guild.id):
                    continue
                timestamp = kickoff_unix(match)
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
                    f"{role_mention}{team_name} match incoming: **{event_name(match)}**\n"
                    f"Kickoff: <t:{timestamp}:f> (<t:{timestamp}:R>)"
                )
                await self.store.mark_announced(str(match["id"]), guild.id)
                logger.info("Announced %s in %s", event_name(match), guild.name)
        except Exception:
            logger.exception("Schedule poll failed")

    @poll_schedule.before_loop
    async def before_poll_schedule(self) -> None:
        await self.wait_until_ready()


BarcelonaBot().run(DISCORD_TOKEN)
