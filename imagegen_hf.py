"""Qwen public Space adapter. HF owns its quota; Cloudflare accounting is separate."""
from concurrent.futures import TimeoutError
from contextlib import closing
from datetime import datetime, timezone
import os
import logging
from pathlib import Path
import tempfile
import time
import uuid

import requests

MODEL = 'Qwen/Qwen-Image-2.1'
QUOTA_URL = 'https://huggingface.co/api/spaces/zero-gpu/quota'


class Unavailable(RuntimeError):
    """A safe reason to use the fallback, without exposing service credentials."""


def configured():
    return bool(os.getenv('HF_TOKEN', '').strip())


def quota(key):
    response = requests.get(QUOTA_URL, headers={'Authorization': 'Bearer '+key}, timeout=(5, 10))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or 'current' not in data:
        raise Unavailable('quota_unavailable')
    return data


def duration_estimate(size, references):
    # Public Space reservation function, not actual billed GPU seconds.
    width, height = size
    pixels = width*height
    tokens = pixels/256 + .2*references*4096
    per_step = 3.4e-6*tokens**1.363*1.3
    return int(min(300, (4+2*references+1.5e-6*pixels+40*per_step)*1.25))


def has_budget(snapshot, size, references):
    runs = snapshot.get('runs') or {}
    remaining = runs.get('remaining')
    if remaining is None and 'limit' in runs and 'used' in runs:
        remaining = runs['limit']-runs['used']
    return (float(snapshot['current']) >= duration_estimate(size, references)
            and (remaining is None or float(remaining) > 0))


def same_quota_window(before, after):
    # HF reports slightly drifting reset timestamps; a subminute drift is not a reset.
    try:
        start=datetime.fromisoformat(before['resetsAt'].replace('Z','+00:00'))
        end=datetime.fromisoformat(after['resetsAt'].replace('Z','+00:00'))
        return abs((end-start).total_seconds()) < 60
    except (KeyError,TypeError,ValueError):
        return False


def make_client(key, directory):
    from gradio_client import Client
    return Client(MODEL, token=key, verbose=False, download_files=directory,
                  httpx_kwargs={'timeout':180})


def file_input(path):
    from gradio_client import handle_file
    return {'image':handle_file(str(path)), 'caption':None}


def wait_job(client, deadline, **kwargs):
    job = client.submit(**kwargs)
    try:
        return job.result(timeout=max(.01, deadline-time.monotonic()))
    except TimeoutError:
        job.cancel()
        raise Unavailable('timeout') from None


def generate(task, prompt, images, size, on_attempt=None):
    import imagegen
    from ai_runtime import record_attempt
    key = os.getenv('HF_TOKEN', '').strip()
    before = after = None
    started = False
    outcome = 'unavailable'
    reason = ''
    client = None
    try:
        before = quota(key)
        if not has_budget(before, size, len(images)):
            raise Unavailable('quota_exhausted')
        with tempfile.TemporaryDirectory(prefix='udb-qwen-') as directory:
            client = make_client(key, directory)
            gallery = []
            for index, raw in enumerate(images):
                path = Path(directory)/('reference_'+str(index)+'.jpg')
                path.write_bytes(raw)
                gallery.append(file_input(path))
            deadline = time.monotonic()+180
            seed = int.from_bytes(os.urandom(4), 'big') % 2147483647
            with closing(imagegen.connect()) as conn, conn:
                conn.execute('UPDATE ai_tasks SET provider=?,model=? WHERE id=?',
                             ('huggingface', MODEL, task['id']))
            if on_attempt:
                on_attempt(1)
            started = True
            # Both endpoints MUST share this client/session: preparation stores gr.State.
            wait_job(client, deadline, api_name='/prepare_request', input_images=gallery,
                     original_prompt=prompt, enable_extend=False, custom_size=True,
                     quality='speed', seed=seed, randomize_seed=False)
            result = wait_job(client, deadline, api_name='/generate_request',
                     original_prompt=prompt, enable_extend=False, custom_size=True,
                     log_dir='./generation_logs_paper_case', seed=seed,
                     height=size[1], width=size[0], negative_prompt='')
            path = result[0] if isinstance(result, (tuple, list)) else result
            raw = imagegen.validate_image(Path(path).read_bytes())
            outcome = 'received'
            return raw
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Unavailable) else type(exc).__name__
        raise Unavailable(reason) from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if started:
            try:
                after = quota(key)
            except Exception:
                pass
        try:
            usage = {'quota_before':before, 'quota_after':after}
            if before is not None and after is not None and same_quota_window(before,after):
                # Shared account delta; other clients can contribute, so never call it exact billing.
                usage['gpu_seconds_account_delta'] = max(0, float(before['current'])-float(after['current']))
            context = {'prompt':prompt, 'width':size[0], 'height':size[1],
                       'references':[{'index':n, 'bytes':len(raw)} for n,raw in enumerate(images)]}
            record_attempt('tasks', task['id'], 'imagegen-hf-'+uuid.uuid4().hex,
                {'provider':'huggingface', 'model':MODEL, 'usage':usage,
                 'calls':[{'provider':'huggingface', 'model':MODEL, 'context':context,
                           'at':datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                           'status':'received' if outcome=='received' else 'error', 'error':reason, 'response':usage}] if started else []},
                outcome if started else 'skipped:'+reason)
        except Exception as exc:
            logging.getLogger(__name__).warning('HF audit failed: task=%s error=%s',task['id'],type(exc).__name__)
