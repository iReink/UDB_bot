"""Process-local schema initialization, scoped to a database file identity."""
from functools import wraps
from pathlib import Path
from threading import RLock

_lock = RLock()
_ready = set()


def forget(path):
    """Explicit startup/migration can rebuild a database at the same path."""
    resolved = str(Path(path).resolve())
    with _lock:
        _ready.difference_update({key for key in _ready if key[2][0] == resolved})


def database_identity(path):
    path = Path(path).resolve()
    try:
        stat = path.stat()
        return str(path), stat.st_dev, stat.st_ino
    except FileNotFoundError:
        return str(path), None, None


def once(path_getter):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with _lock:
                if kwargs.pop('force', False):
                    forget(path_getter())
                key = (function.__module__, function.__name__, database_identity(path_getter()))
                if key in _ready:
                    return
                result = function(*args, **kwargs)
                # Creation changes identity; failed initialization is never cached.
                _ready.add((function.__module__, function.__name__, database_identity(path_getter())))
                return result
        return wrapped
    return decorate
