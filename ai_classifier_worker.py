from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BACKEND_URL = os.getenv("AI_WORKER_BACKEND_URL", "http://94.183.184.65:8080").rstrip("/")
AI_WORKER_TOKEN = os.getenv("AI_WORKER_TOKEN", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
CLASSIFIER_MODEL = os.getenv("AI_CLASSIFIER_MODEL", "gemma4:e4b").strip()
POLL_SECONDS = float(os.getenv("AI_CLASSIFIER_POLL_SECONDS", "1"))
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("AI_CLASSIFIER_OLLAMA_TIMEOUT_SECONDS", "30"))
BACKEND_TIMEOUT_SECONDS = int(os.getenv("AI_CLASSIFIER_BACKEND_TIMEOUT_SECONDS", "15"))


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if AI_WORKER_TOKEN:
        headers["Authorization"] = f"Bearer {AI_WORKER_TOKEN}"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def get_next_task() -> dict[str, Any] | None:
    response = request_json(
        f"{BACKEND_URL}/api/ai/type-checks/next",
        timeout=BACKEND_TIMEOUT_SECONDS,
    )
    return response.get("task")


def call_ollama(model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }
    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as resp:
        response = json.loads(resp.read().decode("utf-8"))
    return str(response.get("response") or "").strip()


def post_result(task_id: int, *, output: str = "", error: str = "") -> dict[str, Any]:
    return request_json(
        f"{BACKEND_URL}/api/ai/type-checks/{task_id}/result",
        method="POST",
        payload={"output": output, "error": error},
        timeout=BACKEND_TIMEOUT_SECONDS,
    )


def process_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    model = CLASSIFIER_MODEL or str(task["model"])
    prompt = str(task["prompt"])
    log(f"type-check #{task_id}: model={model}")
    try:
        output = call_ollama(model, prompt)
    except Exception as exc:
        log(f"type-check #{task_id}: Ollama error: {exc}")
        post_result(task_id, error=str(exc))
        return

    if not output:
        log(f"type-check #{task_id}: empty Ollama response")
        post_result(task_id, error="Ollama returned empty response")
        return

    try:
        response = post_result(task_id, output=output)
    except Exception as exc:
        log(f"type-check #{task_id}: backend result POST failed: {exc}")
        return
    log(
        f"type-check #{task_id}: backend status={response.get('status')} "
        f"result={response.get('result_type')} final_task={response.get('final_task_id')}"
    )


def main() -> int:
    if not AI_WORKER_TOKEN:
        log("AI_WORKER_TOKEN is required")
        return 2

    log(
        f"classifier worker started: backend={BACKEND_URL}, ollama={OLLAMA_URL}, "
        f"model={CLASSIFIER_MODEL or '(task model)'}, poll={POLL_SECONDS}s"
    )
    while True:
        try:
            task = get_next_task()
            if task:
                process_task(task)
            else:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            log("classifier worker stopped")
            return 0
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log(f"poll error: {exc}")
            time.sleep(POLL_SECONDS)
        except Exception as exc:
            log(f"unexpected error: {exc}")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
