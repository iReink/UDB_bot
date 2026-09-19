"""Synthetic album benchmark; only temporary files/DB, no Telegram messages."""
import asyncio
import io
import json
import os
import resource
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as Obj

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import db
import photo_albums as store
from photo_bot import claim,process

async def run():
    with tempfile.TemporaryDirectory(prefix='udb-photo-benchmark-') as temp:
        db.DB_FILE=str(Path(temp)/'test.db');store.MEDIA_ROOT=Path(temp)/'media'
        with store.connection() as c:
            c.execute('CREATE TABLE daily_events(id INTEGER PRIMARY KEY,chat_id INTEGER,name TEXT,date TEXT,time TEXT)')
        store.ensure_schema();store.configure_topic(-1,1,1)
        raw=io.BytesIO();Image.effect_noise((2000,1500),70).convert('RGB').save(raw,'PNG')
        class Bot:
            async def get_file(self,file_id):return Obj(file_path='fake',file_size=len(raw.getvalue()))
            async def download_file(self,path,destination,**kwargs):destination.write(raw.getvalue())
        for mid in range(10):
            store.enqueue(Obj(photo=[],document=Obj(file_id='test',file_unique_id=str(mid),file_size=len(raw.getvalue())),
                chat=Obj(id=-1),message_thread_id=1,from_user=Obj(id=1),sender_chat=None,
                media_group_id='test',message_id=mid,date=datetime.now(store.TZ)))
        with store.connection() as c:c.execute('UPDATE photo_batches SET touched_at=0')
        before=resource.getrusage(resource.RUSAGE_CHILDREN)
        started=time.perf_counter();durations=[]
        while (p:=claim()):
            t=time.perf_counter();await process(Bot(),p);durations.append(time.perf_counter()-t)
        after=resource.getrusage(resource.RUSAGE_CHILDREN)
        with store.connection() as c:
            rows=c.execute('SELECT status,stored_size FROM daily_photos').fetchall()
        print(json.dumps({'photos':len(rows),'ready':sum(r['status']=='ready' for r in rows),
            'elapsed_s':round(time.perf_counter()-started,3),'p95_processing_s':round(sorted(durations)[-1],3),
            'conversion_cpu_s':round(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,3),
            'conversion_peak_rss_kib':after.ru_maxrss,'stored_bytes':sum(r['stored_size'] or 0 for r in rows),
            'source_bytes_each':len(raw.getvalue())},indent=2))

asyncio.run(run())
