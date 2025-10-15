import re, time

from contextlib import contextmanager


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
