import asyncio
import io
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import patch,AsyncMock

from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import db
import photo_albums as store
from photo_convert import convert
from web.photo_api import install


class AlbumTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.dbpatch=patch.object(db,'DB_FILE',str(self.root/'db.sqlite'))
        self.rootpatch=patch.object(store,'MEDIA_ROOT',self.root/'media')
        self.dbpatch.start();self.rootpatch.start()
        with store.connection() as c:
            c.executescript('''CREATE TABLE daily_events(id INTEGER PRIMARY KEY,chat_id INTEGER,name TEXT,date TEXT,time TEXT);
              CREATE TABLE daily_participants(daily_id INTEGER,user_id INTEGER);''')
        store.ensure_schema();store.configure_topic(-1,42,100)
        self.at=datetime(2026,9,16,18,tzinfo=store.TZ)

    def tearDown(self):
        self.rootpatch.stop();self.dbpatch.stop();self.temp.cleanup()

    def event(self,id=1,day=16,hour=15,chat=-1):
        with store.connection() as c:
            c.execute('INSERT INTO daily_events VALUES (?,?,?,?,?)',(id,chat,'Посиделки Карпачо',f'2026-09-{day:02}',f'{hour:02}:00'))

    def message(self,id=1,group=None,thread=42,document=False,anon=False):
        file=Obj(file_id='file',file_unique_id='unique',file_size=1000)
        return Obj(photo=[] if document else [file],document=file if document else None,
            chat=Obj(id=-1),message_thread_id=thread,sender_chat=Obj(id=-2) if anon else None,
            from_user=Obj(id=100),media_group_id=group,message_id=id,date=self.at)

    def batch(self):
        with store.connection() as c:
            return dict(c.execute('SELECT * FROM photo_batches ORDER BY id LIMIT 1').fetchone())

    def ready(self):
        with store.connection() as c:
            c.execute("UPDATE daily_photos SET status='ready',path='1.jpg',thumb_path='1.webp'")
        (store.MEDIA_ROOT/'1.jpg').write_bytes(b'image');(store.MEDIA_ROOT/'1.webp').write_bytes(b'thumb')

    def test_routing_and_idempotency(self):
        self.assertFalse(store.enqueue(self.message(thread=99)))
        store.enqueue(self.message());store.enqueue(self.message())
        with store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM daily_photos').fetchone()[0],1)
        animation=self.message(id=2,document=True);animation.animation=Obj()
        self.assertFalse(store.enqueue(animation))

    def test_album_and_anonymous_documents(self):
        store.enqueue(self.message(group='A',document=True,anon=True))
        store.enqueue(self.message(id=2,group='A',document=True,anon=True))
        b=self.batch();self.assertIsNone(b['user_id']);self.assertEqual(b['sender_chat_id'],-2)
        with store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM photo_batches').fetchone()[0],1)
        with self.assertRaises(PermissionError):store.choose(b['id'],100,False,cancel=True)

    def test_auto_no_confirmation_and_future_exclusion(self):
        self.event(1,15);self.event(2,16,20)
        store.enqueue(self.message());self.ready();store.decide(self.batch()['id'])
        self.assertEqual(self.batch()['daily_id'],1);self.assertEqual(self.batch()['decision'],'attached')
        with store.connection() as c:self.assertEqual(len(store.choices(c,self.batch())),2)

    def test_ambiguity_and_manual_choice(self):
        self.event(1,15);self.event(2,16)
        store.enqueue(self.message());self.ready();bid=self.batch()['id'];store.decide(bid)
        self.assertEqual(self.batch()['decision'],'pending')
        with self.assertRaises(PermissionError):store.choose(bid,200,False,2)
        store.choose(bid,100,False,2);self.assertEqual(self.batch()['daily_id'],2)

    def test_cross_chat_and_old_event(self):
        self.event(1,16,chat=-2);self.event(2,14)
        store.enqueue(self.message());self.ready();store.decide(self.batch()['id'])
        self.assertEqual(self.batch()['decision'],'pending')
        with self.assertRaises(ValueError):store.choose(self.batch()['id'],100,False,1)

    def test_cancel_cleanup_and_late_album(self):
        self.event();store.enqueue(self.message(group='x'));bid=self.batch()['id'];self.ready();store.decide(bid)
        store.choose(bid,100,False,cancel=True);store.cleanup()
        self.assertFalse((store.MEDIA_ROOT/'1.jpg').exists())
        store.enqueue(self.message(id=2,group='x'))
        with store.connection() as c:self.assertEqual(c.execute('SELECT status FROM daily_photos WHERE message_id=2').fetchone()[0],'cancelled')

    def test_expiry_only_pending(self):
        store.enqueue(self.message());self.ready();bid=self.batch()['id'];store.decide(bid)
        with patch('photo_albums.time.time',return_value=self.at.timestamp()+8*86400):store.cleanup()
        self.assertEqual(self.batch()['decision'],'expired')

    def test_attached_never_expires_and_delete_guard(self):
        self.event();store.enqueue(self.message());self.ready();store.decide(self.batch()['id'])
        with patch('photo_albums.time.time',return_value=self.at.timestamp()+99*86400):store.cleanup()
        self.assertEqual(self.batch()['decision'],'attached')
        with self.assertRaises(sqlite3.IntegrityError):
            with store.connection() as c:c.execute('DELETE FROM daily_events WHERE id=1')
        store.delete_photo(1,100,False);store.delete_photo(1,100,False)
        with store.connection() as c:c.execute('DELETE FROM daily_events WHERE id=1')
        with store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM daily_photo_albums').fetchone()[0],0)

    def test_size_and_thumbnail_and_original(self):
        source=self.root/'noise.png';target=self.root/'out.jpg';thumb=self.root/'thumb.webp'
        Image.effect_noise((1800,1200),80).convert('RGB').save(source)
        result=convert(source,target,thumb)
        self.assertLessEqual(result['size'],300000);self.assertLessEqual(thumb.stat().st_size,15000)
        with Image.open(thumb) as im:self.assertLessEqual(max(im.size),256)
        convert(source,target,thumb,original=True);self.assertEqual(source.read_bytes(),target.read_bytes())

    def test_transparency_orientation_formats(self):
        for format in ('PNG','GIF','WEBP','TIFF','BMP','HEIF','AVIF'):
            with self.subTest(format=format):
                from pillow_heif import register_heif_opener
                register_heif_opener()
                source=self.root/('source.'+format.lower());dest=self.root/'result.jpg';thumb=self.root/'thumb.webp'
                Image.new('RGB',(64,32),'red').save(source,format=format)
                convert(source,dest,thumb)
                with Image.open(dest) as im:self.assertEqual(im.size,(64,32))
        source=self.root/'transparent.png';Image.new('RGBA',(40,30),(0,0,0,0)).save(source)
        convert(source,dest,thumb)
        with Image.open(dest) as im:self.assertEqual(im.getpixel((0,0)),(255,255,255))
        exif=Image.Exif();exif[274]=6
        source=self.root/'rotated.jpg';Image.new('RGB',(80,40)).save(source,exif=exif)
        convert(source,dest,thumb)
        with Image.open(dest) as im:self.assertEqual(im.size,(40,80));self.assertFalse(im.getexif())

    def test_api_access_search_and_delete(self):
        self.event();store.enqueue(self.message());self.ready();store.decide(self.batch()['id'])
        with store.connection() as c:token=c.execute('SELECT token FROM daily_photo_albums').fetchone()[0]
        app=FastAPI()
        def session(r):
            if not r.headers.get('x-test-user'):raise HTTPException(401)
            return {'telegram_user_id':int(r.headers['x-test-user'])}
        install(app,session,lambda r:(int(session(r)['telegram_user_id']),-1),
                lambda r,u,p:dict(r),lambda c,ids:{})
        with TestClient(app) as client:
            data=client.get('/api/albums/'+token).json()
            self.assertFalse(data['photos'][0]['can_delete']);self.assertNotIn('user_id',str(data))
            self.assertEqual(client.get('/api/albums/invalid').status_code,404)
            image_response=client.get('/api/albums/'+token+'/photos/1/image')
            self.assertEqual(image_response.status_code,200)
            self.assertEqual(image_response.headers['cache-control'],'private, max-age=3600')
            self.assertEqual(client.get('/api/albums/'+token+'/photos/1/image',headers={
                'If-None-Match':image_response.headers['etag']}).status_code,304)
            self.assertEqual(client.get('/api/daily/search').status_code,401)
            h={'x-test-user':'100','origin':'http://testserver'}
            self.assertEqual(len(client.get('/api/daily/search?q=КАРПАЧО',headers=h).json()['events']),1)
            self.assertEqual(client.get('/api/daily/calendar?month=2026-09',headers=h).json()['days'][0]['has_photos'],1)
            self.assertEqual(client.delete('/api/album-photos/1',headers={**h,'x-test-user':'200'}).status_code,403)
            self.assertEqual(client.delete('/api/album-photos/1',headers={**h,'origin':'https://evil.example'}).status_code,403)
            self.assertEqual(client.delete('/api/album-photos/1',headers=h).status_code,200)
            self.assertEqual(client.get('/api/albums/'+token+'/photos/1/image').status_code,404)
            self.assertEqual(client.get('/api/albums/'+token+'/photos/1/image',headers={
                'If-None-Match':image_response.headers['etag']}).status_code,404)

    def test_worker_conversion(self):
        from photo_bot import process,claim
        self.event();store.enqueue(self.message(document=True))
        with store.connection() as c:c.execute('UPDATE photo_batches SET touched_at=0')
        p=claim();raw=io.BytesIO();Image.new('RGB',(50,50),'blue').save(raw,'PNG')
        class FakeBot:
            async def get_file(self,id):return Obj(file_size=len(raw.getvalue()),file_path='fake')
            async def download_file(self,path,destination,**kwargs):destination.write(raw.getvalue())
        asyncio.run(process(FakeBot(),p))
        with store.connection() as c:self.assertEqual(c.execute('SELECT status FROM daily_photos').fetchone()[0],'ready')

    def test_notifications_and_late_members(self):
        from photo_bot import notice
        self.event();store.enqueue(self.message(group='x'));self.ready();bid=self.batch()['id']
        with patch.dict('os.environ',{'PHOTO_PUBLIC_BASE_URL':'https://example.test'}):
            b,text,markup=notice(bid)
            self.assertIn('Фотографии (1) успешно',text)
            self.assertTrue(any(k.url for row in markup.inline_keyboard for k in row))
            store.enqueue(self.message(id=2,group='x'));self.ready()
            self.assertIn('Фотографии (2) успешно',notice(bid)[1])
            store.choose(bid,100,False,cancel=True)
            self.assertIn('отменена',notice(bid)[1])

    def test_no_candidates_midnight_and_future_window(self):
        self.at=datetime(2026,9,17,0,5,tzinfo=store.TZ)
        self.event(1,16,23);self.event(2,18,1)
        store.enqueue(self.message());self.ready();store.decide(self.batch()['id'])
        self.assertEqual(self.batch()['daily_id'],1)
        with store.connection() as c:self.assertEqual([r['id'] for r in store.choices(c,self.batch())],[1])

    def test_corrupt_and_oversize(self):
        source=self.root/'bad';source.write_bytes(b'not an image')
        with self.assertRaises(Exception):convert(source,self.root/'out',self.root/'thumb')
        msg=self.message(document=True);msg.document.file_size=store.MAX_FILE+1
        store.enqueue(msg)
        with store.connection() as c:self.assertEqual(c.execute('SELECT status FROM daily_photos').fetchone()[0],'failed')

    def test_low_disk_retry(self):
        from photo_bot import process,claim
        store.enqueue(self.message())
        with store.connection() as c:c.execute('UPDATE photo_batches SET touched_at=0')
        p=claim()
        with patch('photo_bot.shutil.disk_usage',return_value=Obj(free=1)):
            asyncio.run(process(Obj(),p))
        with store.connection() as c:
            row=c.execute('SELECT status,error FROM daily_photos').fetchone()
            self.assertEqual(row['status'],'queued');self.assertIn('места',row['error'])

    def test_topic_command_permissions(self):
        from aiogram import Bot,Dispatcher
        from aiogram.types import Update
        import photo_bot
        async def run():
            dp=Dispatcher();photo_bot.register(dp)
            bot=Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk')
            bot.session=AsyncMock(return_value=True)
            for uid,thread in [(200,77),(100,88)]:
                update=Update.model_validate({'update_id':uid,'message':{'message_id':uid,'date':int(self.at.timestamp()),
                    'chat':{'id':-1,'type':'supergroup'},'from':{'id':uid,'is_bot':False,'first_name':'Tester'},
                    'message_thread_id':thread,'is_topic_message':True,'text':'/set_photo_topic',
                    'entities':[{'type':'bot_command','offset':0,'length':16}]}})
                await dp.feed_update(bot,update)
                with store.connection() as c:
                    self.assertEqual(c.execute('SELECT thread_id FROM photo_topics WHERE chat_id=-1').fetchone()[0],42 if uid==200 else 88)
        with patch.object(photo_bot,'ADMIN_IDS',{100}):asyncio.run(run())


if __name__=='__main__':unittest.main()
