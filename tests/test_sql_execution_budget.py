from contextlib import closing
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import ai_tasks

class SqlBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.db'
        with closing(sqlite3.connect(self.path)) as c, c:c.execute('CREATE TABLE sample(value INTEGER)');c.executemany('INSERT INTO sample VALUES(?)',[(n,) for n in range(120)])
        self.db=patch.object(ai_tasks,'DB_FILE',self.path);self.db.start()
    def tearDown(self):self.db.stop();self.tmp.cleanup()
    def test_normal_query_and_row_limit(self):
        cols,rows,more=ai_tasks.execute_readonly_sql('SELECT value FROM sample ORDER BY value',max_rows=5)
        self.assertEqual(cols,['value']);self.assertEqual(rows,[(n,) for n in range(5)]);self.assertTrue(more)
    def test_infinite_recursion_is_bounded_and_next_query_runs(self):
        with self.assertRaisesRegex(ai_tasks.TextToSqlError,'лимит выполнения'):
            ai_tasks.execute_readonly_sql('WITH RECURSIVE endless(x) AS (VALUES(1) UNION ALL SELECT x FROM endless) SELECT count(*) FROM endless',max_vm_steps=10000)
        self.assertEqual(ai_tasks.execute_readonly_sql('SELECT count(*) FROM sample')[1],[(120,)])
    def test_deadline_interrupts_expensive_join(self):
        with patch('time.monotonic',side_effect=[0,10]):
            with self.assertRaisesRegex(ai_tasks.TextToSqlError,'лимит выполнения'):
                ai_tasks.execute_readonly_sql('SELECT count(*) FROM sample a CROSS JOIN sample b CROSS JOIN sample c',timeout_seconds=1)
    def test_budget_applies_during_fetch(self):
        with self.assertRaisesRegex(ai_tasks.TextToSqlError,'лимит выполнения'):
            ai_tasks.execute_readonly_sql('WITH RECURSIVE endless(x) AS (VALUES(1) UNION ALL SELECT x FROM endless) SELECT x FROM endless',max_rows=10000,max_vm_steps=10000)
    def test_write_protection_survives_budget(self):
        with self.assertRaises(sqlite3.DatabaseError):ai_tasks.execute_readonly_sql('DELETE FROM sample')
        self.assertEqual(ai_tasks.execute_readonly_sql('SELECT count(*) FROM sample')[1],[(120,)])
