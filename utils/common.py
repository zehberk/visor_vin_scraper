import re, time

from contextlib import contextmanager
from datetime import datetime, timedelta


@contextmanager
def stopwatch(label="Elapsed"):  # pragma: no cover
    start = time.time()
    yield
    end = time.time()
    print(f"{label}: {end - start:.2f} seconds")


def make_string_url_safe(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("/", "-")  # replace fractions like 1/2
    s = s.replace("+", "_plus")  # replace plus signs with KBB's replacement
    s = re.sub(r"[^a-z0-9_]+", "-", s)
    return s.strip("-")


def current_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_time_delta(time1: str, time2: str) -> timedelta:
    dt1 = datetime.strptime(time1, "%Y%m%d_%H%M%S")
    dt2 = datetime.strptime(time2, "%Y%m%d_%H%M%S")
    return dt1 - dt2
