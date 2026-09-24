import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

import bot


def http_error(status=429, headers=None):
    response = SimpleNamespace(status=status, reason="test", headers=headers or {})
    return bot.discord.HTTPException(response, "test failure")


class RetryDelayTests(unittest.TestCase):
    def test_valid_delays_are_not_capped_by_backoff(self):
        self.assertEqual(bot.retry_after_seconds(http_error(headers={"Retry-After": "7200"}), 300), 7200)

    def test_bad_delays_use_fallback(self):
        for value in ("nan", "inf", "-inf", "-10", "garbage", None):
            with self.subTest(value=value):
                self.assertEqual(bot.retry_after_seconds(http_error(headers={"Retry-After": value}), 300), 300)

    def test_http_date(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        delay = bot.retry_after_seconds(http_error(headers={"Retry-After": format_datetime(future)}), 300)
        self.assertGreater(delay, 1198)
        self.assertLessEqual(delay, 1200)


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_blocks_subsequent_http_requests_until_reset(self):
        """Exercise the real aiohttp trace path used by REST and webhooks."""
        clock = SimpleNamespace(now=10.0)
        cooldown = bot.DiscordCooldown()
        cooldown.hosts = {"127.0.0.1"}
        arrivals = []

        async def endpoint(request):
            arrivals.append(clock.now)
            if len(arrivals) == 1:
                return web.Response(status=429, text="temporarily blocked", headers={"Retry-After": "612"})
            return web.json_response({"ok": True})

        async def sleep(delay):
            clock.now += delay

        app = web.Application()
        app.router.add_get("/", endpoint)
        async with TestServer(app) as server:
            with patch.object(bot, "time", SimpleNamespace(monotonic=lambda: clock.now)):
                with patch.object(bot.asyncio, "sleep", side_effect=sleep):
                    async with aiohttp.ClientSession(trace_configs=[cooldown.trace_config()]) as session:
                        async with session.get(server.make_url("/")) as response:
                            self.assertEqual(response.status, 429)
                            # Trace inspection must leave the body readable by discord.py.
                            self.assertEqual(await response.text(), "temporarily blocked")
                        async with session.get(server.make_url("/")) as response:
                            self.assertEqual(response.status, 200)
        self.assertEqual(arrivals, [10.0, 623.0])

    async def test_normal_route_limit_is_left_to_discord_library(self):
        cooldown = bot.DiscordCooldown()
        response = SimpleNamespace(
            status=429, headers={"Via": "1.1 google", "Retry-After": "2"},
            json=AsyncMock(return_value={"retry_after": 2, "global": False}),
        )
        await cooldown.request_end(None, None, SimpleNamespace(url=URL("https://discord.com/api/v10/test"), response=response))
        self.assertEqual(cooldown.remaining, 0)

    async def test_global_body_delay_is_respected_and_secrets_are_not_logged(self):
        cooldown = bot.DiscordCooldown()
        response = SimpleNamespace(
            status=429, reason="rate limited", headers={"Via": "1.1 google"},
            json=AsyncMock(return_value={"retry_after": 120, "global": True}),
        )
        with self.assertLogs(bot.logger, level="WARNING") as logs:
            await cooldown.request_end(None, None, SimpleNamespace(
                url=URL("https://discord.com/api/v10/webhooks/123/SECRET"), response=response))
        self.assertGreater(cooldown.remaining, 120)
        self.assertNotIn("SECRET", " ".join(logs.output))

    async def test_shorter_block_does_not_shorten_existing_cooldown(self):
        cooldown = bot.DiscordCooldown()
        cooldown.defer(3600)
        until = cooldown.until
        cooldown.defer(5)
        self.assertEqual(cooldown.until, until)


class CommandSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = bot.MatchAlertBot()
        self.tree = self.client.tree

    async def asyncTearDown(self):
        await self.client.close()

    def remote(self):
        commands = []
        for index, command in enumerate(self.tree.get_commands()):
            data = command.to_dict(self.tree)
            data.update(id=str(index + 1), application_id="123", contexts=[0, 1, 2], integration_types=[0])
            commands.append(bot.app_commands.AppCommand(data=data, state=self.client._connection))
        return commands

    async def test_real_remote_models_with_optional_defaults_do_not_trigger_sync(self):
        with patch.object(self.tree, "fetch_commands", AsyncMock(return_value=self.remote())):
            with patch.object(self.tree, "sync", AsyncMock()) as sync:
                await bot.sync_commands_if_changed(self.tree)
                sync.assert_not_awaited()

    async def test_changed_command_and_permissions_trigger_sync(self):
        for kind in ("description", "permissions", "option", "missing"):
            remote = self.remote()
            if kind == "description":
                remote[0].description = "old description"
            elif kind == "permissions":
                remote[0].default_member_permissions = bot.discord.Permissions(8)
            elif kind == "option":
                remote[0].options[0].description = "old option"
            else:
                remote.pop()
            with self.subTest(kind=kind), patch.object(self.tree, "sync", AsyncMock()) as sync:
                await bot.sync_commands_if_changed(self.tree, existing=remote)
                sync.assert_awaited_once_with(guild=None)

    async def test_matching_explicit_permissions_do_not_trigger_sync(self):
        command = self.tree.get_commands()[0]
        original = command.default_permissions
        command.default_permissions = bot.discord.Permissions(8)
        try:
            with patch.object(self.tree, "sync", AsyncMock()) as sync:
                await bot.sync_commands_if_changed(self.tree, existing=self.remote())
                sync.assert_not_awaited()
        finally:
            command.default_permissions = original

    async def test_guild_sync_429_waits_without_restarting_bot(self):
        self.client._connection._guilds = {1: SimpleNamespace(id=1)}
        calls = []

        async def wait():
            calls.append(self.client.cooldown.remaining)
            self.client.cooldown.until = 0

        with patch.object(self.tree, "fetch_commands", AsyncMock(side_effect=[http_error(), []])):
            with patch.object(self.client.cooldown, "wait", side_effect=wait):
                await self.client.sync_legacy_guild_commands()
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1], 300)
        self.assertTrue(self.client._commands_synced)

    async def test_new_guild_does_not_get_duplicate_registrations(self):
        self.client._connection._guilds = {1: SimpleNamespace(id=1)}
        with patch.object(self.tree, "fetch_commands", AsyncMock(return_value=[])):
            with patch.object(self.tree, "sync", AsyncMock()) as sync:
                await self.client.sync_legacy_guild_commands()
                sync.assert_not_awaited()
                self.assertTrue(self.client._commands_synced)

    async def test_legacy_guild_commands_are_updated_when_changed(self):
        guild = SimpleNamespace(id=1)
        self.client._connection._guilds = {1: guild}
        remote = self.remote()
        remote.pop()
        with patch.object(self.tree, "fetch_commands", AsyncMock(return_value=remote)):
            with patch.object(self.tree, "sync", AsyncMock()) as sync:
                await self.client.sync_legacy_guild_commands()
                sync.assert_awaited_once_with(guild=guild)

    async def test_ready_reconnect_does_not_repeat_guild_sync(self):
        work = AsyncMock()
        with patch.object(self.client, "sync_legacy_guild_commands", work):
            await self.client.on_ready()
            await self.client._command_sync_task
            await self.client.on_ready()
            work.assert_awaited_once()


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_stays_healthy_while_readiness_reports_disconnected_and_blocked(self):
        cooldown = bot.DiscordCooldown()
        status = bot.ServiceStatus(cooldown)
        app = web.Application()
        app[bot.STATUS_KEY] = status
        app.router.add_get("/live", bot.liveness)
        app.router.add_get("/health", bot.health)
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/health")
            self.assertEqual(response.status, 503)
            status.bot = SimpleNamespace(is_ready=lambda: True, _commands_synced=True)
            response = await client.get("/health")
            self.assertEqual(response.status, 200)
            cooldown.defer(600)
            response = await client.get("/health")
            self.assertEqual(response.status, 503)
            self.assertEqual((await response.json())["status"], "rate_limited")
            response = await client.get("/live")
            self.assertEqual(response.status, 200)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_deployment_preserves_existing_cooldown_before_first_client(self):
        client = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
        runner = SimpleNamespace(cleanup=AsyncMock())

        async def wait(cooldown):
            self.assertGreater(cooldown.remaining, 600)
            factory.assert_not_called()
            cooldown.until = 0

        with patch.dict(bot.os.environ, {"DISCORD_NOT_BEFORE": str(bot.time.time() + 600)}):
            with patch.object(bot, "start_health_server", AsyncMock(return_value=runner)):
                with patch.object(bot, "MatchAlertBot", return_value=client) as factory:
                    with patch.object(bot.DiscordCooldown, "wait", wait):
                        await bot.run_bot()
        client.start.assert_awaited_once()
        runner.cleanup.assert_awaited_once()

    async def test_close_cancels_tasks_and_releases_database_even_after_task_failure(self):
        client = bot.MatchAlertBot()
        pool = SimpleNamespace(close=AsyncMock(), terminate=Mock())
        client.store.pool = pool

        async def failure():
            raise RuntimeError("background failure")

        client._command_sync_task = asyncio.create_task(failure())
        polling = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        with patch.object(client.poll_schedule, "get_task", return_value=polling):
            await client.close()
            await client.close()
        self.assertTrue(polling.cancelled())
        pool.close.assert_awaited_once()
        self.assertIsNone(client.store.pool)

    async def test_startup_closes_client_before_wait_and_reuses_cooldown(self):
        order = []
        clients = []
        shared_cooldowns = []

        def create(cooldown):
            shared_cooldowns.append(cooldown)
            client = SimpleNamespace()
            client.start = AsyncMock(side_effect=http_error(headers={"Retry-After": "600"}) if not clients else None)
            client.close = AsyncMock(side_effect=lambda: order.append("close"))
            clients.append(client)
            return client

        async def wait(cooldown):
            if clients:
                self.assertEqual(order, ["close"])
                self.assertGreater(cooldown.remaining, 600)
                order.append("wait")
                cooldown.until = 0

        runner = SimpleNamespace(cleanup=AsyncMock())
        with patch.object(bot, "start_health_server", AsyncMock(return_value=runner)):
            with patch.object(bot, "MatchAlertBot", side_effect=create):
                with patch.object(bot.DiscordCooldown, "wait", wait):
                    await bot.run_bot()
        self.assertEqual(order, ["close", "wait", "close"])
        self.assertIs(shared_cooldowns[0], shared_cooldowns[1])
        runner.cleanup.assert_awaited_once()

    async def test_non_rate_limit_error_is_not_retried(self):
        client = SimpleNamespace(start=AsyncMock(side_effect=http_error(401)), close=AsyncMock())
        runner = SimpleNamespace(cleanup=AsyncMock())
        with patch.object(bot, "start_health_server", AsyncMock(return_value=runner)):
            with patch.object(bot, "MatchAlertBot", return_value=client) as factory:
                with self.assertRaises(bot.discord.HTTPException):
                    await bot.run_bot()
        factory.assert_called_once()
        client.close.assert_awaited_once()
        runner.cleanup.assert_awaited_once()
