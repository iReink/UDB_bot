"""Keep provider connections alive; requests sessions are private to a thread."""
import threading
import requests

_local=threading.local()


def post(url,**kwargs):
    if not hasattr(_local,'session'):_local.session=requests.Session()
    return _local.session.post(url,**kwargs)
