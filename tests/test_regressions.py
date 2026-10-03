import asyncio
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database
from web import create_app


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = database.DB_NAME
        database.DB_NAME = str(Path(self.tmp.name) / 'test.db')

    async def asyncTearDown(self):
        database.DB_NAME = self.old
        self.tmp.cleanup()

    async def test_new_schema_and_repeated_init(self):
        await database.init_db()
        await database.init_db()
        await database.get_user('1')
        await database.update_user('1', season_points=9, bank=12)
        user = await database.get_user('1')
        self.assertEqual(user['season_points'], 9)
        self.assertEqual(user['bank'], 12)

    async def test_partial_old_schema_migrates_without_erasing_data(self):
        with sqlite3.connect(database.DB_NAME) as db:
            db.execute('CREATE TABLE users (user_id TEXT PRIMARY KEY, bank INTEGER DEFAULT 0)')
            db.execute("INSERT INTO users VALUES ('1', 42)")
        await database.init_db()
        user = await database.get_user('1')
        self.assertEqual(user['bank'], 42)
        self.assertEqual(user['season_points'], 0)

    async def test_reject_unknown_field(self):
        await database.init_db()
        with self.assertRaises(ValueError):
            await database.update_user('1', invalid=1)

    async def test_concurrent_user_creation(self):
        await database.init_db()
        rows = await asyncio.gather(*(database.get_user('1') for _ in range(5)))
        self.assertTrue(all(row['user_id'] == '1' for row in rows))


class DashboardTests(unittest.TestCase):
    def test_all_routes_registered(self):
        paths = {r.rule for r in create_app().url_map.iter_rules()}
        self.assertTrue({'/feeds/add', '/feeds/delete', '/schedule/delete'} <= paths)

    def test_authentication(self):
        with patch.dict(os.environ, {'DASH_TOKEN': 'test-secret'}):
            app = create_app()
            client = app.test_client()
            self.assertEqual(client.get('/').status_code, 401)
            self.assertEqual(client.get('/', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
            self.assertEqual(client.get('/', headers={'Authorization': 'Bearer test-secret'}).status_code, 200)

    def test_unconfigured_dashboard_is_closed(self):
        with patch.dict(os.environ, {'DASH_TOKEN': ''}):
            self.assertEqual(create_app().test_client().get('/').status_code, 503)

    def test_import_registers_automod_without_starting_network(self):
        import main
        self.assertIsNotNone(main.bot.get_command('automod_sync'))
        self.assertIsNotNone(main.bot.get_command('automod_clear'))
        self.assertEqual(len(main.TRANSLATE_TABLE), 14)


class PersistentViewsTests(unittest.IsolatedAsyncioTestCase):
    async def test_giveaways_have_distinct_persistent_button_ids(self):
        import main
        first = main.GiveawayView('first')
        second = main.GiveawayView('second')
        self.assertTrue(first.is_persistent())
        self.assertTrue(second.is_persistent())
        self.assertNotEqual(first.children[0].custom_id, second.children[0].custom_id)

    async def test_ticket_button_is_persistent(self):
        import main
        self.assertTrue(main.TicketView().is_persistent())


class StateTests(unittest.TestCase):
    def test_concurrent_updates_keep_all_items(self):
        from concurrent.futures import ThreadPoolExecutor
        from json_state import JSON_LOCK, read_json, write_json
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / 'items.json')
            def append(number):
                with JSON_LOCK:
                    items = read_json(filename, [])
                    items.append(number)
                    write_json(filename, items)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(append, range(20)))
            self.assertEqual(sorted(read_json(filename, [])), list(range(20)))

    def test_form_redirect_keeps_read_auth_and_rejects_unauthenticated_writes(self):
        from contextlib import chdir
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            with patch.dict(os.environ, {'DASH_TOKEN': 'test-secret'}):
                client = create_app().test_client()
                result = client.post('/schedule', data={
                    'token': 'test-secret', 'datetime': '2030-01-01T00:00:00',
                    'message': 'test', 'channel_id': '123',
                }, follow_redirects=True)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(client.post('/schedule/delete', data={'index': '0'}).status_code, 401)

    def test_invalid_schedule_is_not_written(self):
        from contextlib import chdir
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            with patch.dict(os.environ, {'DASH_TOKEN': 'test-secret'}):
                client = create_app().test_client()
                client.post('/schedule', data={'token': 'test-secret', 'datetime': 'invalid',
                    'message': 'test', 'channel_id': '123'})
                self.assertFalse(Path('scheduled.json').exists())
