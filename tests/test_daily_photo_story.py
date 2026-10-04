from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from PIL import Image
import db,ai_tasks,ai_runtime as rt,photo_albums as albums,photo_story as stories
import daily_photo_story as daily
from photo_convert import capture_time,convert


class DailyStoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/'test.db'
        self.patches=[patch.object(ai_tasks,'DB_FILE',self.path),patch.object(db,'DB_FILE',str(self.path)),patch.object(albums,'MEDIA_ROOT',self.root/'media')]
        for p in self.patches:p.start()
        with closing(stories.connection()) as c,c:c.executescript('CREATE TABLE daily_events(id INTEGER PRIMARY KEY,chat_id INTEGER,name TEXT,date TEXT,time TEXT); CREATE TABLE daily_participants(daily_id INTEGER,user_id INTEGER);')
        albums.ensure_schema();rt.initialize();rt.heartbeat('photo','groq',['tasks'],task_types=list(stories.KINDS))
        self.due=datetime(2026,10,3,18,tzinfo=albums.TZ).timestamp()
        with closing(stories.connection()) as c,c:c.execute("INSERT INTO daily_events VALUES(1,-42,'Кофе <друзья>','2026-10-02','18:00')")
        self.send=Mock(return_value=123)
        self.scheduler_patch=patch.object(daily,'schedule',side_effect=lambda now: original_schedule(now,allow_backfill=True))
        original_schedule=daily.schedule
        self.scheduler_patch.start()
        self.claim_clock=patch.object(rt,'now',return_value=datetime(2026,10,4,0,0))
        self.claim_clock.start()

    def tearDown(self):
        self.scheduler_patch.stop();self.claim_clock.stop()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def photos(self,n=1,sent=None):
        with albums.connection() as c:
            b=c.execute("INSERT INTO photo_batches(chat_id,thread_id,group_key,first_message_id,sent_at,touched_at,daily_id,decision) VALUES(-42,1,?,1,?,0,1,'attached')",(str(n)+str(sent),self.due-100)).lastrowid
            for i in range(n):
                ident=c.execute("INSERT INTO daily_photos(batch_id,chat_id,message_id,file_id,kind,sent_at,status,path) VALUES(?,-42,?,'file','photo',?,'ready',?)",(b,b*100+i,sent or self.due-100,'image.jpg')).lastrowid
            albums.album_token(c,1,-42)
        Image.new('RGB',(64,64),'red').save(albums.MEDIA_ROOT/'image.jpg')

    def finish(self):
        types=[]
        while True:
            task=rt.claim('tasks','photo')
            if not task:break
            types.append(task['task_type'])
            with closing(stories.connection()) as c:row=dict(c.execute('SELECT * FROM ai_tasks WHERE id=?',(task['id'],)).fetchone())
            result=stories.complete(row,'На столе чашки. Уютная встреча за кофе.',None,self.send)
            self.assertEqual(result['status'],'done')
        return types

    def state(self):
        with closing(stories.connection()) as c:return dict(c.execute('SELECT * FROM daily_photo_stories WHERE daily_id=1').fetchone())

    def test_due_notification_one_time_and_multiple_batches(self):
        daily.schedule(self.due-60);self.photos(21)
        self.assertEqual(daily.schedule(self.due+1),1)
        self.assertEqual(daily.schedule(self.due+2),0)
        self.assertEqual(self.finish(),['photo_story']*3+['photo_story_merge'])
        self.send.assert_called_once();self.assertIn('Вчера прошёл дейлик <b>Кофе &lt;друзья&gt;</b>',self.send.call_args.args[1])
        self.assertEqual(self.state()['delivery_state'],'sent')
        self.photos(2,self.due+500)
        self.assertEqual(daily.schedule(self.due+600),0)

    def test_old_albums_and_late_first_photos_are_silent(self):
        self.photos();daily.schedule(self.due+1);self.finish();self.send.assert_not_called()
        self.assertEqual(self.state()['status'],'done')

    def test_empty_deadline_then_late_upload(self):
        daily.schedule(self.due-60);self.assertEqual(daily.schedule(self.due+1),0)
        self.photos(sent=self.due+100);daily.schedule(self.due+101);self.finish()
        self.send.assert_not_called();self.assertTrue(self.state()['story_text'])

    def test_creator_excluded_and_local_files_chat_scoped(self):
        self.photos();daily.schedule(self.due+1)
        with patch.dict('os.environ',{'AI_CREATOR_USER_ID':'999'}):task=rt.claim('tasks','photo')
        self.assertEqual(task['system_instruction'],'');self.assertNotIn(ai_tasks.CREATOR_POLICY_MARKER,task['prompt'])
        self.assertTrue(stories.image_parts(task['payload']['photos'],-42))
        from ai_providers import PromptTooLarge
        with self.assertRaises(PromptTooLarge):stories.image_parts(task['payload']['photos'],-99)

    def test_capture_exif_survives_extraction_before_conversion(self):
        image=Image.new('RGB',(100,100));exif=Image.Exif();exif[36867]='2026:10:02 20:30:00';exif[36881]='+05:00'
        source=self.root/'src.jpg';image.save(source,exif=exif)
        result=convert(source,self.root/'out.jpg',self.root/'thumb.webp')
        self.assertEqual(result['captured_at'],datetime(2026,10,2,20,30,tzinfo=albums.TZ).timestamp())
        with Image.open(self.root/'out.jpg') as compressed:self.assertIsNone(capture_time(compressed))

    def test_delivery_unknown_keeps_album_story_and_no_regeneration(self):
        daily.schedule(self.due-60);self.photos();daily.schedule(self.due+1)
        self.send.side_effect=rt.DeliveryError('unknown')
        with self.assertRaises(rt.DeliveryError):self.finish()
        self.assertTrue(self.state()['story_text']);self.assertEqual(self.state()['delivery_state'],'unknown')
        self.assertEqual(daily.schedule(self.due+2),0)

    def test_order_is_capture_then_stable_fallback(self):
        rows=[dict(id=2,message_id=2,sent_at=20,captured_at=5),dict(id=1,message_id=1,sent_at=10,captured_at=None)]
        self.assertEqual([p['id'] for p in daily.ordered_photos(rows)],[2,1])
