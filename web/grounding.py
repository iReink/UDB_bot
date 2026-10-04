"""Read-only, unguessable, expiring Google result pages and common completion path."""
from contextlib import closing
from html import escape
import json
import os
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import ai_grounding as grounding
import ai_runtime as rt
from ai_formatting import answer_html,telegram_chunks


def register(app):
    @app.get('/ai_sources/{token}',response_class=HTMLResponse)
    def result_page(token: str):
        if len(token)!=43:raise HTTPException(404)
        with closing(rt.connect()) as conn:
            row=conn.execute('SELECT result_json FROM ai_grounding_results WHERE token=? AND expires>?',(token,rt.stamp())).fetchone()
        if not row:raise HTTPException(404)
        result=json.loads(row[0]);attribution='Google Maps' if result['tool']=='maps' else 'Google Search'
        answer=answer_html(result['text'],result.get('sources',[]),web=True)
        links=''.join(f'<li><a target="_blank" rel="noopener noreferrer" href="{escape(s["url"],quote=True)}">{escape(s["title"])}</a></li>' for s in result.get('sources',[]))
        suggestions=result.get('suggestions_html','')
        # Raw vendor markup never shares our origin, cookies or script privileges.
        frame=('<iframe sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" title="Рекомендации Google Search" srcdoc="'+escape('<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data: https:; base-uri &#39;none&#39;; form-action &#39;none&#39;">'+suggestions,quote=True)+'"></iframe>') if suggestions else ''
        body='<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Ответ и источники Google</title><style>body{margin:0;background:#f5f7fa;color:#26384b;font:17px/1.8 system-ui}main{max-width:820px;margin:40px auto;padding:32px;background:white;border-radius:24px}p{margin:0 0 1em}h2{font-size:20px;margin:1.6em 0 .6em}li{margin:.35em 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:14px;border-radius:12px}article{overflow-wrap:anywhere}a{color:#3869ba}iframe{width:100%;min-height:240px;border:0}h1{font-size:24px}@media(max-width:600px){main{margin:0;padding:24px;border-radius:0}}</style><main><h1>'+attribution+'</h1><article>'+answer+'</article><ul>'+links+'</ul>'+frame+'</main></html>'
        return HTMLResponse(body,headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'"})


def complete(task, data, send, response_from_context):
    import ai_tasks as tasks
    ident=task['id'];kind=task['task_type'];payload=json.loads(task['payload_json'] or '{}')
    try:
        if kind=='maps_translation':
            original=grounding.get(payload['grounding_task_id'])
            if not original:raise ValueError('Grounding result expired')
            result=json.loads(original['result_json'])
            result['text']=tasks.validate_response_output(data.output,allow_markdown=True)
            # Translation is separate; names and links never come from translated output.
            for marker in set(__import__('re').findall(r'\[\d+\]',json.loads(original['result_json'])['text'])):
                if marker not in result['text']:raise ValueError('Translation lost source markers')
        else:result=grounding.validate_result(data.output)
    except (ValueError,KeyError,TypeError) as exc:
        retry=grounding.retry_or_fail(ident,str(exc))
        return {'ok':True,'status':'retry' if retry else 'failed','task_id':ident,'error':str(exc)}
    if result['status']=='fallback':
        from web_search import build_web_context,WebSearchError
        try:context=build_web_context(question=payload['message_text'],search_plan=payload['plan'])
        except WebSearchError:context='Внешние источники недоступны. Не утверждай актуальные сведения как подтверждённые.'
        if result['tool']=='maps':context+='\nGoogle Maps недоступен. Обозначь, что это веб-поиск без подтверждения Maps. Не выдумывай адреса и часы работы.'
        final=response_from_context(chat_id=task['chat_id'],user_id=task['user_id'],request_message_id=task['request_message_id'],message_text=payload['message_text'],trigger_reason='grounding_fallback',web_context=context)
        tasks.mark_response_task_done(ident,response_text=json.dumps(result,ensure_ascii=False),response_message_id=None)
        return {'ok':True,'status':'fallback','task_id':ident,'final_task_id':final}
    if result['status']=='grounded':grounding.save(ident,result)
    if kind=='maps_grounding' and result['status']=='grounded':
        translation={'grounding_task_id':ident}
        prompt='Переведи ответ Google Maps на русский. Верни только перевод, сохрани абзацы, заголовки, списки и выделения исходного ответа. Не оборачивай весь ответ в code fence. Не меняй названия мест, адреса, числа и ссылки. Сохрани все маркеры источников [1], [2] и т.д. Не добавляй сведения, приветствия или комментарии.\n\n'+result['text']
        final=grounding.create_task(task,'maps_translation',translation,prompt)
        tasks.mark_response_task_done(ident,response_text=json.dumps(result,ensure_ascii=False),response_message_id=None)
        return {'ok':True,'status':'done','task_id':ident,'final_task_id':final}
    viewer=None
    if result['status']=='grounded':
        row=grounding.get(ident)
        viewer=os.getenv('AI_GROUNDING_PUBLIC_URL','https://94.183.184.65').rstrip('/')+'/ai_sources/'+row['token']
    chunks=telegram_chunks(grounding.telegram_html(result,viewer));message_id=None
    for index,text in enumerate(chunks):
        sent=send(task['chat_id'],text,reply_to_message_id=task['request_message_id'] if index==0 else None)
        if index==0:message_id=sent
    tasks.mark_response_task_done(ident,response_text=json.dumps(result,ensure_ascii=False),response_message_id=message_id)
    return {'ok':True,'status':'done','task_id':ident,'response_message_id':message_id}
