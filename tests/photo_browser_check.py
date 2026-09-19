"""Offline browser smoke: real frontend files with stubbed APIs, no production writes."""
import json
import io
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'.tmp_chrome'/'photo_checks'
OUTPUT.mkdir(parents=True,exist_ok=True)
token='x'*43
sample=io.BytesIO();Image.new('RGB',(900,600),'#bd789d').save(sample,'JPEG')
event={'id':1,'name':'Карпачо в кафе','date':'2026-09-16','time':'15:00','expired':True,
       'photo_count':12,'album_url':'/albums/'+token,'participants':[],'all_participants':[],
       'drivers':[],'datetime_iso':'2026-09-16T15:00:00+05:00','viewer_is_participant':True}

def serve(route):
    path=urlsplit(route.request.url).path
    if path.startswith('/api/albums/'):
        if path.endswith(('/image','/thumb')):
            route.fulfill(body=sample.getvalue(),content_type='image/jpeg');return
        route.fulfill(json={'name':'Карпачо в кафе','authenticated':True,'next_cursor':None,'photos':[
          {'id':i,'thumb_url':f'/api/albums/{token}/photos/{i}/thumb','image_url':f'/api/albums/{token}/photos/{i}/image',
           'can_delete':True,'can_move':True} for i in range(1,13)]});return
    if path=='/api/state':route.fulfill(json={'authorized':False});return
    if path=='/api/daily/search':
        query=parse_qs(urlsplit(route.request.url).query)
        match=query.get('q',[''])[0].casefold() in event['name'].casefold() and query.get('date',[event['date']])[0]==event['date']
        route.fulfill(json={'events':[event] if match else [],'next_cursor':None});return
    if path=='/api/daily/calendar':route.fulfill(json={'days':[{'date':'2026-09-16','event_count':1,'has_photos':1}]});return
    if path.startswith('/api/'):
        route.fulfill(json={'ok':True,'events':[]});return
    if path.startswith('/albums/'):
        file=ROOT/'web/templates/album.html'
    elif path=='/':file=ROOT/'web/templates/index.html'
    elif path.startswith('/static/'):file=ROOT/'web'/path.lstrip('/')
    else:route.fulfill(status=404);return
    if not file.is_file():route.fulfill(status=404);return
    import mimetypes
    route.fulfill(body=file.read_bytes(),content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    for width,height in [(390,844),(1440,900)]:
        page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/*',serve)
        page.goto('http://photo.test/albums/'+token)
        page.locator('#albumGrid button').first.wait_for()
        assert page.locator('#albumBack').is_hidden()
        assert page.locator('#albumGrid button').count()==12
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(OUTPUT/f'grid-{width}.png'))
        page.locator('#albumGrid button').first.click()
        assert page.locator('#albumViewer').evaluate('(e)=>e.open')
        page.locator('#photoMenuButton').click();page.locator('#photoMove').click()
        menu_box=page.locator('#photoMenuButton').bounding_box()
        assert menu_box['width']==menu_box['height']==44
        assert 'позже' in page.locator('#confirmText').inner_text()
        page.locator('#confirmCancel').click()
        page.screenshot(path=str(OUTPUT/f'viewer-{width}.png'))
        page.keyboard.press('Escape')
        page.goto('http://photo.test/')
        page.wait_for_timeout(500)
        page.evaluate("document.getElementById('dailyPanel').classList.remove('hidden'); activeSelectedChatId=-1;")
        page.locator('#dailySearchInput').fill('КАРПАЧО')
        page.locator('.daily-card-title').filter(has_text='Карпачо').wait_for()
        assert '📷' in page.locator('.daily-card-title').inner_text()
        page.locator('.daily-card-head').click()
        photo_link=page.get_by_role('link',name='📷 Фото',exact=True)
        assert photo_link.count()==1
        assert photo_link.evaluate('(e)=>getComputedStyle(e).textDecorationLine')=='none'
        assert photo_link.evaluate('(e)=>getComputedStyle(e).height')=='32px'
        assert photo_link.get_attribute('href').endswith('?from=daily')
        calendar_box=page.locator('#dailyCalendarToggle').bounding_box()
        assert calendar_box['width']==calendar_box['height']==44
        page.locator('#dailyCalendarToggle').click()
        page.locator('.daily-calendar-grid button').filter(has_text='📷').wait_for()
        page.screenshot(path=str(OUTPUT/f'calendar-{width}.png'))
        page.locator('.daily-calendar-grid button').filter(has_text='📷').click()
        assert page.locator('#dailyCalendarToggle').inner_text()=='×'
        cross_box=page.locator('#dailyCalendarToggle').bounding_box()
        assert cross_box['width']==cross_box['height']==44
        page.locator('#dailyCalendarToggle').click()
        assert page.locator('#dailyCalendarToggle').inner_text()=='📅'
        page.locator('#dailySearchInput').fill('ТАКОГО ДЕЙЛИКА НЕТ')
        page.wait_for_timeout(500)
        assert page.locator('.daily-card').count()==0
        assert 'не найдены' in page.locator('.daily-empty-state').inner_text()
        page.goto('http://photo.test/albums/'+token+'?from=daily')
        page.locator('#albumBack').wait_for()
        back_box=page.locator('#albumBack').bounding_box()
        assert back_box['width']==back_box['height']==44
        assert page.locator('#albumBack').inner_text()=='←'
        assert page.locator('#albumBack').evaluate('(e)=>getComputedStyle(e).backgroundColor')==page.locator('#albumLogin').evaluate('(e)=>getComputedStyle(e).backgroundColor')
        assert not errors, errors
        print(f'Browser checks passed at {width}x{height}')
        page.close()
    browser.close()
