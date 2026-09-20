import unittest

import bot


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
