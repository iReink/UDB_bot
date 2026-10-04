"""Provider adapters; output remains subject to the existing backend validators."""
from __future__ import annotations

import json
import os
import re
import requests
import ai_audit


class ProviderUnavailable(Exception):
    def __init__(self, message, retry_after=30, provider_cooldown=True, kind="transport"):
        super().__init__(message)
        self.retry_after = retry_after
        self.provider_cooldown = provider_cooldown
        self.kind=kind


class PromptTooLarge(ValueError):
    pass


def tokenizer():
    import tiktoken
    return tiktoken.get_encoding("o200k_harmony")


def prepare_prompt(task, limit=6000):
    enc = tokenizer()
    prompt = task["prompt"]
    from mechanics import strip_last
    from ai_tasks import CREATOR_POLICY_MARKER, CREATOR_PERSONA_GUIDE, CREATOR_REPLY_GUARD, BOT_PERSONA_GUIDE
    policy = prompt.split('\n\n', 1)[0] if prompt.startswith(CREATOR_POLICY_MARKER + '\n') else ''
    professional = bool(policy and CREATOR_PERSONA_GUIDE in prompt)

    def preserve_policy(rebuilt):
        if professional:
            rebuilt = rebuilt.replace(BOT_PERSONA_GUIDE, CREATOR_PERSONA_GUIDE)
            if not rebuilt.endswith(CREATOR_REPLY_GUARD):
                rebuilt += '\n\n' + CREATOR_REPLY_GUARD
        return policy + '\n\n' + rebuilt if policy and not rebuilt.startswith(policy + '\n\n') else rebuilt
    if "Схема БД:" in prompt:
        instructions, schema = prompt.split("Схема БД:", 1)
        lines = []
        structural = []
        for line in schema.splitlines():
            if line.startswith("### "):
                lines.append(line)
                structural.append(line)
            elif line.startswith("|"):
                cells = [c.strip().strip('`') for c in line.strip('|').split('|')]
                if len(cells) >= 3 and cells[0] not in ("Поле", "Колонка") and not cells[0].startswith('-'):
                    lines.append(cells[0] + ': ' + cells[1] + ' ' + cells[-1])
                    structural.append(cells[0] + ': ' + cells[1])
            elif line.startswith("- ") or line.startswith("Ключ:"):
                lines.append(line)
                structural.append(line)
        prompt = "Схема БД:\n" + '\n'.join(lines) + '\n\n' + instructions
        if len(enc.encode(prompt, disallowed_special=())) > limit:
            prompt = "Схема БД:\n" + '\n'.join(structural) + '\n\n' + instructions
    while len(enc.encode(prompt,disallowed_special=()))>limit:
        trimmed=strip_last(prompt)
        if trimmed is None:break
        prompt=trimmed
    if task.get("task_type") == "data_analysis_response" and len(enc.encode(prompt, disallowed_special=())) > limit:
        # Trim complete rows of the ORIGINAL valid JSON, never slice serialized JSON.
        from ai_tasks import get_data_analysis, build_data_analysis_response_prompt
        analysis_id = task.get('payload', {}).get('analysis_id')
        analysis = get_data_analysis(analysis_id) if analysis_id else None
        if analysis:
            analysis = dict(analysis)
            rows = json.loads(analysis["rows_json"] or '[]')
            while rows:
                analysis["rows_json"] = json.dumps(rows, ensure_ascii=False)
                analysis["truncated"] = 1
                prompt = preserve_policy(build_data_analysis_response_prompt(analysis=analysis))
                if len(enc.encode(prompt, disallowed_special=())) <= limit:
                    break
                rows.pop()
            if not rows:
                analysis["rows_json"] = '[]'
                prompt = preserve_policy(build_data_analysis_response_prompt(analysis=analysis))
    while len(enc.encode(prompt,disallowed_special=()))>limit:
        trimmed=strip_last(prompt)
        if trimmed is None:break
        prompt=trimmed
    lines = prompt.splitlines()
    if task.get('task_type') == 'response':
        from rag_search import strip_last_fragment
        while len(enc.encode(prompt, disallowed_special=())) > limit:
            trimmed = strip_last_fragment(prompt)
            if trimmed is None:
                break
            prompt = trimmed
        lines = prompt.splitlines()
    while len(enc.encode('\n'.join(lines), disallowed_special=())) > limit:
        index = next((i for i, line in enumerate(lines) if line.startswith('- [') and '] ' in line), None)
        if index is None:
            raise PromptTooLarge("Обязательная часть запроса превышает допустимый размер API")
        lines.pop(index)
    return '\n'.join(lines)


def call_local(task, timeout):
    if task.get('task_type')=='grounding_notice':return task['prompt'],{'provider':'backend','model':'location-clarification'}
    if task.get('task_type') in ('web_grounding','maps_grounding'):
        from ai_grounding import local_fallback
        return local_fallback(task)
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip('/')
    model = task["model"]
    if task["queue"] != "tasks":
        model = os.getenv("AI_CLASSIFIER_MODEL", "gemma4:e4b") or model
    try:
        payload = {"model": model, "prompt": task["prompt"], "stream": False, "options": {"temperature": 0}}
        if task.get('system_instruction'):
            payload['system'] = task['system_instruction']
        response = ai_audit.post(url + '/api/generate', provider='local', model=model, json=payload, timeout=(3, timeout))
        response.raise_for_status()
        output = str(response.json().get('response') or '').strip()
        if not output:
            raise ProviderUnavailable("local: empty response")
        return output, {"model": model, "provider": "local"}
    except (requests.RequestException, ValueError) as exc:
        raise ProviderUnavailable("local: " + type(exc).__name__) from exc


def external_models(kind):
    if kind in ('photo_story','imagegen_prepare'):
        from photo_story import VISION_MODELS
        return VISION_MODELS
    from ai_runtime import MODEL_HEAVY, MODEL_GOOGLE_PRIMARY, MODEL_GOOGLE_SECONDARY, MODEL_GOOGLE_LAST
    google=(MODEL_GOOGLE_PRIMARY,MODEL_GOOGLE_SECONDARY,MODEL_GOOGLE_LAST)
    return (MODEL_HEAVY,)+google if kind in ('text_to_sql','data_analysis_sql','data_analysis_response') else google


def refusal_kind(response, body=None):
    error=(body or {}).get('error') or {}
    if not isinstance(error,dict):error={}
    violations=[v for d in error.get('details',[]) for v in d.get('violations',[])]
    if any('perday' in str(v.get('quotaId','')).lower() for v in violations):return 'daily'
    message=str(error.get('message','')).lower()
    if response.status_code==429 and any(word in message for word in ('per day','per_day','daily quota')):return 'daily'
    if response.status_code==429:return 'minute'
    return 'transport' if response.status_code>=500 or response.status_code in (408,502,504) else 'permanent'


def retry_delay(response, body=None):
    from ai_runtime import quota_day_wait
    value=response.headers.get('retry-after','')
    delay=int(float(value))+1 if re.fullmatch(r'\d+(\.\d+)?',value) else 30
    error=body.get('error') if isinstance(body,dict) else {}
    if not isinstance(error,dict):error={}
    for detail in error.get('details',[]):
        retry=detail.get('retryDelay','')
        if re.fullmatch(r'\d+(\.\d+)?s',retry):delay=max(delay,int(float(retry[:-1]))+1)
        for violation in detail.get('violations',[]):
            if 'perday' in violation.get('quotaId','').lower():
                delay=max(delay,quota_day_wait('gemini'))
    return delay


def call_groq(task, timeout, model=None):
    from ai_runtime import MODEL_HEAVY, reserve, settle, cool_model, budget_wait, budget_kind
    model=model or MODEL_HEAVY
    key=os.getenv('GROQ_API_KEY','').strip()
    if not key:raise ProviderUnavailable('External API: Groq key unavailable',provider_cooldown=False)
    prompt=prepare_prompt(task)
    system=task.get('system_instruction') or ''
    count=len(tokenizer().encode(prompt+system,disallowed_special=()))+64
    messages=([{'role':'system','content':system}] if system else [])+[{'role':'user','content':prompt}]
    completion_limit=1536
    reservation=reserve(model,count,completion_limit)
    if reservation is None:raise ProviderUnavailable('External API: model budget occupied',budget_wait(model,count,completion_limit),False,budget_kind(model,count,completion_limit))
    try:
        r=ai_audit.post('https://api.groq.com/openai/v1/chat/completions',provider='groq',model=model,headers={'Authorization':'Bearer '+key},json={
            'model':model,'messages':messages,
            'temperature':0,'reasoning_effort':'low','include_reasoning':False,
            'max_completion_tokens':completion_limit},timeout=(5,timeout))
    except requests.RequestException:
        cool_model(model,30)
        raise ProviderUnavailable('External API: Groq transport failure',provider_cooldown=False)
    if not r.ok:
        settle(reservation,{})
        try:body=r.json()
        except ValueError:body={}
        kind=refusal_kind(r,body);delay=retry_delay(r,body)
        if kind=='daily':
            from ai_runtime import quota_day_wait
            delay=max(delay,quota_day_wait(model))
        cool_model(model,delay,kind)
        raise ProviderUnavailable('External API: Groq HTTP '+str(r.status_code),delay,False,kind)
    try:
        body=r.json();usage=body.get('usage') or {};settle(reservation,usage)
        output=str(body['choices'][0]['message'].get('content') or '').strip()
        if not output:raise ValueError('Empty result')
        return output,{'provider':'groq','model':model,'usage':usage}
    except (ValueError,KeyError,IndexError,TypeError):
        cool_model(model,30)
        raise ProviderUnavailable('External API: malformed Groq result',provider_cooldown=False)


def google_request(model, method, payload, key, timeout, tool=None):
    from ai_runtime import cool_model
    try:
        post = requests.post if method == 'countTokens' else lambda url, **kw: ai_audit.post(url, provider='google', model=model, **kw)
        r=post('https://generativelanguage.googleapis.com/v1beta/models/'+model+':'+method,
                        headers={'x-goog-api-key':key},json=payload,timeout=(5,timeout))
    except requests.RequestException:
        if tool:
            from ai_grounding import cool_tool
            cool_tool(tool,model,30)
        else:cool_model(model,30)
        raise ProviderUnavailable('External API: Google transport failure',provider_cooldown=False)
    try:body=r.json()
    except ValueError:body={}
    if not r.ok:
        delay=retry_delay(r,body)
        kind=refusal_kind(r,body)
        if tool:
            from ai_grounding import cool_tool
            delay=max(delay,86400) if r.status_code in (400,403,404) else delay
            violations=[v for d in ((body.get('error') or {}).get('details') or []) for v in d.get('violations',[])]
            shared=any(any(word in str(v.get('quotaId','')).lower() for word in ('grounding','search','maps')) for v in violations)
            cool_tool(tool,model,delay,shared)
        else:
            from photo_story import VISION_MODELS
            if model in VISION_MODELS and r.status_code in (400,403,404):delay=max(delay,86400)
            cool_model(model,delay,kind)
        # Do not expose upstream messages: they can echo credentials or private prompts.
        raise ProviderUnavailable('External API: Google HTTP '+str(r.status_code),delay,False,kind)
    return body


def call_google(task, timeout, model, tool=None):
    from ai_runtime import reserve,settle,model_limits,budget_wait,budget_kind,cool_model
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key:raise ProviderUnavailable('External API: Google key unavailable',provider_cooldown=False)
    wait=budget_wait(model)
    if wait:raise ProviderUnavailable('External API: model budget occupied',wait,False,budget_kind(model))
    ceiling=min(24000,model_limits(model)['TPM']-64)
    prompt=prepare_prompt(task,limit=ceiling)
    system=task.get('system_instruction') or ''
    for attempt in range(5):
        contents=[{'role':'user','parts':[{'text':prompt}]+task.get('_image_parts',[])}]
        payload={'contents':contents,'generationConfig':{'temperature':0,'maxOutputTokens':4096 if task.get('task_type') in ('photo_story','photo_story_merge') else 1536}}
        if task.get('task_type') in ('photo_story','imagegen_prepare'):
            payload['generationConfig']['thinkingConfig']={'thinkingLevel':'low' if model in ('gemini-3.8-flash','gemini-3.7-flash') else 'minimal'}
        if tool:
            payload['tools']=[{'googleMaps' if tool=='maps' else 'googleSearch':{}}]
            coordinates=task.get('payload',{}).get('plan',{}).get('coordinates')
            if tool=='maps' and coordinates:
                payload['toolConfig']={'retrievalConfig':{'latLng':coordinates,'languageCode':'en'}}
            payload['generationConfig']['maxOutputTokens']=3072
            if model.startswith('gemini-2.5-') or model=='gemini-3.1-flash-lite':payload['generationConfig']['thinkingConfig']={'thinkingBudget':0}
            elif model.startswith('gemma-'):payload['generationConfig']['thinkingConfig']={'thinkingLevel':'high' if model=='gemma-4-26b-a4b-it' else 'minimal'}
        if system:
            payload['systemInstruction']={'parts':[{'text':system}]}
        count_payload={'generateContentRequest':{'model':'models/'+model,**payload}} if system or tool else {'contents':contents}
        counted=google_request(model,'countTokens',count_payload,key,min(timeout,15),tool=tool)
        try:count=int(counted['totalTokens'])+64
        except (KeyError,TypeError,ValueError):raise ProviderUnavailable('External API: missing token count',provider_cooldown=False)
        if count<=model_limits(model)['TPM']:break
        # Rebuild from original context, preserving complete rows and mandatory instructions.
        ceiling=max(1,int(ceiling*(model_limits(model)['TPM']-64)/count*0.9))
        prompt=prepare_prompt(task,limit=ceiling)
    else:raise PromptTooLarge('Обязательная часть не помещается в минутный бюджет модели')
    completion_limit=payload['generationConfig']['maxOutputTokens']
    reservation=reserve(model,count,completion_limit,tool=tool)
    if reservation is None:
        from ai_grounding import tool_wait
        delay=max(budget_wait(model,count,completion_limit),tool_wait(tool,model) if tool else 0,1)
        raise ProviderUnavailable('External API: model budget occupied',delay,False,budget_kind(model,count,completion_limit))
    try:
        body=google_request(model,'generateContent',payload,key,timeout,tool=tool)
    except ProviderUnavailable:
        # Unknown transport outcomes keep the conservative reservation.
        raise
    usage=body.get('usageMetadata') or {}
    normalized={'prompt_tokens':int(usage.get('promptTokenCount',count)),
                'completion_tokens':int(usage.get('candidatesTokenCount',0))+int(usage.get('thoughtsTokenCount',0)),
                'prompt_tokens_details':{'cached_tokens':int(usage.get('cachedContentTokenCount',0))}}
    settle(reservation,normalized)
    candidates=body.get('candidates') or []
    if not candidates:
        if tool:
            from ai_grounding import cool_tool
            cool_tool(tool,model,300)
        else:cool_model(model,30)
        raise ProviderUnavailable('External API: Google returned no candidate',provider_cooldown=False)
    candidate=candidates[0]
    if tool:
        from ai_grounding import normalize,settle_tool
        result=normalize(candidate,tool,model,normalized)
        metadata=candidate.get('groundingMetadata') or {}
        settle_tool(reservation,bool(metadata.get('groundingChunks') or metadata.get('webSearchQueries') or metadata.get('searchEntryPoint')))
        if candidate.get('finishReason')!='STOP' or result['status']!='grounded':
            from ai_grounding import cool_tool
            cool_tool(tool,model,300)
            raise ProviderUnavailable('Grounding: no complete sourced response',300,False)
        return json.dumps(result,ensure_ascii=False),{'provider':'google','model':model,'usage':normalized,'tool':tool}
    if candidate.get('finishReason') in ('MAX_TOKENS','SAFETY','RECITATION','BLOCKLIST','PROHIBITED_CONTENT'):
        cool_model(model,30)
        raise ProviderUnavailable('External API: incomplete or blocked Google result',provider_cooldown=False)
    output=''.join(part.get('text','') for part in candidate.get('content',{}).get('parts',[]) if not part.get('thought')).strip()
    if not output:
        cool_model(model,30)
        raise ProviderUnavailable('External API: Google returned no final text',provider_cooldown=False)
    return output,{'provider':'google','model':model,'usage':normalized}


def call_external(task, timeout):
    if task.get('task_type')=='photo_story' and '_image_parts' not in task:
        from photo_story import image_parts
        task=dict(task)
        task['_image_parts']=image_parts(task.get('payload',{}).get('photos',[]),task.get('chat_id'))
    if task.get('task_type')=='grounding_notice':return task['prompt'],{'provider':'backend','model':'location-clarification'}
    if task.get('task_type') in ('web_grounding','maps_grounding'):
        from ai_grounding import call
        return call(task,timeout)
    from ai_runtime import model_ready,connect,stamp
    from contextlib import closing
    waits=[];kinds=[]
    model_kind='response' if task['task_type']=='imagegen_prepare' and not task.get('_image_parts') else task['task_type']
    for model in external_models(model_kind):
        if not model_ready(model):
            with closing(connect()) as conn:
                row=conn.execute('SELECT unavailable_until,reason FROM ai_provider_state WHERE provider=?',(model,)).fetchone()
            from datetime import datetime
            waits.append(max(1,int((datetime.fromisoformat(row[0])-datetime.fromisoformat(stamp())).total_seconds())+1) if row else 30)
            kinds.append(row['reason'] if row and row['reason'] in ('daily','minute','transport','permanent') else 'minute')
            continue
        try:
            return call_groq(task,timeout,model) if model.startswith('openai/') else call_google(task,timeout,model)
        except ProviderUnavailable as exc:
            waits.append(max(1,int(exc.retry_after)));kinds.append(exc.kind)
        except PromptTooLarge:waits.append(300);kinds.append("permanent")
    kind='daily' if kinds and all(k=='daily' for k in kinds) else 'minute' if 'minute' in kinds else 'transport' if 'transport' in kinds else 'permanent'
    relevant=[w for w,k in zip(waits,kinds) if k==kind]
    raise ProviderUnavailable('External API: all allowed models unavailable or awaiting budget',min(relevant or [30]),False,kind)
