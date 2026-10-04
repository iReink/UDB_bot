"""Private Google embedding adapter and shared, conservative project budget."""
from __future__ import annotations

import json
import math
import os
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
import ai_http
import rag_repository as repo


class Unavailable(Exception):
    def __init__(self, reason, retry_after=60):
        super().__init__(reason)
        self.retry_after=max(1,int(retry_after))


def limits():
    # Explicit values must come from verified project quotas. Zero disables calls.
    return {key:max(0,int(os.getenv('RAG_EMBED_'+key,'0'))) for key in ('RPM','TPM','RPD')}


def reserve(text, purpose):
    budget=limits()
    # Local tokenizer is an estimate, not Google's tokenizer. Keep headroom and
    # settle the reservation against authoritative usageMetadata after success.
    count=max(32,math.ceil(repo.tokens(text)*1.5))
    pacific=datetime.now(ZoneInfo('America/Los_Angeles'))
    midnight=pacific.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    reset=(pacific.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).astimezone(timezone.utc)
    day_wait=max(1,(reset-datetime.now(timezone.utc)).total_seconds())
    with closing(repo.connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if not all(budget.values()): raise Unavailable('Квота embedding не подтверждена или равна нулю',3600)
        from mechanics import urgent
        priority=urgent(conn)
        if priority and purpose!='mechanics_index':
            raise Unavailable('Приоритетная индексация справочника; доступен лексический поиск',30)
        cooldown=repo.get_state(conn,'embedding_retry_at')
        if cooldown and cooldown>repo.stamp():
            raise Unavailable(repo.get_state(conn,'embedding_reason','Ожидание Google'),(datetime.fromisoformat(cooldown)-datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
        minute=list(conn.execute('SELECT at,tokens,purpose FROM ai_rag_usage WHERE at>? ORDER BY at',(repo.stamp(-60),)))
        daily=conn.execute('SELECT count(*) FROM ai_rag_usage WHERE at>=?',(midnight,)).fetchone()[0]
        indexing=conn.execute("SELECT count(*) FROM ai_rag_usage WHERE at>=? AND purpose IN ('index','mechanics_index')",(midnight,)).fetchone()[0]
        handbook_calls=conn.execute("SELECT count(*) FROM ai_rag_usage WHERE at>=? AND purpose='mechanics_index'",(midnight,)).fetchone()[0]
        rpm,tpm=(int(budget[k]*.9) for k in ('RPM','TPM'))
        acceptance_probe=purpose=='mechanics_probe' and os.getenv('RAG_ACCEPTANCE_PROBE')=='1'
        rpd=budget['RPD'] if priority or acceptance_probe else budget['RPD']*95//100
        index_daily_limit=budget['RPD'] if priority else budget['RPD']*85//100
        repo.set_state(conn,'quota',repo.encode(dict(rpm=budget['RPM'],tpm=budget['TPM'],rpd=budget['RPD'],used_today=daily,mechanics_used_today=handbook_calls,index_used_today=indexing,index_daily_limit=index_daily_limit,reserved_tokens_minute=sum(r[1] for r in minute))))
        if daily>=rpd:
            conn.commit()
            raise Unavailable('Общий дневной бюджет embedding исчерпан',day_wait)
        if purpose in ('index','mechanics_index') and indexing>=index_daily_limit:
            conn.commit()
            raise Unavailable('Дневной бюджет индексации исчерпан; поиск по памяти доступен в пределах оставшегося бюджета',day_wait)
        if count>tpm:
            conn.commit()
            raise Unavailable('Один фрагмент превышает минутный бюджет embedding',3600)
        index_minute=[r for r in minute if r[2] in ('index','mechanics_index')]
        share=.9 if priority else .6
        index_busy=purpose in ('index','mechanics_index') and (len(index_minute)>=int(budget['RPM']*share) or sum(r[1] for r in index_minute)+count>int(budget['TPM']*share))
        if len(minute)>=rpm or sum(r[1] for r in minute)+count>tpm or index_busy:
            wait=61 if not minute else max(1,(datetime.fromisoformat(minute[0][0])+timedelta(seconds=61)-datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
            conn.commit()
            raise Unavailable('Ожидание минутного бюджета embedding',wait)
        return conn.execute('INSERT INTO ai_rag_usage(at,purpose,tokens) VALUES (?,?,?)',(repo.stamp(),purpose,count)).lastrowid


def embed(text, purpose='query', timeout=3):
    prefix=('title: Справочник механик | text: ' if purpose=='mechanics_index' else 'title: Переписка Telegram | text: ' if purpose=='index' else 'task: search result | query: ')
    content=prefix+text
    cache_purpose='query' if purpose=='mechanics_probe' else purpose
    key=repo.digest([repo.MODEL,768,cache_purpose,content])
    with closing(repo.connect()) as conn:
        cached=conn.execute('SELECT vector_json FROM ai_rag_cache WHERE key=? AND expires_at>?',(key,repo.stamp())).fetchone()
        if cached: return json.loads(cached[0])
    secret=os.getenv('GEMINI_API_KEY','')
    if not secret: raise Unavailable('Ключ Google не настроен',3600)
    ident=reserve(content,purpose)
    try:
        response=ai_http.post(f'https://generativelanguage.googleapis.com/v1beta/models/{repo.MODEL}:embedContent',
            headers={'x-goog-api-key':secret},json={'content':{'parts':[{'text':content}]},'outputDimensionality':768},timeout=(min(1,timeout),timeout))
        data=response.json()
    except (requests.RequestException,ValueError):
        with closing(repo.connect()) as conn, conn:
            conn.execute("UPDATE ai_rag_usage SET status='error' WHERE id=?",(ident,))
        raise Unavailable('Embedding API временно недоступен',30) from None
    if not response.ok:
        wait=60
        if response.status_code==429:
            details=data.get('error',{}).get('details',[])
            for detail in details:
                delay=detail.get('retryDelay','')
                if delay:
                    try: wait=max(wait,float(delay.rstrip('s')))
                    except ValueError: pass
                for violation in detail.get('violations',[]):
                    quota=(violation.get('quotaId','')+' '+violation.get('quotaMetric','')).lower()
                    if 'perday' in quota or 'per_day' in quota: wait=86400
                    if str(violation.get('quotaValue',''))=='0': wait=86400
            try: wait=max(wait,float(response.headers.get('Retry-After',0)))
            except ValueError: pass
        elif response.status_code in (400,401,403,404): wait=86400
        reason=f'Google Embedding: HTTP {response.status_code}'
        with closing(repo.connect()) as conn, conn:
            conn.execute("UPDATE ai_rag_usage SET status='error' WHERE id=?",(ident,))
            repo.set_state(conn,'embedding_retry_at',repo.stamp(wait))
            repo.set_state(conn,'embedding_reason',reason)
        raise Unavailable(reason,wait)
    vector=data.get('embedding',{}).get('values',[])
    if len(vector)!=768 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in vector):
        raise Unavailable('Google вернул несовместимый вектор',60)
    norm=math.sqrt(sum(v*v for v in vector))
    if norm<=0: raise Unavailable('Google вернул пустой вектор',60)
    vector=[v/norm for v in vector]
    with closing(repo.connect()) as conn, conn:
        conn.execute("UPDATE ai_rag_usage SET status='done' WHERE id=?",(ident,))
        actual=data.get('usageMetadata',{}).get('promptTokenCount')
        if isinstance(actual,int) and not isinstance(actual,bool) and actual>0:
            conn.execute('UPDATE ai_rag_usage SET tokens=? WHERE id=?',(actual,ident))
        conn.execute('INSERT OR REPLACE INTO ai_rag_cache VALUES (?,?,?)',(key,repo.encode(vector),repo.stamp(86400)))
    return vector


class Qdrant:
    def __init__(self, collection=None):
        self.url=os.getenv('QDRANT_URL','').rstrip('/')
        self.key=os.getenv('QDRANT_API_KEY','')
        self.collection=collection or os.getenv('QDRANT_COLLECTION') or repo.COLLECTION

    def request(self, method, path, body=None, timeout=3):
        if not self.url.startswith('https://') or not self.key:
            raise Unavailable('Qdrant не настроен',3600)
        try:
            r=requests.request(method,self.url+path,headers={'api-key':self.key},json=body,timeout=(min(1,timeout),timeout))
            if not r.ok: raise Unavailable(f'Qdrant: HTTP {r.status_code}',60)
            return r.json().get('result')
        except (requests.RequestException,ValueError):
            raise Unavailable('Qdrant временно недоступен',30) from None

    def ensure(self):
        collections=self.request('GET','/collections',timeout=15)
        names={r['name'] for r in collections['collections']}
        path='/collections/'+self.collection
        if self.collection not in names:
            self.request('PUT',path,{'vectors':{'size':768,'distance':'Cosine'},'on_disk_payload':True},timeout=20)
        info=self.request('GET',path,timeout=15)
        vector=info['config']['params']['vectors']
        if vector.get('size')!=768 or vector.get('distance')!='Cosine':
            raise Unavailable('Коллекция Qdrant имеет другую размерность или метрику',86400)
        for field,kind in [('chat_id','integer'),('index_version','keyword'),('max_message_id','integer')]:
            self.request('PUT',path+'/index?wait=true',{'field_name':field,'field_schema':kind},timeout=20)

    def upsert(self, chunk, vector):
        parts=json.loads(chunk['parts_json'])
        payload=dict(chat_id=chunk['chat_id'],index_version=repo.VERSION,day=chunk['day'],generation=chunk['generation'],
            message_ids=sorted({p['message_id'] for p in parts}),user_ids=sorted({p['user_id'] for p in parts}),
            min_message_id=chunk['min_id'],max_message_id=chunk['max_id'],start_at=chunk['start_at'],end_at=chunk['end_at'],
            text=chunk['text'],content_hash=chunk['content_hash'])
        result=self.request('PUT','/collections/'+self.collection+'/points?wait=true',{'points':[{'id':chunk['id'],'vector':vector,'payload':payload}]},timeout=20)
        if result.get('status')!='completed': raise Unavailable('Qdrant не подтвердил загрузку',15)

    def query(self, vector, chat, before, timeout=1):
        return self.request('POST','/collections/'+self.collection+'/points/query',
            {'query':vector,'limit':40,'with_payload':False,'filter':{'must':[
                {'key':'chat_id','match':{'value':chat}}, {'key':'index_version','match':{'value':repo.VERSION}},
                {'key':'max_message_id','range':{'lt':before}}]}},timeout=timeout)['points']

    def delete(self, ids):
        if ids:
            result=self.request('POST','/collections/'+self.collection+'/points/delete?wait=true',{'points':ids},timeout=20)
            if result.get('status')!='completed': raise Unavailable('Qdrant не подтвердил удаление',30)
