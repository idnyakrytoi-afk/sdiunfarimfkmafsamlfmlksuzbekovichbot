"""Thread-safe atomic JSON state shared by the dashboard and bot workers."""
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

JSON_LOCK = RLock()


def read_json(filename, default):
    with JSON_LOCK:
        try:
            with open(filename, encoding='utf-8') as stream:
                return json.load(stream)
        except FileNotFoundError:
            return default


def write_json(filename, value):
    with JSON_LOCK:
        path = Path(filename)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
