"""Only one job folder may be prepared by this app process at a time."""

from functools import wraps
from threading import Lock

_job_lock = Lock()


def one_job_at_a_time(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        if not _job_lock.acquire(blocking=False):
            raise RuntimeError(
                "Another job is being prepared. Wait for it to finish, then click Create folder."
            )
        try:
            return function(*args, **kwargs)
        finally:
            _job_lock.release()

    return guarded
