import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import asyncpg
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from teams import ALIASES, TEAMS, find_competition, find_team

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("match-alert-bot")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
# These are fallbacks for servers that have not configured a favourite team.
# Keep the legacy environment names as a compatibility fallback for existing deployments.
DEFAULT_TEAM_ID = os.getenv("DEFAULT_TEAM_ID", os.getenv("TEAM_ID", "83"))
DEFAULT_TEAM_NAME = os.getenv("DEFAULT_TEAM_NAME", os.getenv("TEAM_NAME", "FC Barcelona"))
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
REMINDER_RETENTION_HOURS = float(os.getenv("REMINDER_RETENTION_HOURS", "3"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))

# ESPN's public scoreboard endpoint. DEFAULT_TEAM_ID selects the fallback team to monitor.
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"
COMPETITION_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{competition}/scoreboard"


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
            return [(DEFAULT_TEAM_NAME, DEFAULT_TEAM_ID)]

        guild_state = self.local_state.get("guilds", {}).get(str(guild_id), {})
        if "teams" in guild_state and guild_state["teams"]:
            return [
                (team["team_name"], str(team["team_id"]))
                for team in guild_state["teams"]
            ]
        if guild_state.get("team_name") and guild_state.get("team_id"):
            return [(guild_state["team_name"], str(guild_state["team_id"]))]
        return [(DEFAULT_TEAM_NAME, DEFAULT_TEAM_ID)]

    async def get_team_settings(self, guild_id: int, team_id: str) -> dict:
        """Return one team's optional routing overrides, tolerating legacy records."""
        if self.pool:
            row = await self.pool.fetchrow(
                "SELECT team_name, team_id, channel_id, role_id FROM guild_teams WHERE guild_id=$1 AND team_id=$2",
                guild_id, str(team_id),
            )
            return dict(row) if row else {}
        for team in self.local_state.get("guilds", {}).get(str(guild_id), {}).get("teams", []):
            if str(team.get("team_id")) == str(team_id):
                return dict(team)
        return {}

    async def set_team_route(self, guild_id: int, team_name: str, team_id: str, *, channel_id=None, role_id=None) -> None:
        """Set only supplied team overrides; omitted values remain unchanged."""
        if self.pool:
            await self.pool.execute(
                """INSERT INTO guild_teams (guild_id, team_id, team_name, channel_id, role_id)
                VALUES ($1,$2,$3,$4,$5) ON CONFLICT (guild_id,team_id) DO UPDATE SET
                team_name=EXCLUDED.team_name, channel_id=COALESCE($4,guild_teams.channel_id), role_id=COALESCE($5,guild_teams.role_id)""",
                guild_id, str(team_id), team_name, channel_id, role_id,
            )
            return
        guild = self.local_state.setdefault("guilds", {}).setdefault(str(guild_id), {})
        teams = guild.setdefault("teams", [{"team_id": str(guild.get("team_id", DEFAULT_TEAM_ID)), "team_name": guild.get("team_name", DEFAULT_TEAM_NAME)}])
        team = next((item for item in teams if str(item.get("team_id")) == str(team_id)), None)
        if team is None:
            team = {"team_id": str(team_id), "team_name": team_name}
            teams.append(team)
        team["team_name"] = team_name
        if channel_id is not None: team["channel_id"] = channel_id
        if role_id is not None: team["role_id"] = role_id
        save_state(self.local_state)

    async def reset_team_route(self, guild_id: int, team_id: str, setting: str) -> None:
        if self.pool:
            columns = "channel_id=NULL" if setting == "channel" else "role_id=NULL" if setting == "role" else "channel_id=NULL, role_id=NULL"
            await self.pool.execute(f"UPDATE guild_teams SET {columns} WHERE guild_id=$1 AND team_id=$2", guild_id, str(team_id))
            return
        team = await self.get_team_settings(guild_id, team_id)
        for key in (("channel_id",) if setting == "channel" else ("role_id",) if setting == "role" else ("channel_id", "role_id")):
            team.pop(key, None)
        guild = self.local_state.get("guilds", {}).get(str(guild_id), {})
        for item in guild.get("teams", []):
            if str(item.get("team_id")) == str(team_id): item.clear(); item.update(team)
        save_state(self.local_state)

    async def save_lifecycle(self, guild_id: int, event_id: str, snapshot: dict) -> None:
        """Persist observed fixture state separately from disposable message metadata."""
        if self.pool:
            await self.pool.execute(
                """INSERT INTO match_lifecycle (guild_id, event_id, kickoff, status, team_ids, observed_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (guild_id, event_id) DO UPDATE SET kickoff=EXCLUDED.kickoff,
                status=EXCLUDED.status, team_ids=EXCLUDED.team_ids, observed_at=EXCLUDED.observed_at""",
                guild_id, event_id, snapshot.get("kickoff"), snapshot.get("status"), snapshot.get("team_ids", []), snapshot.get("observed_at"),
            )
            return
        self.local_state.setdefault("match_lifecycle", {})[f"{guild_id}:{event_id}"] = snapshot
        save_state(self.local_state)

    async def get_lifecycle(self, guild_id: int, event_id: str) -> dict | None:
        if self.pool:
            row = await self.pool.fetchrow("SELECT * FROM match_lifecycle WHERE guild_id=$1 AND event_id=$2", guild_id, event_id)
            return dict(row) if row else None
        return self.local_state.get("match_lifecycle", {}).get(f"{guild_id}:{event_id}")

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
                            else DEFAULT_TEAM_NAME
                        )
                        initial_id = (
                            str(legacy["team_id"])
                            if legacy and legacy["team_name"] and legacy["team_id"]
                            else DEFAULT_TEAM_ID
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
            initial_name = guild_state.get("team_name", DEFAULT_TEAM_NAME)
            initial_id = str(guild_state.get("team_id", DEFAULT_TEAM_ID))
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

    async def save_reminder(
        self,
        match_id: str,
        guild_id: int,
        channel_id: int,
        message_id: int,
        delete_after: datetime,
    ) -> None:
        """Store the Discord message that should be deleted after a match."""
        announced_id = f"{guild_id}:{match_id}"
        if self.pool:
            await self.pool.execute(
                """UPDATE announced_matches
                SET channel_id = $2, message_id = $3, delete_after = $4
                WHERE match_id = $1""",
                announced_id,
                channel_id,
                message_id,
                delete_after,
            )
            return
        reminders = self.local_state.setdefault("reminder_messages", [])
        reminders.append(
            {
                "match_id": announced_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "delete_after": delete_after.isoformat(),
            }
        )
        save_state(self.local_state)

    async def get_expired_reminders(self, guild_id: int, now: datetime) -> list[dict]:
        """Return this guild's reminder messages whose retention period elapsed."""
        if self.pool:
            rows = await self.pool.fetch(
                """SELECT match_id, channel_id, message_id
                FROM announced_matches
                WHERE match_id LIKE $1 AND channel_id IS NOT NULL AND message_id IS NOT NULL
                  AND delete_after IS NOT NULL AND delete_after <= $2""",
                f"{guild_id}:%",
                now,
            )
            return [dict(row) for row in rows]

        expired = []
        guild_prefix = f"{guild_id}:"
        for reminder in self.local_state.get("reminder_messages", []):
            if not reminder.get("match_id", "").startswith(guild_prefix):
                continue
            if not reminder.get("channel_id") or not reminder.get("message_id"):
                continue
            try:
                delete_after = datetime.fromisoformat(reminder["delete_after"])
            except (KeyError, ValueError):
                continue
            if delete_after <= now:
                expired.append(reminder)
        return expired

    async def get_pending_reminders(self) -> list[dict]:
        """Return all sent reminders that still have Discord messages."""
        if self.pool:
            rows = await self.pool.fetch(
                """SELECT match_id, channel_id, message_id
                FROM announced_matches
                WHERE channel_id IS NOT NULL AND message_id IS NOT NULL
                  AND delete_after IS NOT NULL"""
            )
            return [dict(row) for row in rows]
        return [
            reminder
            for reminder in self.local_state.get("reminder_messages", [])
            if reminder.get("channel_id") and reminder.get("message_id")
        ]

    async def save_delivery(self, guild_id: int, event_id: str, channel_id: int, message_id: int, delete_after: datetime) -> None:
        """Save one destination delivery independently of other destinations."""
        if self.pool:
            await self.pool.execute(
                """INSERT INTO alert_deliveries (guild_id,event_id,channel_id,message_id,delete_after)
                VALUES ($1,$2,$3,$4,$5) ON CONFLICT (guild_id,event_id,channel_id) DO UPDATE SET
                message_id=EXCLUDED.message_id, delete_after=EXCLUDED.delete_after""",
                guild_id, str(event_id), channel_id, message_id, delete_after,
            )
            return
        deliveries = self.local_state.setdefault("alert_deliveries", [])
        existing = next((item for item in deliveries if item.get("guild_id") == guild_id and item.get("event_id") == str(event_id) and item.get("channel_id") == channel_id), None)
        record = {"guild_id": guild_id, "event_id": str(event_id), "channel_id": channel_id, "message_id": message_id, "delete_after": delete_after.isoformat()}
        if existing: existing.update(record)
        else: deliveries.append(record)
        save_state(self.local_state)

    async def get_deliveries(self, guild_id: int, event_id: str) -> list[dict]:
        if self.pool:
            rows = await self.pool.fetch("SELECT * FROM alert_deliveries WHERE guild_id=$1 AND event_id=$2", guild_id, str(event_id))
            return [dict(row) for row in rows]
        return [item for item in self.local_state.get("alert_deliveries", []) if item.get("guild_id") == guild_id and item.get("event_id") == str(event_id)]

    async def clear_reminder(self, announced_id: str) -> None:
        """Clear reminder metadata while retaining duplicate-alert history."""
        if self.pool:
            await self.pool.execute(
                """UPDATE announced_matches
                SET channel_id = NULL, message_id = NULL, delete_after = NULL
                WHERE match_id = $1""",
                announced_id,
            )
            return
        reminders = [
            reminder
            for reminder in self.local_state.get("reminder_messages", [])
            if reminder.get("match_id") != announced_id
        ]
        self.local_state["reminder_messages"] = reminders
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
    return f"{names.get('home', DEFAULT_TEAM_NAME)} vs {names.get('away', 'opponent')}"


def event_has_team(event: dict, team_id: str) -> bool:
    """Return whether an event contains a saved club or national team."""
    competitors = event.get("competitions", [{}])[0].get("competitors", [])
    if not team_id.startswith("international:"):
        return any(str(item.get("team", {}).get("id")) == team_id for item in competitors)
    for competitor in competitors:
        team_name = competitor.get("team", {}).get("displayName")
        selected = find_team(team_name) if team_name else None
        if selected and selected[1] == team_id:
            return True
    return False


def event_phase(event: dict) -> str:
    """Return whether an ESPN event is upcoming, live, or final."""
    status_type = event.get("status", {}).get("type", {})
    detail = " ".join(str(status_type.get(key, "")) for key in ("name", "detail", "description")).casefold()
    if any(word in detail for word in ("postponed", "abandoned", "cancelled", "canceled", "suspended")):
        return exceptional_status(event)
    if status_type.get("completed") or status_type.get("state") == "post":
        return "final"
    if status_type.get("state") == "in":
        return "live"
    return "upcoming"


def exceptional_status(event: dict) -> str:
    """Return an explicit exceptional ESPN status, or an empty string."""
    status_type = event.get("status", {}).get("type", {})
    detail = " ".join(str(status_type.get(key, "")) for key in ("name", "detail", "description")).casefold()
    for word, label in (("postponed", "postponed"), ("abandoned", "abandoned"),
                        ("cancelled", "cancelled"), ("canceled", "cancelled"), ("suspended", "suspended")):
        if word in detail:
            return label
    return ""


def normalize_team_query(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def team_suggestions(query: str) -> list[tuple[str, str]]:
    """Return deterministic local autocomplete choices, without network calls."""
    needle = normalize_team_query(query)
    candidates = {}
    for key, (display, team_id) in TEAMS.items():
        if needle and needle not in key and not key.startswith(needle):
            continue
        candidates.setdefault(team_id, (display, key, 0 if key == needle else 1 if key.startswith(needle) else 2))
    for alias, key in ALIASES.items():
        selected = TEAMS.get(key)
        if selected and (alias == needle or alias.startswith(needle) or needle in alias):
            display, team_id = selected
            rank = 0 if alias == needle else 1 if alias.startswith(needle) else 2
            current = candidates.get(team_id)
            if current is None or rank < current[2]:
                candidates[team_id] = (display, key, rank)
    return [(display, key) for display, key, rank in sorted(candidates.values(), key=lambda item: (item[2], item[0].casefold())) if not needle or rank < 3][:25]


async def team_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=name[:100], value=value[:100]) for name, value in team_suggestions(current)]


def event_score(event: dict) -> str:
    """Format the current home and away score from an ESPN event."""
    competitors = event.get("competitions", [{}])[0].get("competitors", [])
    scores = {}
    for item in competitors:
        score = item.get("score", "?")
        if isinstance(score, dict):
            score = score.get("displayValue", score.get("value", "?"))
        scores[item.get("homeAway")] = str(score)
    return f"{scores.get('home', '?')}–{scores.get('away', '?')}"


def result_message(event: dict) -> str:
    """Format a live or final match update for a Discord reminder."""
    phase = event_phase(event)
    if phase in {"postponed", "abandoned", "cancelled", "suspended"}:
        return f"**{phase.upper()}**: **{event_name(event)}**"
    label = "LIVE" if phase == "live" else "FINAL"
    detail = event.get("status", {}).get("type", {}).get("detail")
    suffix = f" ({detail})" if detail and phase == "live" else ""
    return f"**{label}**{suffix}: **{event_name(event)}**\nScore: **{event_score(event)}**"


@app_commands.command(
    name="setchannel",
    description="Choose where match alerts are posted.",
)
@app_commands.describe(channel="The text channel for match alerts", team="Optional monitored team override")
@app_commands.autocomplete(team=team_autocomplete)
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel_command(interaction: discord.Interaction, channel: discord.TextChannel, team: str | None = None) -> None:
    """Configure the guild channel used for match alerts."""
    bot = interaction.client
    if not isinstance(bot, MatchAlertBot):
        return
    await interaction.response.defer(ephemeral=True)
    if team:
        selected = find_team(team)
        monitored = await bot.store.get_guild_teams(interaction.guild_id)
        if not selected or not any(team_id == selected[1] for _, team_id in monitored):
            await interaction.edit_original_response(content="Choose a supported team monitored by this server.")
            return
        await bot.store.set_team_route(interaction.guild_id, selected[0], selected[1], channel_id=channel.id)
    else:
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
@app_commands.describe(role="The role to mention", team="Optional monitored team override")
@app_commands.autocomplete(team=team_autocomplete)
@app_commands.checks.has_permissions(manage_guild=True)
async def setrole_command(interaction: discord.Interaction, role: discord.Role, team: str | None = None) -> None:
    """Configure the guild role mentioned in match alerts."""
    bot = interaction.client
    if not isinstance(bot, MatchAlertBot):
        return
    await interaction.response.defer(ephemeral=True)
    if team:
        selected = find_team(team)
        monitored = await bot.store.get_guild_teams(interaction.guild_id)
        if not selected or not any(team_id == selected[1] for _, team_id in monitored):
            await interaction.edit_original_response(content="Choose a supported team monitored by this server.")
            return
        await bot.store.set_team_route(interaction.guild_id, selected[0], selected[1], role_id=role.id)
    else:
        await bot.store.set_guild_value(interaction.guild_id, "role_id", role.id)
    await interaction.edit_original_response(
        content=f"I will mention {role.mention} in match alerts."
    )

@app_commands.command(name="configure", description="Choose this server's favourite team.")
@app_commands.describe(team="Team name, for example Real Madrid or Japan")
@app_commands.autocomplete(team=team_autocomplete)
@app_commands.checks.has_permissions(manage_guild=True)
async def configure_command(interaction: discord.Interaction, team: str) -> None:
    """Configure the guild's monitored team using the ESPN lookup table."""
    bot = interaction.client
    selected = find_team(team)
    if not isinstance(bot, MatchAlertBot):
        return
    await interaction.response.defer(ephemeral=True)
    if not selected:
        await interaction.edit_original_response(
            content="That team is not in the supported team lookup."
        )
        return
    display_name, team_id = selected
    await bot.store.set_guild_favourite(interaction.guild_id, display_name, team_id)
    await interaction.edit_original_response(
        content=f"This server's favourite team is now **{display_name}** (ESPN ID `{team_id}`)."
    )


@app_commands.command(name="addteam", description="Add a team to this server's match alerts.")
@app_commands.describe(team="Team name, for example Arsenal or Japan")
@app_commands.autocomplete(team=team_autocomplete)
@app_commands.checks.has_permissions(manage_guild=True)
async def addteam_command(interaction: discord.Interaction, team: str) -> None:
    """Add one team to the guild's monitored teams."""
    bot = interaction.client
    selected = find_team(team)
    if not isinstance(bot, MatchAlertBot):
        return
    await interaction.response.defer(ephemeral=True)
    if not selected:
        await interaction.edit_original_response(
            content="That team is not in the supported team lookup."
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
@app_commands.describe(team="Team name, for example Arsenal or Japan")
@app_commands.autocomplete(team=team_autocomplete)
@app_commands.checks.has_permissions(manage_guild=True)
async def removeteam_command(interaction: discord.Interaction, team: str) -> None:
    """Remove one team from the guild's monitored teams."""
    bot = interaction.client
    selected = find_team(team)
    if not isinstance(bot, MatchAlertBot):
        return
    await interaction.response.defer(ephemeral=True)
    if not selected:
        await interaction.edit_original_response(
            content="That team is not in the supported team lookup."
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
    favourite_id = str(settings.get("team_id", DEFAULT_TEAM_ID))
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
    favourite_id = str(settings.get("team_id", DEFAULT_TEAM_ID))
    content = "Teams monitored by this server:\n" + "\n".join(
        f"- **{team_name}**{' (favourite)' if team_id == favourite_id else ''}"
        for team_name, team_id in teams
    )
    await interaction.edit_original_response(content=content)


@app_commands.command(name="resetalerts", description="Reset a team's channel and/or role override.")
@app_commands.describe(team="Monitored team", setting="Override to reset")
@app_commands.autocomplete(team=team_autocomplete)
@app_commands.choices(setting=[app_commands.Choice(name="channel", value="channel"), app_commands.Choice(name="role", value="role"), app_commands.Choice(name="both", value="both")])
@app_commands.checks.has_permissions(manage_guild=True)
async def resetalerts_command(interaction: discord.Interaction, team: str, setting: str = "both") -> None:
    bot = interaction.client
    selected = find_team(team)
    await interaction.response.defer(ephemeral=True)
    monitored = await bot.store.get_guild_teams(interaction.guild_id)
    if not selected or not any(team_id == selected[1] for _, team_id in monitored):
        await interaction.edit_original_response(content="Choose a supported team monitored by this server.")
        return
    await bot.store.reset_team_route(interaction.guild_id, selected[1], setting)
    await interaction.edit_original_response(content=f"Reset {setting} alert override for **{selected[0]}**.")


@app_commands.command(name="help", description="Show the bot commands and examples.")
async def help_command(interaction: discord.Interaction) -> None:
    """Explain the available commands and how to use them."""
    content = (
        "**Match Alert — Commands**\n\n"
        "**Everyone**\n"
        "`/nextmatch` — Show the favourite team's next match within 7 days.\n"
        "Example: `/nextmatch`\n"
        "`/nextmatch team:Arsenal` — Check another supported team's next match.\n"
        "Example: `/nextmatch team:Real Madrid`\n"
        "`/nextmatch competition:Premier League` — Show the next match in a supported competition.\n"
        "Example: `/nextmatch competition:La Liga`\n"
        "`/teams` — List the teams monitored by this server.\n\n"
        "`/results [team:<team>] [limit:<1-10>]` — Show completed results from the last 30 days.\n\n"
        "**Server managers (Manage Server permission)**\n"
        "`/configure team:<club>` — Set the favourite team.\n"
        "Example: `/configure team:FC Barcelona`\n"
        "`/addteam team:<club>` — Add a team to match alerts.\n"
        "Example: `/addteam team:Arsenal`\n"
        "`/removeteam team:<club>` — Stop monitoring a team.\n"
        "Example: `/removeteam team:Arsenal`\n"
        "`/setchannel channel:<channel>` — Choose where alerts are posted.\n"
        "Example: `/setchannel channel:#football-alerts`\n"
        "`/setrole role:<role>` — Choose the role mentioned in alerts.\n"
        "Example: `/setrole role:@Football Fans`\n\n"
        "Team names must be from the supported team lookup, including the top 50 men's national teams. Competition names also include the five major leagues and second divisions, Primeira Liga, Eredivisie, Süper Lig, MLS, Saudi Pro League, UEFA Champions League, and domestic cups."
    )
    await interaction.response.send_message(content=content, ephemeral=True)


@app_commands.command(name="nextmatch", description="Show a team's or competition's next match in the next 7 days.")
@app_commands.describe(
    team="Optional team name, for example Real Madrid or Japan",
    competition="Optional competition name, for example Premier League",
)
@app_commands.autocomplete(team=team_autocomplete)
async def nextmatch_command(
    interaction: discord.Interaction,
    team: str | None = None,
    competition: str | None = None,
) -> None:
    """Show the next match for a team or in a supported competition within seven days."""
    await interaction.response.defer(ephemeral=True)
    try:
        if team is not None and competition is not None:
            await interaction.edit_original_response(
                content="Choose either a team or a competition, not both."
            )
            return

        if competition is not None:
            selected_competition = find_competition(competition)
            if not selected_competition:
                await interaction.edit_original_response(
                    content="That competition is not supported. Choose a supported top-five league, UEFA Champions League, or one of the five domestic cups."
                )
                return
            competition_name, competition_code = selected_competition
            match = await fetch_next_competition_match(competition_code)
            if not match:
                content = f"No {competition_name} match was found in the next 7 days."
            else:
                timestamp = kickoff_unix(match)
                content = (
                    f"Next {competition_name} match: **{event_name(match)}**\n"
                    f"Kickoff: <t:{timestamp}:f> (<t:{timestamp}:R>)"
                )
            await interaction.edit_original_response(content=content)
            return

        if team is not None:
            selected = find_team(team)
            if not selected:
                await interaction.edit_original_response(
                    content="That team is not in the supported team lookup."
                )
                return
            team_name, team_id = selected
        else:
            settings = await interaction.client.store.get_guild(interaction.guild_id)
            team_id = str(settings.get("team_id", DEFAULT_TEAM_ID))
            team_name = settings.get("team_name", DEFAULT_TEAM_NAME)

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
    except aiohttp.ClientError:
        logger.exception("/nextmatch ESPN data provider request failed")
        await interaction.edit_original_response(
            content="The match data service is temporarily unavailable. Please try again shortly."
        )
    except Exception:
        logger.exception("/nextmatch failed")
        await interaction.edit_original_response(
            content="I could not fetch the fixture right now. Check the bot logs for details."
        )


async def fetch_next_competition_match(competition_code: str) -> dict | None:
    """Fetch the earliest upcoming fixture for a supported ESPN competition."""
    events = await fetch_upcoming_events(competition_code=competition_code)
    return min(events, key=lambda event: event["date"], default=None)


async def fetch_upcoming_events(competition_code: str | None = None) -> list[dict]:
    """Fetch upcoming ESPN fixtures, optionally scoped to one competition."""
    timeout = aiohttp.ClientTimeout(total=20)
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=7)
    scoreboard_url = (
        COMPETITION_SCOREBOARD_URL.format(competition=competition_code)
        if competition_code
        else SCOREBOARD_URL
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # ESPN's soccer scoreboard endpoint accepts a single date reliably;
        # date ranges can return HTTP 400 on the soccer feed.
        payloads = []
        day = now.date()
        while day <= cutoff.date():
            date = day.strftime("%Y%m%d")
            async with session.get(
                scoreboard_url, params={"limit": 500, "dates": date}
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error("ESPN scoreboard request failed: %s %s: %s", response.status, date, body[:500])
                    response.raise_for_status()
                payloads.append(await response.json())
            day += timedelta(days=1)

    upcoming_events = []
    for payload in payloads:
        for event in payload.get("events", []):
            try:
                start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if now < start <= cutoff:
                upcoming_events.append(event)
    return upcoming_events


async def fetch_next_matches(team_ids: list[str]) -> dict[str, dict | None]:
    """Fetch each requested team's next upcoming ESPN fixture in one request."""
    team_ids = [str(team_id) for team_id in team_ids]
    next_matches = dict.fromkeys(team_ids)
    next_match_starts = {}
    if not team_ids:
        return next_matches

    for event in await fetch_upcoming_events():
        start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        for team_id in next_matches:
            if event_has_team(event, team_id) and (
                team_id not in next_match_starts or start < next_match_starts[team_id]
            ):
                next_matches[team_id] = event
                next_match_starts[team_id] = start
    return next_matches


async def fetch_recent_events(match_ids: list[str]) -> dict[str, dict]:
    """Fetch recently started events so sent reminders can show live results."""
    match_ids = {str(match_id) for match_id in match_ids}
    if not match_ids:
        return {}
    timeout = aiohttp.ClientTimeout(total=20)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=REMINDER_RETENTION_HOURS + 24)
    end = now + timedelta(days=1)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # The soccer scoreboard rejects date ranges (for example, 20260919-20260920)
        # with HTTP 400. Query individual days, as we do for upcoming fixtures.
        events = {}
        day = start.date()
        while day <= end.date():
            async with session.get(
                SCOREBOARD_URL, params={"limit": 500, "dates": day.strftime("%Y%m%d")}
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(
                        "ESPN recent-score request failed: %s %s: %s",
                        response.status,
                        day,
                        body[:500],
                    )
                    response.raise_for_status()
                payload = await response.json()
            events.update(
                {
                    str(event["id"]): event
                    for event in payload.get("events", [])
                    if str(event.get("id")) in match_ids
                }
            )
            day += timedelta(days=1)
    return events


async def fetch_recent_results(team_id: str, days: int = 30, limit: int = 10) -> list[dict]:
    """Fetch completed, non-exceptional results for a supported team."""
    timeout = aiohttp.ClientTimeout(total=8)
    now = datetime.now(timezone.utc)
    events = {}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for offset in range(days):
            day = (now.date() - timedelta(days=offset)).strftime("%Y%m%d")
            async with session.get(SCOREBOARD_URL, params={"limit": 500, "dates": day}) as response:
                if response.status >= 400:
                    response.raise_for_status()
                payload = await response.json()
            for event in payload.get("events", []):
                event_id = event.get("id")
                if not event_id or not event_has_team(event, str(team_id)) or event_phase(event) != "final":
                    continue
                try:
                    start = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
                    if not event_score_has_values(event):
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                events[str(event_id)] = (start, event)
            if len(events) >= limit:
                break
    return [event for _, event in sorted(events.values(), key=lambda pair: pair[0], reverse=True)[:limit]]


def event_score_has_values(event: dict) -> bool:
    competitors = event.get("competitions", [{}])[0].get("competitors", [])
    return len(competitors) >= 2 and all(item.get("score") is not None for item in competitors[:2])


def format_result(event: dict) -> str:
    timestamp = kickoff_unix(event)
    return f"**{event_name(event)}** — **{event_score(event)}** (<t:{timestamp}:d>)"


def lifecycle_snapshot(event: dict, team_ids: list[str] | None = None) -> dict:
    """Build a JSON/Postgres-friendly observation record for one ESPN event."""
    kickoff = None
    try:
        kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "event_id": str(event.get("id", "")),
        "kickoff": kickoff,
        "status": event_phase(event),
        "team_ids": [str(team_id) for team_id in (team_ids or [])],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def lifecycle_transition(previous: dict | None, event: dict) -> dict:
    """Classify an observation against a saved snapshot without guessing event identity."""
    current = lifecycle_snapshot(event)
    if not previous:
        current["transition"] = "baseline"
        return current
    old_kickoff = previous.get("kickoff")
    new_kickoff = current.get("kickoff")
    old_status = previous.get("status", "upcoming")
    new_status = current["status"]
    if old_kickoff and new_kickoff:
        try:
            kickoff_changed = datetime.fromisoformat(old_kickoff).astimezone(timezone.utc) != datetime.fromisoformat(new_kickoff).astimezone(timezone.utc)
        except ValueError:
            kickoff_changed = old_kickoff != new_kickoff
    else:
        kickoff_changed = old_kickoff != new_kickoff
    if kickoff_changed:
        current["transition"] = "rescheduled"
        current["old_kickoff"] = old_kickoff
    elif old_status != new_status:
        current["transition"] = f"{old_status}_to_{new_status}"
    else:
        current["transition"] = "unchanged"
    return current


def lifecycle_notice(previous: dict | None, event: dict) -> str | None:
    """Format a transition notice; return None when no notification is needed."""
    transition = lifecycle_transition(previous, event)
    phase = transition["status"]
    if transition["transition"] == "rescheduled":
        old = transition.get("old_kickoff")
        old_timestamp = int(datetime.fromisoformat(old).timestamp()) if old else None
        new_timestamp = kickoff_unix(event) if transition.get("kickoff") else None
        if old_timestamp is not None and new_timestamp is not None:
            return f"**RESCHEDULED**: **{event_name(event)}**\nWas: <t:{old_timestamp}:f>\nNow: <t:{new_timestamp}:f>"
    if phase in {"postponed", "abandoned", "cancelled", "suspended"} and transition["transition"] != "unchanged":
        return f"**{phase.upper()}**: **{event_name(event)}**"
    return None


@app_commands.command(name="results", description="Show a team's completed matches from the last 30 days.")
@app_commands.describe(team="Optional supported team or alias", limit="Number of results, from 1 to 10")
@app_commands.autocomplete(team=team_autocomplete)
async def results_command(interaction: discord.Interaction, team: str | None = None, limit: app_commands.Range[int, 1, 10] = 5) -> None:
    await interaction.response.defer(ephemeral=True)
    if interaction.guild_id is None and team is None:
        await interaction.edit_original_response(content="Choose a team when using /results in a DM.")
        return
    try:
        if team is None:
            settings = await interaction.client.store.get_guild(interaction.guild_id)
            selected = (settings.get("team_name", DEFAULT_TEAM_NAME), str(settings.get("team_id", DEFAULT_TEAM_ID)))
        else:
            selected = find_team(team)
        if not selected:
            await interaction.edit_original_response(content="That team is not in the supported team lookup.")
            return
        name, team_id = selected
        results = await fetch_recent_results(team_id, limit=limit)
        content = f"**{name} results — last 30 days**\n" + ("\n".join(format_result(event) for event in results) if results else "No completed results found.")
        await interaction.edit_original_response(content=content[:1900])
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await interaction.edit_original_response(content="The match data service is temporarily unavailable. Please try again shortly.")
    except Exception:
        logger.exception("/results failed")
        await interaction.edit_original_response(content="I could not fetch results right now. Please try again shortly.")


async def health(request: web.Request) -> web.Response:
    """Return the lightweight HTTP health-check response."""
    return web.json_response({"ok": True, "service": "match-alert"})


class MatchAlertBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = StateStore()
        self.tree.add_command(setchannel_command)
        self.tree.add_command(setrole_command)
        self.tree.add_command(resetalerts_command)
        self.tree.add_command(addteam_command)
        self.tree.add_command(removeteam_command)
        self.tree.add_command(teams_command)
        self.tree.add_command(help_command)
        self.tree.add_command(nextmatch_command)
        self.tree.add_command(results_command)
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

    async def cleanup_reminders(self) -> None:
        """Delete bot reminders after their matches have finished."""
        now = datetime.now(timezone.utc)
        for guild in self.guilds:
            for reminder in await self.store.get_expired_reminders(guild.id, now):
                announced_id = reminder["match_id"]
                channel = guild.get_channel(int(reminder["channel_id"]))
                if channel is None:
                    await self.store.clear_reminder(announced_id)
                    continue
                try:
                    message = await channel.fetch_message(int(reminder["message_id"]))
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    logger.warning("Cannot delete reminder %s in %s", reminder["message_id"], guild.name)
                    continue
                except discord.HTTPException:
                    logger.exception("Failed to delete reminder %s in %s", reminder["message_id"], guild.name)
                    continue
                await self.store.clear_reminder(announced_id)

    async def update_live_reminders(self) -> None:
        """Update sent reminders with live scores and final results."""
        reminders = await self.store.get_pending_reminders()
        match_ids = [reminder["match_id"].split(":", 1)[1] for reminder in reminders]
        events = await fetch_recent_events(match_ids)
        guilds = {guild.id: guild for guild in self.guilds}
        for reminder in reminders:
            announced_id = reminder["match_id"]
            try:
                guild_id, match_id = announced_id.split(":", 1)
                event = events.get(match_id)
                guild = guilds.get(int(guild_id))
                previous = await self.store.get_lifecycle(int(guild_id), match_id) if event is not None else None
                if event is None or guild is None or (event_phase(event) == "upcoming" and not lifecycle_notice(previous, event)):
                    continue
                channel = guild.get_channel(int(reminder["channel_id"]))
                if channel is None:
                    continue
                message = await channel.fetch_message(int(reminder["message_id"]))
                await message.edit(content=lifecycle_notice(previous, event) or result_message(event))
                await self.store.save_lifecycle(int(guild_id), match_id, lifecycle_snapshot(event))
            except discord.NotFound:
                # The lifecycle record survives message deletion. Re-post once in the
                # original destination so a later status transition remains visible.
                try:
                    replacement = await channel.send(
                        lifecycle_notice(previous, event) or result_message(event),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    delete_after = datetime.now(timezone.utc) + timedelta(hours=REMINDER_RETENTION_HOURS)
                    await self.store.save_reminder(
                        match_id, int(guild_id), channel.id, replacement.id, delete_after
                    )
                    await self.store.save_delivery(int(guild_id), match_id, channel.id, replacement.id, delete_after)
                    await self.store.save_lifecycle(int(guild_id), match_id, lifecycle_snapshot(event))
                except discord.HTTPException:
                    logger.exception("Failed to replace deleted reminder %s", match_id)
            except discord.Forbidden:
                logger.warning("Cannot update reminder %s", reminder["message_id"])
            except discord.HTTPException:
                logger.exception("Failed to update reminder %s", reminder["message_id"])

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll_schedule(self) -> None:
        """Poll each guild's configured teams and send new match alerts."""
        try:
            try:
                await self.update_live_reminders()
                await self.cleanup_reminders()
            except Exception:
                logger.exception("Reminder maintenance failed")
            guild_configs = []
            all_team_ids = set()
            for guild in self.guilds:
                guild_state = await self.store.get_guild(guild.id)
                teams = await self.store.get_guild_teams(guild.id)
                guild_configs.append((guild, guild_state, teams))
                all_team_ids.update(team_id for _, team_id in teams)

            matches = await fetch_next_matches(list(all_team_ids))
            for guild, guild_state, teams in guild_configs:
                destinations = {}
                for team_name, team_id in teams:
                    match = matches[team_id]
                    if not match:
                        continue
                    await self.store.save_lifecycle(
                        guild.id, str(match["id"]), lifecycle_snapshot(match, [team_id])
                    )
                    if exceptional_status(match):
                        continue
                    route = await self.store.get_team_settings(guild.id, team_id)
                    channel_id = route.get("channel_id") or guild_state.get("channel_id")
                    if not channel_id:
                        continue
                    destinations.setdefault((str(match["id"]), int(channel_id)), {"match": match, "teams": [], "roles": set()})
                    destinations[(str(match["id"]), int(channel_id))]["teams"].append(team_name)
                    role_id = route.get("role_id") or guild_state.get("role_id")
                    if role_id: destinations[(str(match["id"]), int(channel_id))]["roles"].add(int(role_id))
                for (match_id, channel_id), candidate in destinations.items():
                    deliveries = await self.store.get_deliveries(guild.id, match_id)
                    if deliveries:
                        if any(int(item.get("channel_id", 0)) == channel_id for item in deliveries):
                            continue
                    elif await self.store.has_announced(match_id, guild.id):
                        # Legacy rows without a saved channel conservatively suppress
                        # ordinary duplicates until a status transition requires one.
                        continue
                    channel = guild.get_channel(channel_id)
                    if channel is None:
                        logger.warning("Configured channel is unavailable in %s", guild.name)
                        continue
                    role_mention = " ".join(f"<@&{role_id}>" for role_id in sorted(candidate["roles"]))
                    role_mention = f"{role_mention} " if role_mention else ""
                    match = candidate["match"]
                    timestamp = kickoff_unix(match)
                    try:
                        message = await channel.send(
                            f"{role_mention}{', '.join(candidate['teams'])} match incoming: **{event_name(match)}**\n"
                            f"Kickoff: <t:{timestamp}:f> (<t:{timestamp}:R>)",
                            allowed_mentions=discord.AllowedMentions(roles=True),
                        )
                        await self.store.mark_announced(str(match["id"]), guild.id)
                        delete_after = datetime.fromtimestamp(timestamp, timezone.utc) + timedelta(
                            hours=REMINDER_RETENTION_HOURS
                        )
                        await self.store.save_reminder(
                            str(match["id"]), guild.id, channel.id, message.id, delete_after
                        )
                        await self.store.save_delivery(guild.id, str(match["id"]), channel.id, message.id, delete_after)
                        logger.info("Announced %s in %s", event_name(match), guild.name)
                    except discord.HTTPException:
                        logger.exception("Failed to announce %s in %s", event_name(match), guild.name)
        except Exception:
            logger.exception("Schedule poll failed")

    @poll_schedule.before_loop
    async def before_poll_schedule(self) -> None:
        await self.wait_until_ready()


if __name__ == "__main__":
    MatchAlertBot().run(DISCORD_TOKEN)
