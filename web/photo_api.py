"""Album routes registered against the existing web session/serializer."""
import calendar
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from web.asset_version import PHOTO_ASSET_VERSION

import photo_albums as store
from daily_search_store import filters as daily_search_filters
from settings import ADMIN_IDS


def install(app, require_session, selected_chat, serialize, participants_map):
    def viewer(request):
        try:
            return int(require_session(request)['telegram_user_id'])
        except HTTPException:
            return None

    def album(c, token):
        row = c.execute('''SELECT a.*,d.name FROM daily_photo_albums a JOIN daily_events d
          ON d.id=a.daily_id AND d.chat_id=a.chat_id WHERE a.token=?''',(token,)).fetchone()
        if not row:
            raise HTTPException(404,'Альбом не найден')
        return row

    @app.get('/albums/{token}')
    def album_page(token: str):
        with store.connection() as c:
            album(c,token)
        html = (Path(__file__).parent/'templates'/'album.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('__PHOTO_ASSET_VERSION__', PHOTO_ASSET_VERSION),headers={
            'X-Robots-Tag':'noindex, nofollow','Referrer-Policy':'no-referrer',
            'Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"})

    @app.get('/api/albums/{token}')
    def album_list(token: str, request: Request, after: int = 0, limit: int = 60):
        uid = viewer(request)
        limit = max(1,min(limit,100))
        with store.connection() as c:
            a = album(c,token)
            rows = c.execute('''SELECT p.* FROM daily_photos p JOIN photo_batches b ON b.id=p.batch_id
              WHERE b.daily_id=? AND b.chat_id=? AND b.decision='attached' AND p.status='ready'
              AND p.id>? ORDER BY p.id LIMIT ?''',(a['daily_id'],a['chat_id'],after,limit+1)).fetchall()
        data = [{'id':p['id'],'width':p['width'],'height':p['height'],
                 'image_url':f'/api/albums/{token}/photos/{p["id"]}/image',
                 'thumb_url':f'/api/albums/{token}/photos/{p["id"]}/thumb',
                 'can_delete':uid is not None and (uid in ADMIN_IDS or uid==p['user_id']),
                 'can_move':uid in ADMIN_IDS} for p in rows[:limit]]
        return JSONResponse({'name':a['name'],'photos':data,'authenticated':uid is not None,
                             'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None},
                            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/api/albums/{token}/photos/{photo_id}/{variant}')
    def album_file(token: str,photo_id: int,variant: str,request: Request):
        if variant not in ('image','thumb'):
            raise HTTPException(404)
        with store.connection() as c:
            a=album(c,token)
            p=c.execute('''SELECT p.* FROM daily_photos p JOIN photo_batches b ON b.id=p.batch_id
               WHERE p.id=? AND b.daily_id=? AND b.chat_id=? AND b.decision='attached' AND p.status='ready' ''',
               (photo_id,a['daily_id'],a['chat_id'])).fetchone()
        if not p:
            raise HTTPException(404)
        path=store.safe_path(p['thumb_path'] if variant=='thumb' else p['path'])
        if not path.is_file():
            raise HTTPException(404)
        response = FileResponse(path,media_type='image/webp' if variant=='thumb' else 'image/jpeg',stat_result=path.stat(),
            headers={'Cache-Control':'private, max-age=3600','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
        # Check visibility above before honoring validators, including after deletion.
        validators = request.headers.get('if-none-match', '').split(',')
        if any(v.strip().removeprefix('W/') == response.headers['etag'] or v.strip() == '*' for v in validators):
            return Response(status_code=304, headers=dict(response.headers))
        return response

    @app.delete('/api/album-photos/{photo_id}')
    def remove(photo_id: int,request: Request):
        # Cookies alone must not authorize cross-origin mutations.
        origin=request.headers.get('origin')
        if not origin or urlsplit(origin).netloc!=request.headers.get('host'):
            raise HTTPException(403,'Недопустимый источник запроса')
        uid=int(require_session(request)['telegram_user_id'])
        try:
            store.delete_photo(photo_id,uid,uid in ADMIN_IDS)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404,str(exc)) from exc
        return {'ok':True}

    def filters(uid,chat,q,date,only_mine):
        try:
            return daily_search_filters(uid,chat,q,date,only_mine)
        except ValueError as exc:
            raise HTTPException(400,'Некорректная дата') from exc

    @app.get('/api/daily/search')
    def search(request: Request,q: str='',date: str='',only_mine: bool=False,cursor: str='',limit: int=30):
        uid,chat=selected_chat(request)
        clauses,args=filters(uid,chat,q,date,only_mine)
        if cursor:
            try:
                dt,event_id=cursor.rsplit('|',1)
                datetime.strptime(dt,'%Y-%m-%d %H:%M')
                event_id=int(event_id)
            except ValueError:
                raise HTTPException(400,'Некорректный курсор')
            clauses.append("(d.date||' '||d.time<? OR (d.date||' '||d.time=? AND d.id<?))")
            args.extend([dt,dt,event_id])
        limit=max(1,min(limit,50))
        with store.connection() as c:
            rows=c.execute("SELECT d.* FROM daily_events d WHERE "+' AND '.join(clauses)+
                ' ORDER BY d.date DESC,d.time DESC,d.id DESC LIMIT ?',(*args,limit+1)).fetchall()
        page=rows[:limit]
        people=participants_map(chat,[r['id'] for r in page])
        return {'events':[serialize(r,uid,people.get(r['id'],[])) for r in page],
                'next_cursor':f"{page[-1]['date']} {page[-1]['time']}|{page[-1]['id']}" if len(rows)>limit else None}

    @app.get('/api/daily/calendar')
    def month(request: Request,month: str,q: str='',only_mine: bool=False):
        uid,chat=selected_chat(request)
        try:
            start=datetime.strptime(month,'%Y-%m')
        except ValueError:
            raise HTTPException(400,'Некорректный месяц')
        clauses,args=filters(uid,chat,q,'',only_mine)
        clauses.append('d.date BETWEEN ? AND ?')
        args.extend([start.strftime('%Y-%m-01'),start.strftime('%Y-%m-')+str(calendar.monthrange(start.year,start.month)[1])])
        with store.connection() as c:
            rows=c.execute('''SELECT d.date,COUNT(*) event_count,
              MAX(EXISTS(SELECT 1 FROM photo_batches b JOIN daily_photos p ON p.batch_id=b.id
               WHERE b.daily_id=d.id AND b.chat_id=d.chat_id AND b.decision='attached' AND p.status='ready')) has_photos
              FROM daily_events d WHERE '''+' AND '.join(clauses)+' GROUP BY d.date',args).fetchall()
        return {'days':[dict(r) for r in rows]}
