import tempfile
import unittest
from datetime import datetime, timezone

import bot
from teams import find_competition, find_team


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self):
        return {"events": [{"id": "42"}]}

    async def text(self):
        return ""

    def raise_for_status(self):
        return None


class _Session:
    requested_dates = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get(self, url, *, params):
        self.requested_dates.append(params["dates"])
        return _Response()


class FetchRecentEventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_queries_each_scoreboard_date_individually(self):
        original_session = bot.aiohttp.ClientSession
        _Session.requested_dates = []
        bot.aiohttp.ClientSession = _Session
        try:
            events = await bot.fetch_recent_events(["42"])
        finally:
            bot.aiohttp.ClientSession = original_session

        self.assertEqual(events, {"42": {"id": "42"}})
        self.assertGreaterEqual(len(_Session.requested_dates), 2)
        self.assertTrue(all("-" not in date for date in _Session.requested_dates))


class NationalTeamTests(unittest.TestCase):
    def test_top_fifty_national_team_lookup_includes_common_aliases(self):
        self.assertEqual(find_team("Japan"), ("Japan", "international:japan"))
        self.assertEqual(find_team("La Roja"), ("Spain", "international:spain"))
        self.assertEqual(find_team("España"), ("Spain", "international:spain"))
        self.assertEqual(
            find_team("USA"), ("United States", "international:united-states")
        )
        self.assertEqual(
            find_team("Czechia"), ("Czech Republic", "international:czech-republic")
        )

    def test_national_team_is_matched_using_espn_event_name(self):
        event = {
            "competitions": [
                {"competitors": [{"team": {"displayName": "Korea Republic"}}]}
            ]
        }
        self.assertTrue(bot.event_has_team(event, "international:south-korea"))

    def test_current_second_division_club_is_strictly_catalogued(self):
        team_name, team_id = find_team("Middlesbrough")
        self.assertEqual((team_name, team_id), ("Middlesbrough", "369"))
        event = {"competitions": [{"competitors": [{"team": {"id": "369"}}]}]}
        self.assertTrue(bot.event_has_team(event, team_id))
        self.assertIsNone(find_team("Not A Real Club"))

    def test_new_leagues_use_numeric_espn_team_ids(self):
        self.assertEqual(find_team("FC Porto"), ("FC Porto", "437"))
        self.assertEqual(find_team("LA Galaxy"), ("LA Galaxy", "187"))
        self.assertEqual(find_team("Al Hilal"), ("Al Hilal", "929"))



class CompetitionTests(unittest.TestCase):
    def test_requested_league_aliases_resolve_to_espn_competitions(self):
        self.assertEqual(find_competition("Championship"), ("EFL Championship", "eng.2"))
        self.assertEqual(find_competition("MLS"), ("Major League Soccer", "usa.1"))
        self.assertEqual(find_competition("Saudi League"), ("Saudi Pro League", "ksa.1"))
        self.assertEqual(find_competition("UEL"), ("UEFA Europa League", "uefa.europa"))
        self.assertEqual(find_competition("UECL"), ("UEFA Conference League", "uefa.europa.conf"))


class LifecycleTests(unittest.TestCase):
    def event(self, date, detail=None):
        status = {"state": "pre", "completed": False}
        if detail:
            status["detail"] = detail
        return {"id": "e1", "date": date, "status": {"type": status}, "competitions": [{"competitors": []}]}

    def test_equivalent_timezone_is_not_rescheduled(self):
        previous = bot.lifecycle_snapshot(self.event("2026-09-24T10:00:00Z"))
        current = bot.lifecycle_transition(previous, self.event("2026-09-24T18:00:00+08:00"))
        self.assertEqual(current["transition"], "unchanged")

    def test_changed_kickoff_is_rescheduled(self):
        previous = bot.lifecycle_snapshot(self.event("2026-09-24T10:00:00Z"))
        current = bot.lifecycle_transition(previous, self.event("2026-09-25T10:00:00Z"))
        self.assertEqual(current["transition"], "rescheduled")
        self.assertEqual(current["old_kickoff"], previous["kickoff"])

    def test_exceptional_status_is_not_final(self):
        event = self.event("2026-09-24T10:00:00Z", "Postponed")
        self.assertEqual(bot.event_phase(event), "postponed")

    def test_reschedule_notice_contains_both_discord_timestamps(self):
        previous = bot.lifecycle_snapshot(self.event("2026-09-24T10:00:00Z"))
        notice = bot.lifecycle_notice(previous, self.event("2026-09-25T10:00:00Z"))
        self.assertIn("RESCHEDULED", notice)
        self.assertEqual(notice.count("<t:"), 2)


class AutocompleteTests(unittest.TestCase):
    def test_aliases_deduplicate_to_canonical_choices(self):
        choices = bot.team_suggestions("barca")
        self.assertTrue(choices)
        self.assertEqual(len({bot.find_team(value)[1] for _, value in choices}), len(choices))
        self.assertTrue(any(name == "Barcelona" and bot.find_team(value) for name, value in choices))

    def test_suggestions_are_bounded_and_round_trip(self):
        choices = bot.team_suggestions("")
        self.assertLessEqual(len(choices), 25)
        for _, value in choices:
            self.assertIsNotNone(bot.find_team(value))

    def test_exact_match_precedes_prefix_match(self):
        choices = bot.team_suggestions("Japan")
        self.assertEqual(choices[0][0], "Japan")


class JsonPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_and_delivery_round_trip(self):
        original_file = bot.STATE_FILE
        with tempfile.TemporaryDirectory() as directory:
            bot.STATE_FILE = bot.Path(directory) / "state.json"
            try:
                store = bot.StateStore()
                snapshot = {"kickoff": "2026-09-24T10:00:00+00:00", "status": "upcoming", "team_ids": ["83"], "observed_at": "2026-09-23T00:00:00+00:00"}
                await store.save_lifecycle(1, "event-1", snapshot)
                await store.save_delivery(1, "event-1", 20, 30, datetime.now(timezone.utc))
                restarted = bot.StateStore()
                self.assertEqual((await restarted.get_lifecycle(1, "event-1"))["kickoff"], snapshot["kickoff"])
                self.assertEqual((await restarted.get_deliveries(1, "event-1"))[0]["channel_id"], 20)
            finally:
                bot.STATE_FILE = original_file
