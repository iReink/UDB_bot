import asyncio
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock,patch

from aiogram import Bot,Dispatcher
from aiogram.types import Update,Message

import db
import photo_albums as photos
import daily_search_store as store
import daily_search_bot as search


class DailySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patchdb=patch.object(db,'DB_FILE',str(Path(self.temp.name)/'stats.db'));self.patchdb.start()
        self.patchmedia=patch.object(photos,'MEDIA_ROOT',Path(self.temp.name)/'media');self.patchmedia.start()
        search.sessions.clear()
        with photos.connection() as c:
            c.executescript('''CREATE TABLE daily_events(id INTEGER PRIMARY KEY,chat_id INTEGER,name TEXT,date TEXT,time TEXT,description TEXT,cars TEXT,link TEXT);
              CREATE TABLE daily_participants(daily_id INTEGER,user_id INTEGER,is_driver INTEGER DEFAULT 0, UNIQUE(daily_id,user_id));''')
        photos.ensure_schema()

    def tearDown(self):
        search.sessions.clear();self.patchmedia.stop();self.patchdb.stop();self.temp.cleanup()

    def event(self,id=1,chat=-1,name='Кофе на Уктусе',day='2026-09-12'):
        with photos.connection() as c:c.execute('INSERT INTO daily_events VALUES (?,?,?,?,?,?,?,?)',(id,chat,name,day,'15:00','Описание','да',''))

    def test_unicode_chat_dates_pagination(self):
        for i in range(1,15):self.event(i,day=f'2026-09-{i:02}')
        self.event(15,chat=-2)
        rows,count,pages,num=store.page(-1,100,q='КОФЕ',start='2026-09-01',end='2026-09-30',page_number=1)
        self.assertEqual((count,pages,num),(14,3,1));self.assertEqual(len(rows),6);self.assertEqual(rows[0]['id'],8)
        self.assertEqual(store.page(-1,100,q='ничего')[1],0)
        self.assertEqual(store.page(-1,100,start='2026-09-12')[1],1)
        self.assertEqual(store.page(-1,100,page_number=999)[3],2)

    def test_photo_filter_counts_only_attached_ready(self):
        self.event()
        with photos.connection() as c:
            c.execute("INSERT INTO photo_batches(id,chat_id,thread_id,user_id,group_key,first_message_id,sent_at,touched_at,daily_id,decision) VALUES(1,-1,42,100,'g',1,0,0,1,'attached')")
            c.execute("INSERT INTO daily_photos(batch_id,chat_id,user_id,message_id,file_id,kind,sent_at,status) VALUES(1,-1,100,1,'x','photo',0,'ready')")
        self.assertEqual(store.page(-1,100,only_photos=True)[1],1)
        with photos.connection() as c:c.execute("UPDATE photo_batches SET decision='pending'")
        self.assertEqual(store.page(-1,100,only_photos=True)[1],0)
        with photos.connection() as c:
            c.execute("UPDATE photo_batches SET decision='attached'");c.execute("UPDATE daily_photos SET status='deleted'")
        self.assertEqual(store.page(-1,100,only_photos=True)[1],0)

    def test_dates_and_participation_guards(self):
        self.assertEqual(search.date_range('02.2024'),('2024-02-01','2024-02-29'))
        self.assertEqual(search.date_range('12.09.2026'),('2026-09-12','2026-09-12'))
        for bad in ['31.02.2026','2026','13.2026','1.2.2026']:
            with self.assertRaises(ValueError):search.date_range(bad)
        self.event(day='2000-01-01');s=search.Search('key',-1,100,42)
        with self.assertRaises(ValueError):search.participation(s,1,'join')
        self.event(2,day='2099-01-01');self.event(3,chat=-2,day='2099-01-01')
        with self.assertRaises(ValueError):search.participation(s,3,'join')
        with self.assertRaises(ValueError):search.participation(s,2,'driver')
        search.participation(s,2,'join');search.participation(s,2,'driver')
        with photos.connection() as c:self.assertEqual(c.execute('SELECT is_driver FROM daily_participants').fetchone()[0],1)
        search.participation(s,2,'join')
        with photos.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM daily_participants').fetchone()[0],0)

    def test_cards_preserve_search_and_past_readonly(self):
        self.event(day='2000-01-01');s=search.Search('key',-1,100,42,message=50,q='кофе',page=2)
        bot=Obj(edit_message_text=AsyncMock())
        with patch('daily.get_daily_participants',return_value=[]):asyncio.run(search.card(bot,s,1))
        payload=bot.edit_message_text.call_args
        self.assertIn('Прошедший',payload.args[0]);self.assertNotIn('Уже идёт',payload.args[0])
        callbacks=[b.callback_data for row in payload.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertEqual(callbacks,['dsearch:key:list','dsearch:key:home']);self.assertEqual((s.q,s.page),('кофе',2))
        with patch('daily.get_daily_participants',return_value=[]):asyncio.run(search.card(bot,s,999))
        self.assertIn('не существует',bot.edit_message_text.call_args.args[0])

    def test_callbacks_owner_expiry_and_reply_matching(self):
        self.event()
        async def scenario():
            dp=Dispatcher();home=AsyncMock();search.register(dp,home)
            bot=Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk')
            bot.session=AsyncMock(return_value=True)
            s=search.Search('key',-1,100,42,message=50);search.sessions[s.key]=s
            async def click(action,uid=100,message=50):
                update=Update.model_validate({'update_id':1,'callback_query':{'id':'c','chat_instance':'x','from':{'id':uid,'is_bot':False,'first_name':'Test'},
                    'data':f'dsearch:key:{action}','message':{'message_id':message,'date':1700000000,'chat':{'id':-1,'type':'supergroup'},'message_thread_id':42}}})
                await dp.feed_update(bot,update)
            await click('photos',uid=200)
            self.assertFalse(s.only_photos);self.assertIn('свой поиск',bot.session.call_args.args[1].text)
            await click('photos');self.assertTrue(s.only_photos)
            state=dp.fsm.get_context(bot=bot,chat_id=-1,user_id=100)
            await click('name')
            self.assertEqual(await state.get_state(),search.SearchInput.name.state)
            self.assertTrue(all(getattr(call.args[1],'reply_markup',None).__class__.__name__!='ForceReply' for call in bot.session.call_args_list))
            await click('list');self.assertIsNone(await state.get_state())
            s.expires=time.monotonic()-1
            await click('reset');self.assertIn('устарел',bot.session.call_args.args[1].text)
            # FSM accepts ordinary text, only from its owner and in the selected thread.
            s.expires=time.monotonic()+1800;search.sessions[s.key]=s
            await click('name')
            handler=AsyncMock()
            async def reply(uid,text='уктус',thread=42):
                msg=Message.model_validate({'message_id':101,'date':1700000000,'chat':{'id':-1,'type':'supergroup'},
                    'from':{'id':uid,'is_bot':False,'first_name':'Test'},'message_thread_id':thread,'text':text},context={'bot':bot})
                ctx=dp.fsm.get_context(bot=bot,chat_id=-1,user_id=uid)
                await search.SearchMessages()(handler,msg,{'state':ctx})
            await reply(200);await reply(100,thread=43)
            self.assertEqual(s.q,'');self.assertEqual(handler.await_count,2)
            await reply(100);self.assertEqual(s.q,'уктус');self.assertIsNone(await state.get_state())
            self.assertEqual(handler.await_count,2)
            await reply(100,text='обычное сообщение');self.assertEqual(s.q,'уктус');self.assertEqual(handler.await_count,3)
            for mark in ('-','—'):
                await click('name');await reply(100,text=' '+mark+' ');self.assertEqual(s.q,'')
                s.start=s.end='2026-09-12'
                await click('dateinput');await reply(100,text=mark);self.assertEqual((s.start,s.end),('',''))
                self.assertTrue(s.only_photos)
            await click('name');await reply(100,text='/daily');self.assertIsNone(await state.get_state())
            await click('name');s.expires=time.monotonic()-1
            await reply(100,text='не фильтр');self.assertIsNone(await state.get_state());self.assertEqual(s.q,'')
            s.expires=time.monotonic()+1800;search.sessions[s.key]=s
            search.sessions['second']=search.Search('second',-1,200,42,message=51,q='другой')
            await click('clearname');self.assertEqual(search.sessions['second'].q,'другой')
            await click('name');await reply(100,text='/cancel');self.assertIsNone(await state.get_state())
            await click('name');await click('home')
            self.assertIsNone(await state.get_state());self.assertNotIn('key',search.sessions)
            self.assertEqual(home.await_count,1)
        asyncio.run(scenario())

    def test_menu_search_when_no_future_events(self):
        import daily
        async def scenario():
            dp=Dispatcher();daily.register_daily_handlers(dp)
            bot=Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk');bot.session=AsyncMock(return_value=True)
            msg={'message_id':5,'date':1700000000,'chat':{'id':-1,'type':'supergroup'},'from':{'id':100,'is_bot':False,'first_name':'Test'},
                 'text':'/daily','entities':[{'type':'bot_command','offset':0,'length':6}]}
            await dp.feed_update(bot,Update.model_validate({'update_id':1,'message':msg}))
            method=bot.session.call_args.args[1]
            self.assertIn('нет',method.text)
            self.assertIn('dsearch:open',[b.callback_data for row in method.reply_markup.inline_keyboard for b in row])
        with patch.object(daily,'DB_PATH',db.DB_FILE):asyncio.run(scenario())

    def test_prompt_invalid_date_preserves_filters_and_fsm(self):
        async def scenario():
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.memory import MemoryStorage
            from aiogram.fsm.storage.base import StorageKey
            state=FSMContext(MemoryStorage(),StorageKey(bot_id=123,chat_id=-1,user_id=100))
            s=search.Search('key',-1,100,42,message=50,q='кофе',only_photos=True)
            search.sessions[s.key]=s
            bot=Obj(id=123,send_message=AsyncMock(return_value=Obj(message_id=101)),edit_message_text=AsyncMock())
            message=Obj(chat=Obj(id=-1),message_thread_id=42,from_user=Obj(id=100),sender_chat=None,
                reply_to_message=Obj(message_id=99),text='31.02.2026',bot=bot)
            handler=AsyncMock()
            await search.prompt(bot,s,'date',state)
            await search.SearchMessages()(handler,message,{'state':state})
            self.assertEqual((s.q,s.only_photos,s.input_kind),('кофе',True,'date'))
            self.assertIn('не существует',bot.edit_message_text.call_args.args[0])
            self.assertEqual(bot.send_message.await_count,0)
            message.text='09.2026'
            await search.SearchMessages()(handler,message,{'state':state})
            self.assertEqual((s.start,s.end),('2026-09-01','2026-09-30'))
            self.assertIsNone(await state.get_state());self.assertEqual(handler.await_count,0)
            await search.prompt(bot,s,'name',state)
            await search.LeaveSearchInput()(handler,Obj(data='daily_new_daily'),{'state':state})
            self.assertIsNone(await state.get_state())
            await state.set_state('CreateDaily:name')
            await search.clear_input(state)
            self.assertEqual(await state.get_state(),'CreateDaily:name')
        asyncio.run(scenario())


if __name__=='__main__':unittest.main()
