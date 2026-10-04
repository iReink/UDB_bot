"""Shared worker protocol for the PC and VPS; heartbeats continue during generation."""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
import requests
import ai_audit

from ai_providers import ProviderUnavailable, PromptTooLarge, call_external, call_local

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, provider, queues, task_types=None):
        self.provider = provider
        self.queues = queues
        self.task_types=task_types or []
        self.worker_id = f'{provider}-{socket.gethostname()}-{uuid.uuid4().hex}'
        self.backend = os.getenv('AI_WORKER_BACKEND_URL', 'http://94.183.184.65:8080').rstrip('/')
        self.token = os.getenv('AI_WORKER_TOKEN', '').strip()
        self.stop = threading.Event()
        self.task = None
        self.task_lock = threading.Lock()
        self.http = threading.local()

    def request(self, path, payload=None, **query):
        if not hasattr(self.http,'session'):self.http.session=requests.Session()
        method = self.http.session.post if payload is not None else self.http.session.get
        r = method(self.backend + path, headers={'Authorization': 'Bearer ' + self.token}, params=query, **({'json': payload} if payload is not None else {}), timeout=(3, 40))
        r.raise_for_status()
        return r.json()

    def beat(self):
        ready = True
        if self.provider == 'local':
            try:
                ready = requests.get(os.getenv('OLLAMA_URL', 'http://localhost:11434').rstrip('/') + '/api/tags', timeout=2).ok
            except requests.RequestException:
                ready = False
        elif not (os.getenv('GROQ_API_KEY', '').strip() or os.getenv('GEMINI_API_KEY', '').strip()):
            ready = False
        self.request('/api/ai/workers/heartbeat', {'worker_id': self.worker_id, 'provider': self.provider, 'queues': self.queues, 'ready': ready,'task_types':self.task_types})
        with self.task_lock:
            task = self.task
        if task:
            self.request('/api/ai/workers/renew', {'worker_id': self.worker_id, 'queue': task['queue'], 'task_id': task['id'], 'lease_token': task['lease_token']})

    def heartbeat_loop(self):
        while not self.stop.wait(10):
            try:
                self.beat()
            except Exception as exc:
                log.warning('heartbeat: %s', type(exc).__name__)

    def process(self, task):
        with self.task_lock:
            self.task = task
        data = {'worker_id': self.worker_id, 'lease_token': task['lease_token']}
        kind = task['task_type']
        timeout = (15 if self.provider == 'local' else 45) if kind == 'type_check' else 300 if kind in ('profile_update', 'chat_summary') else 120 if kind in ('photo_story','photo_story_merge') else 45
        ai_audit.begin()
        try:
            output, metadata = (call_local if self.provider == 'local' else call_external)(task, timeout)
            data.update(output=output, metadata=metadata)
        except ProviderUnavailable as exc:
            data.update(error=str(exc), error_kind='unavailable', metadata={'retry_after':exc.retry_after,'provider_cooldown':exc.provider_cooldown,'refusal_kind':exc.kind})
        except PromptTooLarge as exc:
            data.update(error=str(exc), error_kind='too_large')
        except Exception as exc:
            data.update(error=type(exc).__name__, error_kind='unavailable')
        data.setdefault('metadata', {})['calls'] = ai_audit.take()
        path = f"/api/ai/{task['queue']}/{task['id']}/result"
        # Retry the same token/result if POST outcome is uncertain; never regenerate.
        try:
            while not self.stop.is_set():
                try:
                    result = self.request(path, data)
                    if result.get('status') == 'accepted':
                        self.stop.wait(2)
                        continue
                    log.info('%s #%s source=%s model=%s status=%s', task['queue'], task['id'], self.provider, data.get('metadata', {}).get('model'), result.get('status'))
                    break
                except requests.HTTPError as exc:
                    if exc.response.status_code in (404, 409, 422):
                        log.warning('Result discarded: %s #%s HTTP %s', task['queue'], task['id'], exc.response.status_code)
                        break
                    self.stop.wait(5)
                except requests.RequestException:
                    self.stop.wait(5)
        finally:
            with self.task_lock:
                self.task = None

    def run(self):
        if not self.token:
            log.error('AI_WORKER_TOKEN is required')
            return 2
        if self.provider == 'groq':
            from ai_runtime import initialize
            initialize()
        try:
            self.beat()
        except Exception as exc:
            log.warning('Initial heartbeat: %s', type(exc).__name__)
        thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        thread.start()
        try:
            while not self.stop.is_set():
                try:
                    task=self.request('/api/ai/workers/next',worker_id=self.worker_id,queues=','.join(self.queues),wait_seconds=25).get('task')
                    if task:self.process(task)
                except Exception as exc:
                    log.warning('poll: %s', type(exc).__name__)
                    self.stop.wait(5)
        except KeyboardInterrupt:
            return 0
        finally:
            self.stop.set()
            thread.join(timeout=3)


def run(provider, queues):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    return Worker(provider, queues).run()
