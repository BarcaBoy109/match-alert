import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("barca-bot")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

# ESPN's public scoreboard endpoint. Barcelona's ESPN team id is 83.
SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/83/schedule"


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


def kickoff_unix(event: dict) -> int:
    start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    return int(start.astimezone(timezone.utc).timestamp())


def event_name(event: dict) -> str:
    competition = event["competitions"][0]
    competitors = competition["competitors"]
    names = {item["homeAway"]: item["team"]["displayName"] for item in competitors}
    return f"{names.get('home', 'Barcelona')} vs {names.get('away', 'opponent')}"


async def fetch_next_match() -> dict | None:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(SCHEDULE_URL, params={"limit": 20}) as response:
            response.raise_for_status()
            payload = await response.json()

    now = datetime.now(timezone.utc)
    upcoming = []
    for event in payload.get("events", []):
        try:
            start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start > now and event.get("competitions"):
            upcoming.append(event)
    return min(upcoming, key=lambda item: item["date"]) if upcoming else None


class BarcelonaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.state = load_state()

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self.poll_schedule.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        owner = guild.owner
        if owner:
            try:
                await owner.send(
                    f"Thanks for adding me to **{guild.name}**! "
                    "Use `/setchannel` in a server channel to choose where Barcelona match alerts should be posted."
                )
            except discord.Forbidden:
                logger.info("Could not DM the owner of %s", guild.name)

    @app_commands.command(
        name="setchannel",
        description="Choose where FC Barcelona match alerts are posted.",
    )
    @app_commands.describe(channel="The text channel for match alerts")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild_state = self.state.setdefault("guilds", {}).setdefault(str(interaction.guild_id), {})
        guild_state["channel_id"] = channel.id
        save_state(self.state)
        await interaction.response.send_message(
            f"Match alerts will be posted in {channel.mention}.", ephemeral=True
        )

    @setchannel.error
    async def setchannel_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
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
    async def setrole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        guild_state = self.state.setdefault("guilds", {}).setdefault(str(interaction.guild_id), {})
        guild_state["role_id"] = role.id
        save_state(self.state)
        await interaction.response.send_message(
            f"I will mention {role.mention} in match alerts.", ephemeral=True
        )

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll_schedule(self) -> None:
        try:
            match = await fetch_next_match()
            if not match:
                return

            match_id = str(match["id"])
            if match_id in self.state.get("announced_matches", []):
                return

            timestamp = kickoff_unix(match)
            for guild in self.guilds:
                guild_state = self.state.get("guilds", {}).get(str(guild.id), {})
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
                    f"{role_mention}FC Barcelona match incoming: **{event_name(match)}**\n"
                    f"Kickoff: <t:{timestamp}:t>"
                )

            self.state.setdefault("announced_matches", []).append(match_id)
            self.state["announced_matches"] = self.state["announced_matches"][-50:]
            save_state(self.state)
            logger.info("Announced %s", event_name(match))
        except Exception:
            logger.exception("Schedule poll failed")

    @poll_schedule.before_loop
    async def before_poll_schedule(self) -> None:
        await self.wait_until_ready()


BarcelonaBot().run(DISCORD_TOKEN)
