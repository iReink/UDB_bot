"""Real HTTP cache/slow-image browser checks (routing mocks disable browser cache)."""
import io
import json
import mimetypes
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
TOKEN='z'*43
hits=Counter()
raw=io.BytesIO();Image.new('RGB',(1200,800),'#bc689c').save(raw,'JPEG')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        path=urlsplit(self.path).path
        hits[path]+=1
        headers={}
        if path.endswith(('/image','/thumb')):
            if path.endswith('/image'):time.sleep(.5)
            body=raw.getvalue();mime='image/jpeg';headers['Cache-Control']='private, max-age=3600'
        elif path.startswith('/api/albums/'):
            body=json.dumps({'name':'Тест кеша','authenticated':True,'next_cursor':None,'photos':[
                {'id':i,'image_url':f'/api/albums/{TOKEN}/photos/{i}/image','thumb_url':f'/api/albums/{TOKEN}/photos/{i}/thumb',
                 'can_delete':False,'can_move':False} for i in range(1,4)]}).encode();mime='application/json'
        elif path.startswith('/albums/'):
            body=(ROOT/'web/templates/album.html').read_bytes();mime='text/html'
        elif path.startswith('/static/'):
            file=ROOT/'web'/path.lstrip('/');body=file.read_bytes();mime=mimetypes.guess_type(path)[0]
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        for key,value in headers.items():self.send_header(key,value)
        self.end_headers();self.wfile.write(body)

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        for mobile in (False,True):
            hits.clear()
            context=browser.new_context(viewport={'width':390 if mobile else 1400,'height':844},has_touch=mobile,is_mobile=mobile)
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/albums/{TOKEN}')
            page.locator('#albumGrid button').first.click()
            assert page.locator('#viewerImage').get_attribute('src').endswith('/1/thumb')
            assert page.locator('.viewer-stage').get_attribute('aria-busy')=='true'
            page.wait_for_function("document.querySelector('#viewerImage').src.endsWith('/1/image')")
            width=page.locator('.viewer-stage').bounding_box()['width']
            assert abs(width-page.locator('#viewerImage').bounding_box()['width'])<1
            if mobile:
                assert page.locator('#photoNext span').evaluate('(e)=>getComputedStyle(e).opacity')=='0'
                box=page.locator('.viewer-stage').bounding_box()
                page.touchscreen.tap(box['x']+box['width']*.85,box['y']+box['height']*.5)
            else:
                page.locator('#photoNext').hover();page.wait_for_timeout(180)
                assert page.locator('#photoNext span').evaluate('(e)=>getComputedStyle(e).opacity')=='1'
                page.locator('#photoNext').click()
            page.wait_for_function("document.querySelector('#viewerImage').src.endsWith('/2/image')")
            page.locator('#photoPrev').click()
            page.wait_for_function("document.querySelector('#viewerImage').src.endsWith('/1/image')")
            assert hits[f'/api/albums/{TOKEN}/photos/1/image']==1
            # Cross-page HTTP cache, not merely the JS decoded-image cache.
            page.reload();page.locator('#albumGrid button').first.click()
            page.wait_for_function("document.querySelector('#viewerImage').src.endsWith('/1/image')")
            assert hits[f'/api/albums/{TOKEN}/photos/1/image']==1
            page.locator('#photoNext').click();page.locator('#photoNext').click()
            page.wait_for_function("document.querySelector('#viewerImage').src.endsWith('/3/image')")
            page.wait_for_timeout(600)
            assert page.locator('#viewerImage').get_attribute('src').endswith('/3/image')
            assert not errors,errors
            print('Cache, overlay arrows, preview and rapid navigation OK:', 'mobile' if mobile else 'desktop')
            context.close()
        browser.close()
finally:
    server.shutdown();server.server_close()
