"""Run on the VPS, independently of the local PC."""
import threading
from ai_worker_client import Worker, run

def main():
    fast = Worker('groq', ['type-checks', 'search-plans'])
    thread = threading.Thread(target=fast.run, daemon=True)
    thread.start()
    photos=Worker('groq',['tasks'],task_types=['photo_story','photo_story_merge'])
    photo_thread=threading.Thread(target=photos.run,daemon=True)
    photo_thread.start()
    try:
        return run('groq', ['tasks'])
    finally:
        fast.stop.set()
        photos.stop.set()
        thread.join(timeout=5)
        photo_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
