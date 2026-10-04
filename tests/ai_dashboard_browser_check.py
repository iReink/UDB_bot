"""Browser acceptance checks with synthetic fixtures; no production history or API keys."""
import json
import mimetypes
import tempfile
from io import BytesIO
from PIL import Image
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=Path(tempfile.gettempdir())/'udb-ai-dashboard-browser'
OUTPUT.mkdir(exist_ok=True)
records=[dict(id=i,queue='tasks',task_type='profile_update' if i==2 else 'chat_summary' if i in (3,4,5) else 'response',status='done',chat_id=-42,user_id=123,created_at='2026-10-01T12:00:00',actual_model='gemini-flash' if i%2 else 'gpt-oss',context_preview='Передаваемый JSON-контекст — подробная история и системная инструкция '*3,response_preview='Структурированный ответ модели с подробным описанием результата '*3,receipt={'response_message_id':100+i}) for i in range(1,61)]
records[1]['response_preview']='Тестовый участник'
records[5].update(task_type='type_check',classification_type='imagegen',response_preview='imagegen')
records[6]['response_preview']='Чистый ответ пользователю'
profiles=[dict(task_id=8,user_id=123,chat_id=-42,profile_date='2026-09-30',profile_json=json.dumps({'short_summary':'Любит кофе и книги','interests':['кофе','книги']})),dict(task_id=2,user_id=123,chat_id=-42,profile_date='2026-10-01',profile_json=json.dumps({'short_summary':'Любит чай и книги','stable_interests':['чай','книги'], 'communication_style':'Спокойное общение и технические обсуждения. '*8, 'local_memes':['Шутки о чае','Книжный клуб'], 'facts':['Искусственный факт для проверки вёрстки. '*5]*6}))]
summaries=[dict(task_id=i,chat_id=-42,window_start=f'2026-10-01T0{i}:00:00',window_end=f'2026-10-01T0{i+1}:00:00',summary_text=f'Саммари {i}\n\nУчастники обсуждали планы на выходные, делились новостями и обсуждали работу бота.\n\nЭто искусственный пример для проверки интерфейса, без реальной переписки.') for i in (3,4,5)]


with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    for width,height in [(1440,1000),(390,844)]:
        state={'auth':False,'polls':0}
        def serve(route):
            url=urlsplit(route.request.url);path=url.path
            if path=='/api/auth/code':state['auth']=True;route.fulfill(json={'ok':True});return
            if path.startswith('/api/ai-dashboard/'):
                if not state['auth']:route.fulfill(status=401,json={'detail':'Not authorized'});return
                if path.endswith('/tasks'):
                    state['polls']+=1;q=parse_qs(url.query);filtered=records
                    if q.get('kind'):filtered=[r for r in filtered if r['task_type']==q['kind'][0]]
                    if q.get('model'):filtered=[r for r in filtered if r['actual_model']==q['model'][0]]
                    pg=int(q.get('page',['1'])[0]);route.fulfill(json=dict(rows=filtered[(pg-1)*50:pg*50],total=len(filtered),page=pg,pages=max(1,(len(filtered)+49)//50),stats=[{'model':'gemini-flash','task_type':'response','count':40,'historical':0},{'model':'gpt-oss','task_type':'profile_update','count':14,'historical':0}]+[{'model':f'extra-{i}','task_type':'response','count':2 if i==0 else 1,'historical':0} for i in range(5)],chats=[{'id':-42,'title':'Тестовый чат'}],kinds=['response','profile_update','chat_summary','type_check'],models=['gemini-flash','gpt-oss']));return
                if '/avatar/' in path:
                    if width<650:route.fulfill(status=404)
                    else:
                        raw=BytesIO();Image.new('RGB',(80,80),'#bfd3dd').save(raw,format='PNG');route.fulfill(body=raw.getvalue(),content_type='image/png')
                    return
                ident=int(path.rsplit('/',1)[1]);record=next((r for r in records if r['id']==ident),records[0])
                if '/history/' in path:
                    items=profiles if ident in (2,8) else summaries;index=next(i for i,r in enumerate(items) if r['task_id']==ident);route.fulfill(json={'items':items,'index':index,'previous_profile':json.loads(items[index-1]['profile_json']) if index and ident in (2,8) else None});return
                route.fulfill(json={'task':dict(record,prompt='Historical prompt',payload_json=json.dumps({'display_name':'Тестовый участник'})),'calls':[{'model':'gemini-flash','provider':'google','at':'2026-10-01T12:00:00','status':'received','context':{'systemInstruction':{'text':'Будь корректным'},'contents':[{'role':'user','text':'<img src=x onerror=alert(1)>\nЭто безопасный текст'}]},'response':{'answer':'Ответ','details':{'count':5}}}],'attempts':[]});return
            if path=='/ai_tasks':file=ROOT/'web/templates/ai_tasks.html'
            elif path.startswith('/static/'):file=ROOT/'web'/path.lstrip('/')
            else:route.fulfill(status=404);return
            mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            route.fulfill(body=file.read_bytes(),content_type=mime+'; charset=utf-8')
        page=browser.new_page(viewport={'width':width,'height':height},timezone_id='Asia/Yekaterinburg')
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.clock.install()
        page.route('**/*',serve);page.goto('http://dashboard.test/ai_tasks')
        page.locator('#login').wait_for(state='visible');page.locator('#code').fill('SYNTHETIC');page.locator('#login-form button').click();page.locator('#dashboard').wait_for(state='visible')
        assert page.locator('#rows tr').count()==50
        assert page.locator('#call-count').inner_text()=='60'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('thead th').nth(1).text_content()=='Чат'
        assert page.locator('#rows tr').first.locator('td').nth(1).inner_text()=='Тестовый чат'
        classifier=page.get_by_role('button',name='Ответ задачи 6',exact=True)
        assert classifier.inner_text()=='Генерация изображения'
        assert classifier.get_attribute('title')=='imagegen'
        assert page.get_by_role('button',name='Ответ задачи 2',exact=True).inner_text()=='Тестовый участник'
        assert page.get_by_role('button',name='Ответ задачи 7',exact=True).inner_text()=='Чистый ответ пользователю'
        assert page.locator('#model-chart .bar-row').count()==5
        page.locator('#models-more').click()
        assert page.locator('#model-chart .bar-row').count()==7
        before=state['polls'];page.wait_for_timeout(3300);assert state['polls']==before
        page.clock.fast_forward(120000);page.wait_for_function("document.querySelector('#live').textContent.includes('Обновлено')")
        assert state['polls']>before
        assert page.locator('#model-chart .bar-row').count()==7
        assert page.locator('#models-more').get_attribute('aria-expanded')=='true'
        page.locator('#models-more').click()
        assert page.locator('#model-chart .bar-row').count()==5
        page.screenshot(path=str(OUTPUT/f'dashboard-{width}.png'),full_page=True)
        page.locator('#rows button').first.click();page.locator('#content .field-name').first.wait_for();assert '<img' in page.locator('#content').inner_text();assert page.locator('#content img').count()==0
        assert 'Системная инструкция' in page.locator('#content').inner_text()
        records.insert(0,dict(records[0],id=999))
        page.clock.fast_forward(120000)
        page.wait_for_function("document.querySelector('button[data-key=\"tasks/999/context\"]')!==null")
        assert page.locator('#modal').evaluate('(e)=>e.open')
        records.pop(0)
        page.keyboard.press('Escape');assert not page.locator('#modal').evaluate('(e)=>e.open')
        page.locator('#kind').select_option('profile_update');page.get_by_role('button',name='Ответ задачи 2',exact=True).click();page.locator('.profile-lead').wait_for();assert page.locator('.removed,.added').count()==0
        assert page.locator('.profile-lead').inner_text()=='Любит чай и книги'
        assert 'Мемы и шутки' in page.locator('#content').inner_text()
        assert page.locator('.profile-body ul').count()>0
        heading=page.locator('.profile-heading').first.bounding_box();body=page.locator('.profile-body').first.bounding_box()
        assert (body['x']>heading['x']+100) if width>650 else (body['y']>heading['y'])
        if width<650:page.wait_for_function("document.querySelector('#modal-avatar img')===null")
        else:page.wait_for_function("document.querySelector('#modal-avatar img').naturalWidth>0")
        assert page.locator('#modal-avatar').inner_text()=='ТУ'
        page.screenshot(path=str(OUTPUT/f'dossier-{width}.png'));page.locator('#previous').click();page.wait_for_function("document.getElementById('history-date').textContent==='2026-09-30'");assert page.locator('#previous').is_disabled();page.locator('#following').click();page.wait_for_function("document.getElementById('history-date').textContent==='2026-10-01'");page.locator('.modal-scroll').evaluate('(e)=>e.scrollTop=500');assert page.locator('.modal-scroll').evaluate('(e)=>e.scrollTop')>0
        page.mouse.click(4,4);assert not page.locator('#modal').evaluate('(e)=>e.open')
        page.locator('[data-reset="kind"]').click();page.wait_for_function("document.querySelectorAll('#rows tr').length===50")
        page.get_by_role('button',name='Ответ задачи 4',exact=True).click();page.locator('.summary-card').first.wait_for();assert page.locator('#modal').get_attribute('class')=='summary'
        assert '05:00' in page.locator('#history-date').inner_text()
        assert page.evaluate("summaryDate('2026-10-02T11:40:56')===date('2026-10-02T06:40:56Z')")
        page.screenshot(path=str(OUTPUT/f'summary-{width}.png'));page.locator('#content').hover();page.mouse.wheel(0,150);page.wait_for_function("document.querySelector('.summary-card[aria-hidden=false]').textContent.includes('Саммари 5')")
        page.keyboard.press('ArrowLeft');page.wait_for_function("document.querySelector('.summary-card[aria-hidden=false]').textContent.includes('Саммари 4')");page.keyboard.press('Escape')
        page.locator('#model').select_option('gpt-oss');page.wait_for_function("document.querySelectorAll('#rows tr').length===30");page.locator('#reset-all').click();page.wait_for_function("document.querySelectorAll('#rows tr').length===50");assert page.locator('#days').input_value()=='7'
        page.locator('#next-page').click();page.wait_for_function("document.querySelectorAll('#rows tr').length===10");before=state['polls'];page.locator('#refresh-tasks').click();page.wait_for_function("!document.querySelector('#refresh-tasks').disabled");assert state['polls']==before+1;assert page.locator('#page-label').inner_text()=='2 / 2'
        box=page.locator('#refresh-tasks').bounding_box();assert box['x']+box['width']<=width and box['y']<250
        before=state['polls'];page.clock.fast_forward(120000);page.wait_for_function("!document.querySelector('#refresh-tasks').disabled");assert state['polls']>before;assert page.locator('#page-label').inner_text()=='2 / 2'
        assert not errors,errors
        page.close()
    browser.close()
print(f'Browser checks passed at 1440px and 390px. Screenshots: {OUTPUT}')
