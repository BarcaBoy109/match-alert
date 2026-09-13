import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("barca-bot")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
ANNOUNCEMENT_CHANNEL_ID = int(os.environ["ANNOUNCEMENT_CHANNEL_ID"])
ROLE_ID = int(os.environ["ROLE_ID"])
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

# ESPN's public scoreboard endpoint. Barcelona's ESPN team id is 83.
SCHEDULE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/teams/barcelona/schedule"
)


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
        self.poll_schedule.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll_schedule(self) -> None:
        try:
            match = await fetch_next_match()
            if not match:
                return

            match_id = str(match["id"])
            if self.state.get("last_announced_match_id") == match_id:
                return

            channel = self.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if channel is None:
                logger.error("Announcement channel %s was not found", ANNOUNCEMENT_CHANNEL_ID)
                return

            timestamp = kickoff_unix(match)
            role_mention = f"<@&{ROLE_ID}>"
            await channel.send(
                f"{role_mention} FC Barcelona match incoming: **{event_name(match)}**\n"
                f"Kickoff: <t:{timestamp}:t>"
            )
            self.state["last_announced_match_id"] = match_id
            save_state(self.state)
            logger.info("Announced %s", event_name(match))
        except Exception:
            logger.exception("Schedule poll failed")

    @poll_schedule.before_loop
    async def before_poll_schedule(self) -> None:
        await self.wait_until_ready()


BarcelonaBot().run(DISCORD_TOKEN)
