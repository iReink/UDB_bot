import asyncio
import sqlite3
import unittest
from unittest.mock import patch
from ai_notifications import Connection, Coordinator


class CommitTests(unittest.TestCase):
    def test_notifications_follow_commit_and_rollback(self):
        conn=sqlite3.connect(':memory:',factory=Connection)
        conn.execute('CREATE TABLE ai_tasks(id INTEGER)')
        with patch('ai_notifications.notify') as notify:
            conn.execute('INSERT INTO ai_tasks VALUES(1)')
            notify.assert_not_called()
            conn.rollback();notify.assert_not_called()
            with conn:conn.execute('INSERT INTO ai_tasks VALUES(2)')
            notify.assert_called_once_with({'tasks'})
        conn.close()


class WaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_wakeup_between_read_and_wait_is_not_lost(self):
        coordinator=Coordinator();before=coordinator.versions.copy()
        await coordinator.wake('tasks')
        await asyncio.wait_for(coordinator.wait(['tasks'],before,25),.1)

    async def test_lost_notification_recovers_through_common_fallback(self):
        coordinator=Coordinator()
        with patch('ai_runtime.ready_queues',return_value=['type-checks']),patch('ai_notifications.os.name','nt'):
            await coordinator.start()
            try:
                before=coordinator.versions.copy()
                await asyncio.wait_for(coordinator.wait(['type-checks'],before,25),2)
            finally:await coordinator.close()

    async def test_empty_wait_times_out(self):
        coordinator=Coordinator()
        await asyncio.wait_for(coordinator.wait(['tasks'],coordinator.versions.copy(),.01),.2)
