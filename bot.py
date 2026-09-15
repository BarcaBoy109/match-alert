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

    async def set_guild_value(self, guild_id: int, key: str, value: int | str) -> None:
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

    async def get_guild_teams(self, guild_id: int) -> list[tuple[str, str]]:
        """Return the teams monitored by a guild, including legacy settings."""
        if self.pool:
            rows = await self.pool.fetch(
                "SELECT team_name, team_id FROM guild_teams WHERE guild_id = $1 ORDER BY team_name",
                guild_id,
            )
            if rows:
                return [(row["team_name"], str(row["team_id"])) for row in rows]

            legacy = await self.pool.fetchrow(
                "SELECT team_name, team_id FROM guild_settings WHERE guild_id = $1",
                guild_id,
            )
            if legacy and legacy["team_name"] and legacy["team_id"]:
                return [(legacy["team_name"], str(legacy["team_id"]))]
            return [(TEAM_NAME, TEAM_ID)]

        guild_state = self.local_state.get("guilds", {}).get(str(guild_id), {})
        if "teams" in guild_state and guild_state["teams"]:
            return [
                (team["team_name"], str(team["team_id"]))
                for team in guild_state["teams"]
            ]
        if guild_state.get("team_name") and guild_state.get("team_id"):
            return [(guild_state["team_name"], str(guild_state["team_id"]))]
        return [(TEAM_NAME, TEAM_ID)]

    async def set_guild_favourite(self, guild_id: int, team_name: str, team_id: str) -> None:
        """Set the favourite team and ensure it is included in match alerts."""
        if self.pool:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    existing_teams = await connection.fetch(
                        "SELECT team_id FROM guild_teams WHERE guild_id = $1", guild_id
                    )
                    legacy = await connection.fetchrow(
                        "SELECT team_name, team_id FROM guild_settings WHERE guild_id = $1",
                        guild_id,
                    )
                    if (
                        not existing_teams
                        and legacy
                        and legacy["team_name"]
                        and legacy["team_id"]
                    ):
                        await connection.execute(
                            """INSERT INTO guild_teams (guild_id, team_id, team_name)
                            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
                            guild_id,
                            str(legacy["team_id"]),
                            legacy["team_name"],
                        )
                    await connection.execute(
                        """INSERT INTO guild_settings (guild_id, team_id, team_name) VALUES ($1, $2, $3)
                        ON CONFLICT (guild_id) DO UPDATE
                        SET team_id = EXCLUDED.team_id, team_name = EXCLUDED.team_name""",
                        guild_id,
                        team_id,
                        team_name,
                    )
                    await connection.execute(
                        """INSERT INTO guild_teams (guild_id, team_id, team_name)
                        VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
                        guild_id,
                        team_id,
                        team_name,
                    )
            return

        guild_state = self.local_state.setdefault("guilds", {}).setdefault(str(guild_id), {})
        if "teams" not in guild_state:
            guild_state["teams"] = []
            if guild_state.get("team_name") and guild_state.get("team_id"):
                guild_state["teams"].append(
                    {
                        "team_id": str(guild_state["team_id"]),
                        "team_name": guild_state["team_name"],
                    }
                )
        guild_state["team_id"] = team_id
        guild_state["team_name"] = team_name
        if not any(str(team["team_id"]) == team_id for team in guild_state["teams"]):
            guild_state["teams"].append({"team_id": team_id, "team_name": team_name})
        guild_state["teams"].sort(key=lambda team: team["team_name"])
        save_state(self.local_state)

    async def add_guild_team(self, guild_id: int, team_name: str, team_id: str) -> bool:
        """Add a monitored team and return whether it was newly added."""
        if self.pool:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    rows = await connection.fetch(
                        "SELECT team_id FROM guild_teams WHERE guild_id = $1", guild_id
                    )
                    if not rows:
                        legacy = await connection.fetchrow(
                            "SELECT team_name, team_id FROM guild_settings WHERE guild_id = $1",
                            guild_id,
                        )
                        initial_name = (
                            legacy["team_name"]
                            if legacy and legacy["team_name"] and legacy["team_id"]
                            else TEAM_NAME
                        )
                        initial_id = (
                            str(legacy["team_id"])
                            if legacy and legacy["team_name"] and legacy["team_id"]
                            else TEAM_ID
                        )
                        await connection.execute(
                            """INSERT INTO guild_teams (guild_id, team_id, team_name)
                            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
                            guild_id,
                            initial_id,
                            initial_name,
                        )
                    result = await connection.execute(
                        """INSERT INTO guild_teams (guild_id, team_id, team_name)
                        VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
                        guild_id,
                        team_id,
                        team_name,
                    )
            return result == "INSERT 0 1"

        guild_state = self.local_state.setdefault("guilds", {}).setdefault(str(guild_id), {})
        if "teams" not in guild_state or not guild_state["teams"]:
            initial_name = guild_state.get("team_name", TEAM_NAME)
            initial_id = str(guild_state.get("team_id", TEAM_ID))
            guild_state["teams"] = [{"team_id": initial_id, "team_name": initial_name}]
        if any(str(team["team_id"]) == team_id for team in guild_state["teams"]):
            return False
        guild_state["teams"].append({"team_id": team_id, "team_name": team_name})
        guild_state["teams"].sort(key=lambda team: team["team_name"])
        save_state(self.local_state)
        return True

    async def remove_guild_team(self, guild_id: int, team_id: str) -> bool:
        """Remove one monitored team and return whether it existed."""
        if self.pool:
            result = await self.pool.execute(
                "DELETE FROM guild_teams WHERE guild_id = $1 AND team_id = $2",
                guild_id,
                team_id,
            )
            return result == "DELETE 1"

        guild_state = self.local_state.setdefault("guilds", {}).setdefault(str(guild_id), {})
        teams = guild_state.get("teams", [])
        remaining = [team for team in teams if str(team["team_id"]) != team_id]
        if len(remaining) == len(teams):
            return False
        guild_state["teams"] = remaining
        save_state(self.local_state)
        return True

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

@app_commands.command(name="configure", description="Choose this server's favourite team.")
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
    await bot.store.set_guild_favourite(interaction.guild_id, display_name, team_id)
    await interaction.edit_original_response(
        content=f"This server's favourite team is now **{display_name}** (ESPN ID `{team_id}`)."
    )


@app_commands.command(name="addteam", description="Add a team to this server's match alerts.")
@app_commands.describe(team="Club name, for example Arsenal")
@app_commands.checks.has_permissions(manage_guild=True)
async def addteam_command(interaction: discord.Interaction, team: str) -> None:
    """Add one team to the guild's monitored teams."""
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
    added = await bot.store.add_guild_team(interaction.guild_id, display_name, team_id)
    if added:
        content = f"Added **{display_name}** to this server's match alerts."
    else:
        content = f"**{display_name}** is already monitored by this server."
    await interaction.edit_original_response(content=content)


@app_commands.command(name="removeteam", description="Remove a team from this server's match alerts.")
@app_commands.describe(team="Club name, for example Arsenal")
@app_commands.checks.has_permissions(manage_guild=True)
async def removeteam_command(interaction: discord.Interaction, team: str) -> None:
    """Remove one team from the guild's monitored teams."""
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
    teams = await bot.store.get_guild_teams(interaction.guild_id)
    if not any(saved_id == team_id for _, saved_id in teams):
        await interaction.edit_original_response(
            content=f"**{display_name}** is not monitored by this server."
        )
        return
    settings = await bot.store.get_guild(interaction.guild_id)
    favourite_id = str(settings.get("team_id", TEAM_ID))
    if team_id == favourite_id:
        await interaction.edit_original_response(
            content="The favourite team cannot be removed. Use `/configure` to choose a new favourite first."
        )
        return
    if len(teams) == 1:
        await interaction.edit_original_response(
            content="A server must monitor at least one team. Use `/configure` to replace it."
        )
        return
    await bot.store.remove_guild_team(interaction.guild_id, team_id)
    await interaction.edit_original_response(
        content=f"Removed **{display_name}** from this server's match alerts."
    )


@app_commands.command(name="teams", description="List the teams monitored by this server.")
async def teams_command(interaction: discord.Interaction) -> None:
    """List the guild's monitored teams."""
    await interaction.response.defer(ephemeral=True)
    teams = await interaction.client.store.get_guild_teams(interaction.guild_id)
    settings = await interaction.client.store.get_guild(interaction.guild_id)
    favourite_id = str(settings.get("team_id", TEAM_ID))
    content = "Teams monitored by this server:\n" + "\n".join(
        f"- **{team_name}**{' (favourite)' if team_id == favourite_id else ''}"
        for team_name, team_id in teams
    )
    await interaction.edit_original_response(content=content)


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

        match = (await fetch_next_matches([team_id]))[team_id]
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


async def fetch_next_matches(team_ids: list[str]) -> dict[str, dict | None]:
    """Fetch each requested team's next upcoming ESPN fixture in one request."""
    team_ids = [str(team_id) for team_id in team_ids]
    next_matches = dict.fromkeys(team_ids)
    next_match_starts = {}
    if not team_ids:
        return next_matches

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

    for event in payload.get("events", []):
        try:
            start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        if not now < start <= cutoff:
            continue
        competitor_ids = {
            str(item.get("team", {}).get("id")) for item in competitors
        }
        for team_id in competitor_ids.intersection(next_matches):
            if team_id not in next_match_starts or start < next_match_starts[team_id]:
                next_matches[team_id] = event
                next_match_starts[team_id] = start
    return next_matches


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
        self.tree.add_command(addteam_command)
        self.tree.add_command(removeteam_command)
        self.tree.add_command(teams_command)
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
        """Poll each guild's configured teams and send new match alerts."""
        try:
            guild_configs = []
            all_team_ids = set()
            for guild in self.guilds:
                guild_state = await self.store.get_guild(guild.id)
                teams = await self.store.get_guild_teams(guild.id)
                guild_configs.append((guild, guild_state, teams))
                all_team_ids.update(team_id for _, team_id in teams)

            matches = await fetch_next_matches(list(all_team_ids))
            for guild, guild_state, teams in guild_configs:
                channel_id = guild_state.get("channel_id")
                if not channel_id:
                    continue
                channel = guild.get_channel(channel_id)
                if channel is None:
                    logger.warning("Configured channel is unavailable in %s", guild.name)
                    continue
                role_id = guild_state.get("role_id")
                role_mention = f"<@&{role_id}> " if role_id else ""
                for team_name, team_id in teams:
                    match = matches[team_id]
                    if not match or await self.store.has_announced(str(match["id"]), guild.id):
                        continue
                    timestamp = kickoff_unix(match)
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
