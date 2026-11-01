import tempfile
import unittest
import sqlite3
from pathlib import Path
from storyflow.core import Store, WorkflowError


class WithdrawalTests(unittest.TestCase):
    def test_migrates_existing_database_without_losing_foreign_key_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'state').mkdir()
            with sqlite3.connect(root/'state/story.sqlite3') as db:
                db.executescript('CREATE TABLE episodes(id TEXT PRIMARY KEY,channel TEXT NOT NULL,release_date TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(channel,release_date)); CREATE TABLE costs(id INTEGER PRIMARY KEY,episode_id TEXT REFERENCES episodes(id),amount REAL,category TEXT,created_at TEXT);')
                db.execute('INSERT INTO episodes VALUES(?,?,?,?)',('legacy','story-animation','2026-11-02','{"id":"legacy","scheduled_at":"2026-11-02T20:00:00+09:00"}'))
                db.execute("INSERT INTO costs VALUES(1,'legacy',3,'reservation','2026-10-31')")
            store=Store(root); store.withdraw('legacy','失败保留')
            self.assertEqual(store.db.execute('SELECT amount FROM costs').fetchone()[0],3)
            self.assertFalse(store.db.execute('PRAGMA foreign_key_check').fetchall())
            self.assertTrue((root/'state/backups/before-withdrawal-migration.sqlite3').is_file())
            store.close()

    def test_preserves_failed_attempt_and_releases_slot_without_changing_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); store=Store(root)
            first=store.create('2026-10-31','失败样片','fate',ep='first')
            source=root/'script.txt'; source.write_text('原稿')
            store.register('first','script',source); store.cost('first',2,'reservation')
            with self.assertRaises(WorkflowError): store.create('2026-10-31','第二次','crime',ep='second')
            store.withdraw('first','使用者判定测试失败')
            second=store.create('2026-10-31','第二次','crime',ep='second')
            self.assertEqual(store.episode('first')['scheduled_at'],first['scheduled_at'])
            self.assertEqual(second['scheduled_at'],first['scheduled_at'])
            self.assertEqual(store.next('first')['next'],'withdrawn')
            self.assertIn('script',store.artifacts('first'))
            self.assertEqual(store.db.execute('SELECT count(*) FROM costs').fetchone()[0],1)
            self.assertIn('使用者已退稿，禁止发布',store.validate('first')['errors'])
            self.assertFalse(store.db.execute('PRAGMA foreign_key_check').fetchall())
            store.close()

    def test_cannot_withdraw_a_published_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory); store.create('2026-10-31','已发','fate',ep='first')
            with store.db: store.db.execute("INSERT INTO publications VALUES('first','{}')")
            with self.assertRaises(WorkflowError): store.withdraw('first','不能假装已撤回')
            self.assertNotEqual(store.next('first')['next'],'withdrawn')
            store.close()

    def test_unknown_upload_without_id_still_blocks_withdrawal(self):
        from storyflow.integrations import begin, finish
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory); store.create('2026-10-31','上传待核','fate',ep='first')
            key,_=begin(store,'first','ship',{'source':'reviewed'})
            finish(store,'first','ship',key,'unknown',{'video_id':None})
            with self.assertRaises(WorkflowError): store.withdraw('first','不能凭无id断定没有上传')
            with self.assertRaises(WorkflowError): store.create('2026-10-31','不应重复排','fate',ep='second')
            store.close()
