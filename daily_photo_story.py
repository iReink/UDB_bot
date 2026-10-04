"""One immutable story per daily; resumable vision batches and silent backfill."""
from contextlib import closing
from datetime import datetime,timedelta
from html import escape
import json
import time


def ensure_schema(conn):
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS daily_photo_story_state(key TEXT PRIMARY KEY,value REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS daily_photo_stories(
        daily_id INTEGER PRIMARY KEY,chat_id INTEGER NOT NULL,due_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'waiting_photos',notify_chat INTEGER NOT NULL DEFAULT 0,
        batch_id INTEGER,photos_json TEXT NOT NULL DEFAULT '[]',analyses_json TEXT NOT NULL DEFAULT '[]',
        next_offset INTEGER NOT NULL DEFAULT 0,story_text TEXT,response_message_id INTEGER,
        created_at TEXT NOT NULL,finished_at TEXT,delivery_state TEXT NOT NULL DEFAULT 'silent');
      CREATE INDEX IF NOT EXISTS idx_daily_story_state ON daily_photo_stories(status,due_at);
    ''')


def ordered_photos(rows):
    # Missing capture time is never claimed to be a reliable shooting timestamp.
    return sorted([dict(r) for r in rows],key=lambda p:(p.get('captured_at') or p['sent_at'],p['message_id'],p['id']))


def schedule(now=None, *, allow_backfill=None):
    import photo_story as stories
    from photo_albums import event_time
    from ai_runtime import stamp,enabled
    now=time.time() if now is None else now
    if allow_backfill is None:
        from photo_albums import TZ
        allow_backfill=4<=datetime.fromtimestamp(now,TZ).hour<7
    with closing(stories.connection()) as c,c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='daily_events'").fetchone():return 0
        c.execute('BEGIN IMMEDIATE')
        c.execute("INSERT OR IGNORE INTO daily_photo_story_state VALUES('activated_at',?)",(now,))
        activated=c.execute("SELECT value FROM daily_photo_story_state WHERE key='activated_at'").fetchone()[0]
        events=c.execute('SELECT id,chat_id,name,date,time FROM daily_events ORDER BY date DESC,time DESC').fetchall()
        count=0
        for event in events:
            try:due=(event_time(event)+timedelta(days=1)).timestamp()
            except (ValueError,TypeError):continue
            if due>now:continue
            existing=c.execute('SELECT * FROM daily_photo_stories WHERE daily_id=?',(event['id'],)).fetchone()
            if existing and existing['status']!='waiting_photos':continue
            rows=c.execute("""SELECT p.id,p.message_id,p.sent_at,p.captured_at,p.path FROM daily_photos p
              JOIN photo_batches b ON b.id=p.batch_id WHERE b.daily_id=? AND b.chat_id=?
              AND b.decision='attached' AND p.status='ready'""",(event['id'],event['chat_id'])).fetchall()
            if rows and count>=2:continue
            c.execute("INSERT OR IGNORE INTO daily_photo_stories(daily_id,chat_id,due_at,created_at) VALUES(?,?,?,?)",
                      (event['id'],event['chat_id'],due,stamp()))
            if not rows or not enabled(event['chat_id']):continue
            notify=not existing and due>activated and any(p['sent_at']<=due for p in rows)
            if not notify and not allow_backfill:continue
            from PIL import Image
            from photo_convert import capture_time
            from photo_albums import safe_path
            scanned=[]
            for row in rows:
                p=dict(row)
                if p['captured_at'] is None:
                    try:
                        with Image.open(safe_path(p['path'])) as image:p['captured_at']=capture_time(image)
                    except (OSError,ValueError,TypeError):pass
                    if p['captured_at'] is not None:c.execute('UPDATE daily_photos SET captured_at=? WHERE id=?',(p['captured_at'],p['id']))
                p.pop('path',None);scanned.append(p)
            photos=ordered_photos(scanned)
            batch=c.execute("""INSERT INTO ai_photo_story_batches(chat_id,user_id,group_key,first_message_id,
                touched_at,status,created_at) VALUES(?,0,?,0,?,'queued',?)""",
                (event['chat_id'],'daily:'+str(event['id']),now,stamp())).lastrowid
            payload={'daily_id':event['id'],'batch_id':batch,'photos':[{'daily_photo_id':p['id'],'daily_id':event['id']} for p in photos[:10]],
                     'offset':0,'notify_chat':bool(notify),'event_name':event['name']}
            b=c.execute('SELECT * FROM ai_photo_story_batches WHERE id=?',(batch,)).fetchone()
            prompt=stories.VISION_PROMPT+'\nНазвание встречи (данные автора): '+event['name'][:300]
            prompt+='\nКадры упорядочены по EXIF, когда он есть; остальные — по отправке. Не считай это доказательством последовательности событий.'
            task=stories.insert_task(c,'photo_story',b,prompt,payload)
            c.execute('UPDATE ai_tasks SET priority=? WHERE id=?',(50 if notify else -1,task))
            c.execute('UPDATE ai_photo_story_batches SET vision_task_id=? WHERE id=?',(task,batch))
            c.execute("UPDATE daily_photo_stories SET status='queued',notify_chat=?,batch_id=?,photos_json=?,delivery_state=? WHERE daily_id=?",
                      (int(notify),batch,json.dumps(photos), 'pending' if notify else 'silent',event['id']))
            count+=1
        return count


def image_parts(photos,chat_id):
    import photo_story as stories
    import photo_albums as albums
    from ai_providers import PromptTooLarge
    from PIL import Image,ImageOps
    import io,base64
    if not 1<=len(photos)<=10:raise PromptTooLarge('Invalid daily photo batch')
    parts=[]
    for index,photo in enumerate(photos,1):
        with closing(stories.connection()) as c:
            p=c.execute("""SELECT p.path FROM daily_photos p JOIN photo_batches b ON b.id=p.batch_id
              WHERE p.id=? AND p.chat_id=? AND b.chat_id=p.chat_id AND b.daily_id=?
              AND b.decision='attached' AND p.status='ready'""",(photo['daily_photo_id'],chat_id,photo['daily_id'])).fetchone()
        if not p:continue
        try:
            with Image.open(albums.safe_path(p['path'])) as original:
                if original.width*original.height>20_000_000:raise ValueError('Oversized image')
                image=ImageOps.exif_transpose(original).convert('RGB');image.thumbnail((1280,1280))
                out=io.BytesIO();image.save(out,format='JPEG',quality=80);image.close()
                parts.extend([{'text':f'Кадр {index} подборки.'},{'inlineData':{'mimeType':'image/jpeg','data':base64.b64encode(out.getvalue()).decode()}}])
        except (ValueError,OSError,Image.DecompressionBombError) as exc:raise PromptTooLarge('Не удалось прочитать сохранённое фото') from exc
    if not parts:raise PromptTooLarge('Фотографии подборки удалены или недоступны')
    return parts


def merge_task(c,b,payload,materials):
    import photo_story as stories
    payload=dict(payload);payload.pop('photos',None);payload.pop('reduce_queue',None)
    if sum(len(m) for m in materials)>12000:
        payload['reduce_queue']=materials[2:];materials=materials[:2]
    return stories.insert_task(c,'photo_story_merge',b,stories.MERGE_PROMPT+'\n\n'.join(materials),payload)


def complete(task,raw_output,worker_error,send):
    import photo_story as stories
    from ai_runtime import stamp
    from ai_tasks import validate_response_output
    from ai_formatting import answer_html
    payload=json.loads(task['payload_json']);daily_id=payload['daily_id']
    try:
        if worker_error:raise ValueError('Обработчик не завершил историю дейлика')
        text=validate_response_output(raw_output,allow_markdown=True)
        if len(text)>(6000 if task['task_type']=='photo_story' else 3000):raise ValueError('Сократи ответ до допустимой длины')
    except ValueError as exc:
        result=stories.retry(task,str(exc))
        if result['status']=='failed':
            with closing(stories.connection()) as c,c:c.execute("UPDATE daily_photo_stories SET status='failed' WHERE daily_id=?",(daily_id,))
        return result
    with closing(stories.connection()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        d=c.execute('SELECT * FROM daily_photo_stories WHERE daily_id=? AND chat_id=?',(daily_id,task['chat_id'])).fetchone()
        event=c.execute('SELECT name FROM daily_events WHERE id=? AND chat_id=?',(daily_id,task['chat_id'])).fetchone()
        has_photos=c.execute("SELECT 1 FROM daily_photos p JOIN photo_batches b ON b.id=p.batch_id WHERE b.daily_id=? AND b.chat_id=? AND b.decision='attached' AND p.status='ready' LIMIT 1",(daily_id,task['chat_id'])).fetchone()
        if not d or not event or not has_photos:
            c.execute("UPDATE ai_tasks SET status='cancelled',finished_at=?,lease_until=NULL WHERE id=?",(stamp(),task['id']))
            c.execute("UPDATE daily_photo_stories SET status='cancelled' WHERE daily_id=?",(daily_id,))
            c.execute("UPDATE ai_photo_story_batches SET status='cancelled' WHERE id=?",(payload['batch_id'],))
            return {'ok':True,'status':'cancelled','task_id':task['id']}
        if task['task_type']=='photo_story':
            photos=json.loads(d['photos_json']);offset=payload['offset'];analyses=json.loads(d['analyses_json'])
            if offset<d['next_offset']:
                return {'ok':True,'status':'done','task_id':task['id']}
            analyses.append(text);end=min(len(photos),offset+10)
            b=c.execute('SELECT * FROM ai_photo_story_batches WHERE id=?',(d['batch_id'],)).fetchone()
            next_payload={**payload,'offset':end}
            if end<len(photos):
                next_payload['photos']=[{'daily_photo_id':p['id'],'daily_id':daily_id} for p in photos[end:end+10]]
                child=stories.insert_task(c,'photo_story',b,task['prompt'],next_payload)
            else:
                child=merge_task(c,b,next_payload,analyses)
                c.execute('UPDATE ai_photo_story_batches SET merge_task_id=? WHERE id=?',(child,b['id']))
            c.execute('UPDATE daily_photo_stories SET analyses_json=?,next_offset=? WHERE daily_id=?',(json.dumps(analyses,ensure_ascii=False),end,daily_id))
            c.execute('UPDATE ai_tasks SET priority=? WHERE id=?',(50 if d['notify_chat'] else 5,child))
            c.execute("UPDATE ai_tasks SET status='done',result_text=?,finished_at=?,updated_at=?,lease_until=NULL WHERE id=?",(text,stamp(),stamp(),task['id']))
            return {'ok':True,'status':'done','task_id':task['id'],'final_task_id':child}
        if 'reduce_queue' in payload:
            b=c.execute('SELECT * FROM ai_photo_story_batches WHERE id=?',(d['batch_id'],)).fetchone()
            child=merge_task(c,b,payload,payload['reduce_queue']+[text])
            c.execute('UPDATE ai_tasks SET priority=? WHERE id=?',(50 if d['notify_chat'] else 5,child))
            c.execute("UPDATE ai_tasks SET status='done',result_text=?,finished_at=?,lease_until=NULL WHERE id=?",(text,stamp(),task['id']))
            return {'ok':True,'status':'done','task_id':task['id'],'final_task_id':child}
        # Persist the album story BEFORE attempting Telegram. No late photo can replace it.
        c.execute("UPDATE daily_photo_stories SET story_text=coalesce(story_text,?),status='done',finished_at=? WHERE daily_id=?",(text,stamp(),daily_id))
        should_send=d['notify_chat'] and d['delivery_state']=='pending'
        if should_send:c.execute("UPDATE daily_photo_stories SET delivery_state='sending' WHERE daily_id=?",(daily_id,))
    response=None
    if should_send:
        rendered='Вчера прошёл дейлик <b>'+escape(event['name'][:200])+ '</b>. И вот что там было:\n\n'+answer_html(text)
        # Keep one Telegram message even when generated formatting expands unexpectedly.
        if len(rendered.encode('utf-16-le'))//2>4000:rendered='Вчера прошёл дейлик <b>'+escape(event['name'][:200])+'</b>. И вот что там было:\n\n'+escape(text)
        try:response=send(int(task['chat_id']),rendered)
        except Exception:
            with closing(stories.connection()) as c,c:c.execute("UPDATE daily_photo_stories SET delivery_state='unknown' WHERE daily_id=?",(daily_id,))
            raise
    with closing(stories.connection()) as c,c:
        c.execute("UPDATE daily_photo_stories SET response_message_id=?,delivery_state=CASE WHEN notify_chat=1 AND delivery_state='sending' THEN 'sent' ELSE delivery_state END WHERE daily_id=?",(response,daily_id))
        c.execute("UPDATE ai_tasks SET status='done',result_text=?,response_message_id=?,finished_at=?,updated_at=?,lease_until=NULL WHERE id=?",(text,response,stamp(),stamp(),task['id']))
        c.execute("UPDATE ai_photo_story_batches SET status='done' WHERE id=?",(d['batch_id'],))
    return {'ok':True,'status':'done','task_id':task['id'],'response_message_id':response,'album_story':True}
