"""Read-only Mini App API, separate from administrative dashboard permissions."""
from contextlib import closing
from typing import Literal
import requests
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from summary_access import authenticate, SummaryAccessError
from web.ai_dashboard import connection


class ViewRequest(BaseModel):
    init_data: str = Field(min_length=1,max_length=16384)
    summary_id: int|None = Field(default=None,gt=0)
    direction: Literal['before','after']|None = None


def require_member(bot_token,user_id,chat_id):
    if chat_id>0:
        if chat_id!=user_id:
            raise HTTPException(403,'Это саммари другого личного чата')
        return
    try:
        response=requests.post(f'https://api.telegram.org/bot{bot_token}/getChatMember',
                               json={'chat_id':chat_id,'user_id':user_id},timeout=(2,5))
        data=response.json()
    except (requests.RequestException,ValueError):
        raise HTTPException(503,'Не удалось проверить доступ к чату. Попробуйте ещё раз.')
    if not response.ok or not data.get('ok'):
        raise HTTPException(503,'Не удалось проверить доступ к чату. Боту нужны права на проверку участников.')
    member=data.get('result') or {}
    if member.get('status') not in ('creator','administrator','member') and not (member.get('status')=='restricted' and member.get('is_member') is True):
        raise HTTPException(403,'Саммари доступно только участникам этого чата')


def message_link(conn,chat_id,start,end):
    # t.me/c links apply to supergroups/channels. Legacy groups and DMs have no equivalent.
    if chat_id > -1000000000000 or not str(chat_id).startswith('-100'):
        return None
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages_reactions' AND type='table'").fetchone():
        return None
    row=conn.execute('SELECT message_id FROM messages_reactions WHERE chat_id=? AND date>? AND date<=? AND message_id>0 ORDER BY date,message_id LIMIT 1',(chat_id,start,end)).fetchone()
    return f'https://t.me/c/{str(chat_id)[4:]}/{row[0]}' if row else None


def timeline(path,chat_id,summary_id=None,direction=None):
    with closing(connection(path)) as conn:
        fields='id,summary_text,window_start,window_end'
        scope="chat_id=? AND status='done' AND summary_text IS NOT NULL AND trim(summary_text)<>''"
        if summary_id is None:
            current=conn.execute(f'SELECT {fields} FROM ai_summary WHERE {scope} ORDER BY window_end DESC,id DESC LIMIT 1',(chat_id,)).fetchone()
        else:
            current=conn.execute(f'SELECT {fields} FROM ai_summary WHERE {scope} AND id=?',(chat_id,summary_id)).fetchone()
            if current is None:
                raise HTTPException(404,'Саммари не найдено в этом чате')
        if current is None:
            return {'items':[],'index':0,'has_before':False,'has_after':False}
        previous=list(conn.execute(f'SELECT {fields} FROM ai_summary WHERE {scope} AND (window_end,id)<(?,?) ORDER BY window_end DESC,id DESC LIMIT 20',(chat_id,current['window_end'],current['id'])))
        following=list(conn.execute(f'SELECT {fields} FROM ai_summary WHERE {scope} AND (window_end,id)>(?,?) ORDER BY window_end,id LIMIT 20',(chat_id,current['window_end'],current['id'])))
        if direction=='before': before,after=min(19,len(previous)),0
        elif direction=='after': before,after=0,min(19,len(following))
        else:
            before=min(len(previous),max(9,19-len(following)))
            after=min(len(following),19-before)
        items=[dict(r) for r in reversed(previous[:before])]+[dict(current)]+[dict(r) for r in following[:after]]
        for item in items:
            item['message_link']=message_link(conn,chat_id,item['window_start'],item['window_end'])
        return {'items':items,'index':before,'has_before':len(previous)>before,'has_after':len(following)>after}


def register(app,templates,db_path,bot_token):
    @app.get('/summary_app',response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse('summary_app.html',{'request':request},headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.post('/api/summary-app/view')
    def view(data: ViewRequest):
        try:
            user,chat=authenticate(data.init_data,bot_token)
        except SummaryAccessError as exc:
            raise HTTPException(401,str(exc))
        require_member(bot_token,user,chat)
        return JSONResponse(timeline(db_path,chat,data.summary_id,data.direction),headers={'Cache-Control':'no-store'})
