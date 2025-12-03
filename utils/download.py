import asyncio, base64, glob, hashlib, io, json, os, platform, re, requests, shutil, subprocess, time, urllib.parse

from datetime import timedelta
from pathlib import Path
from PIL import Image
from playwright._impl._errors import TimeoutError as PlaywrightTimeout
from playwright.async_api import (
    APIRequestContext,
    async_playwright,
    Browser,
    Playwright,
)
from tqdm import tqdm
from typing import Iterable
from urllib.parse import urljoin, urlparse, unquote
from websocket import create_connection

from utils.cache import load_cache, save_cache
from utils.common import current_timestamp, get_time_delta
from utils.constants import ANALYSIS_CACHE, DOC_PATH

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = str(
    f"{os.path.abspath(os.getcwd())}/.chrome_profile"
)  # dedicated profile for Carfax auth, must use absolute path
DEVTOOLS_PORT = 9223

CARFAX_PAT = re.compile(r"(carfax\.com/vehiclehistory)", re.I)
AUTOCHECK_PAT = re.compile(r"(autocheck\.web\.dealer\.com|autocheck\.aspx)", re.I)
UNAVAIL_PAT = re.compile(r"unavailable", re.I)

PROVIDERS = {
    "carfax": {
        "key": "carfax_url",
        "file": "carfax.pdf",
        "unavailable": "carfax_unavailable.txt",
        "selector": None,
        "ready": lambda t, href, ready: (
            "vehicle history report" in t and "carfax" in t and ready == "complete"
        ),
    },
    "autocheck": {
        "key": "autocheck_url",
        "file": "autocheck.pdf",
        "unavailable": "autocheck_unavailable.txt",
        "selector": "#full-report",
        "ready": lambda t, href, ready, marker=None: (ready == "complete" and marker),
    },
}

MIN_POLL_DAYS = 1


async def get_stable_html(page, retries=5, delay=0.8):
    last_hash = None
    for _ in range(retries):
        try:
            html = await page.content()
            h = hashlib.md5(html.encode()).hexdigest()
            if h == last_hash:
                return html  # DOM stopped changing
            last_hash = h
            await asyncio.sleep(delay)
        except Exception:
            await asyncio.sleep(delay)
    return await page.content()  # return last known snapshot


async def get_report_link(
    browser: Browser, url: str, wait_time: int = 5
) -> tuple[str, str]:
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        java_script_enabled=True,
        ignore_https_errors=True,
    )
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="commit", timeout=8000)

        try:
            await page.wait_for_selector(
                "a[href*='carfax'], iframe[src*='carfax'], img.carfax-snapshot-hover, "
                "a[href*='autocheck'], iframe[src*='autocheck'], img[alt*='autocheck']",
                timeout=3000,
            )
        except Exception:
            pass  # If not found yet, continue into the normal polling loop

    except PlaywrightTimeout:
        await context.close()
        return "NAV_TIMEOUT", "Unavailable"

    # Wait for dynamic content (poll for carfax appearances)
    start_url = url
    start = time.time()
    while time.time() - start < wait_time:
        # quickly detect navigations/soft-redirects
        current = page.url
        if current.rstrip("/") != start_url.rstrip("/"):
            await context.close()
            return "REMOVED_OR_SOLD", "Unavailable"

        # Some sites have a badge that requires a hover event to populate the carfax snapshot
        try:
            badge = await page.query_selector(
                "img.carfax-snapshot-hover, img[alt*='carfax i'], img[alt*='Show me carfax']"
            )
            if badge:
                await badge.hover()
                await asyncio.sleep(1)  # allow iframe/link to load
        except Exception as e:
            continue

        try:
            html = await get_stable_html(page)
        except Exception:
            # Last ditch effort
            await asyncio.sleep(0.5)
            try:
                html = await page.content()  # fallback
            except Exception:
                continue

        hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
        for href in hrefs:
            if CARFAX_PAT.search(href):
                await context.close()
                return href, "carfax_url"
            if AUTOCHECK_PAT.search(href):
                await context.close()
                return extract_autocheck_url(url, href), "autocheck_url"
        await asyncio.sleep(0.5)

    # assume there is no report
    await context.close()
    return "NO_REPORT", "Unavailable"


def normalize_history_url(listing_url: str, href: str) -> str:
    """
    Resolve and clean a relative AutoCheck or Carfax link.
    Example:
      listing_url = "https://www.drivedirectcars.com/used-Columbus-2020-Subaru-Outback..."
      href = "autocheck.aspx?sv=...&ac=..."
    → "https://www.drivedirectcars.com/autocheck.aspx?sv=...&ac=..."
    """
    if not href:
        return ""

    href = unquote(href)  # just in case it's HTML-encoded
    full = urljoin(listing_url, href)
    return full


def extract_autocheck_url(url: str, href: str) -> str:
    """
    Extracts and decodes the actual AutoCheck URL from a dealer iframe wrapper.
    Example input:
    /iframe.htm?src=https%3A%2F%2Fautocheck.web.dealer.com%2F%3Fdata%3DU2FsdGVkX18...
    Returns:
    https://autocheck.web.dealer.com/?data=U2FsdGVkX18...
    """
    if not href:
        return ""

    # Find the 'src=' parameter value
    match = re.search(r"src=([^&]+)", href)
    if not match:
        match = re.search(r"aspx", href)
        if not match:
            return ""
        return normalize_history_url(url, href)

    encoded_src = match.group(1)
    decoded_src = urllib.parse.unquote(encoded_src)
    return decoded_src


async def worker(semaphore: asyncio.Semaphore, browser: Browser, listing: dict):
    async with semaphore:
        url = listing["listing_url"]

        carfax_url = listing["additional_docs"]["carfax_url"]
        autocheck_url = listing["additional_docs"]["autocheck_url"]

        # Skip cars already processed or new
        if (
            listing["condition"] == "New"
            or carfax_url != "Unavailable"
            or autocheck_url != "Unavailable"
        ):
            return

        link, source = await get_report_link(browser, url)
        if link in ["NAV_TIMEOUT"]:  # , "NO_REPORT"
            # Try one last time, just in case there was a timeout or something unexpected happened
            link, source = await get_report_link(browser, url)

        if source != "Unavailable":
            listing["additional_docs"][source] = link


async def get_missing_urls(listings: list[dict], p: Playwright) -> None:
    semaphore = asyncio.Semaphore(5)  # <-- Max 5 listings in parallel

    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--ignore-https-errors",
            "--disable-http2",
        ],
    )

    tasks = [worker(semaphore, browser, l) for l in listings]
    for f in tqdm(
        asyncio.as_completed(tasks),
        total=len(tasks),
        desc="Searching for report links",
        unit="link",
    ):
        await f

    await browser.close()


def save_listing_json(listing: dict, folder: str) -> str:
    path = os.path.join(folder, "listing.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(listing, f, indent=2, ensure_ascii=False)
    return path


async def download_images(req: APIRequestContext, listing: dict, folder: str) -> int:
    imgs: list[str] = listing.get("images") or []
    if not imgs:
        return 0

    img_dir = os.path.join(folder, "images")
    os.makedirs(img_dir, exist_ok=True)

    count = 0
    for idx, url in enumerate(imgs, start=1):
        # temporary name before detecting extension
        tmp_path = os.path.join(img_dir, f"{idx}")

        resp = await req.get(url)
        if not resp.ok:
            continue

        # read raw bytes
        data = await resp.body()

        # Detect real format from bytes
        try:
            img = Image.open(io.BytesIO(data))
            fmt = (img.format or "").lower()
        except Exception:
            # fallback if Pillow fails
            fmt = "jpg"

        ext = {
            "jpeg": ".jpg",
            "jpg": ".jpg",
            "png": ".png",
            "webp": ".webp",
            "gif": ".gif",
        }.get(fmt, ".jpg")

        final_path = tmp_path + ext

        # avoid re-download if exists
        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            continue

        # write bytes exactly as received
        with open(final_path, "wb") as f:
            f.write(data)

        count += 1

    return count


async def download_sticker(req: APIRequestContext, listing: dict, folder: str) -> bool:
    url = listing.get("additional_docs", {}).get("window_sticker_url")
    if not url or url == "Unavailable":
        return False
    path = os.path.join(folder, "sticker.pdf")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True
    resp = await req.get(url)
    if not resp.ok:
        return False
    with open(path, "wb") as f:
        f.write(await resp.body())
    return True


def _to_https(url: str) -> str:
    return (
        url.replace("http://", "https://", 1)
        if url.lower().startswith("http://")
        else url
    )


def _bootstrap_profile(user_data_dir: str):
    p = Path(user_data_dir)
    p.mkdir(parents=True, exist_ok=True)
    # mark "First Run" and welcome as completed (quiet startup)
    try:
        (p / "First Run").write_text("", encoding="utf-8")
    except Exception:
        pass
    local_state = p / "Local State"
    try:
        state = {}
        if local_state.exists():
            try:
                state = json.loads(local_state.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.setdefault("browser", {})["has_seen_welcome_page"] = True
        local_state.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _launch_chrome(port: int, user_data_dir: str):
    args = [
        CHROME_EXE,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-features=SigninIntercept,SignInProfileCreation,AccountConsistency,ChromeWhatsNewUI",
        "about:blank",
    ]
    return subprocess.Popen(args)


def _browser_ws_url(port: int) -> str:
    info = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=5).json()
    return info["webSocketDebuggerUrl"]


def _browser_connect(ws_url: str):
    u = urlparse(ws_url)
    origin = f"http://{u.hostname}:{u.port or 80}"
    return create_connection(ws_url, timeout=20, origin=origin)


def _cdp(ws, _id: int, method: str, params=None, sid: str | None = None):
    msg = {"id": _id, "method": method, "params": params or {}}
    if sid:
        msg["sessionId"] = sid
    ws.send(json.dumps(msg))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id:
            return m


def _create_target(ws, url: str) -> str:
    r = _cdp(
        ws,
        1,
        "Target.createTarget",
        {"url": url, "newWindow": False, "background": False},
    )
    return r["result"]["targetId"]


def _attach(ws, target_id: str) -> str:
    r = _cdp(ws, 2, "Target.attachToTarget", {"targetId": target_id, "flatten": True})
    return r["result"]["sessionId"]


def _close_target(ws, target_id: str):
    try:
        _cdp(ws, 3, "Target.closeTarget", {"targetId": target_id})
    except Exception:
        pass


def _eval(ws, sid: str, expr: str, args: list | None = None):
    # If no args, keep the simple evaluate path
    if not args:
        r = _cdp(
            ws,
            100,
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
            sid,
        )
        return r["result"]["result"].get("value")

    # With args: call a function in the page context
    # 1) Get a handle to the global object
    root = _cdp(
        ws,
        101,
        "Runtime.evaluate",
        {"expression": "window", "returnByValue": False},
        sid,
    )
    obj_id = root["result"]["result"]["objectId"]

    # 2) Normalize function declaration
    fn_src = expr.strip()
    # Allow either "(selector) => {...}" *or* "function(selector){...}"
    if not (fn_src.startswith("(") or fn_src.startswith("function")):
        # If someone passed a body/expression, wrap it
        fn_src = f"(function(){{ return ({fn_src}); }})"

    # 3) Call it with arguments
    call = _cdp(
        ws,
        102,
        "Runtime.callFunctionOn",
        {
            "objectId": obj_id,
            "functionDeclaration": fn_src,
            "arguments": [{"value": a} for a in args],
            "returnByValue": True,
            "awaitPromise": True,
        },
        sid,
    )
    return call["result"]["result"].get("value")


def _set_media(ws, sid: str, media: str = "screen"):
    _cdp(ws, 150, "Emulation.setEmulatedMedia", {"media": media}, sid)


def _wait_until_carfax_ready(ws, sid: str, timeout=90):
    _cdp(ws, 10, "Page.enable", sid=sid)
    _cdp(ws, 11, "Runtime.enable", sid=sid)
    end = time.time() + timeout
    while time.time() < end:
        info = _eval(
            ws,
            sid,
            "({t: document.title, href: location.href, ready: document.readyState})",
        )
        t = (info.get("t") or "").lower()
        href = (info.get("href") or "").lower()
        ready = (info.get("ready") or "").lower()
        if "access blocked" in t or "/record-check" in href:
            raise RuntimeError("access blocked")
        if "vehicle history report" in t and "carfax" in t and ready == "complete":
            return
        time.sleep(0.5)
    raise TimeoutError("report not ready")


def _wait_until_selector_ready(ws, sid: str, provider: str, timeout=90):
    _cdp(ws, 10, "Page.enable", sid=sid)
    _cdp(ws, 11, "Runtime.enable", sid=sid)
    end = time.time() + timeout
    meta = PROVIDERS[provider]
    selector = meta.get("selector")
    while time.time() < end:
        # Evaluate both DOM readyState and provider-specific marker
        script = """
			(selector) => {
				return {
					ready: document.readyState,
					href: location.href,
					title: document.title,
					marker: selector ? document.querySelector(selector) !== null : true
				};
			}
		"""
        info = _eval(ws, sid, script, args=[selector])
        t = (info.get("title") or "").lower()
        href = (info.get("href") or "").lower()
        ready = (info.get("ready") or "").lower()
        has_marker = info.get("marker")

        # Detect blocks / paywalls
        if "access blocked" in t or "buy full report" in t or "/record-check" in href:
            raise RuntimeError("access blocked or paywall")

        # Ready when DOM is loaded and marker is present
        if ready == "complete" and has_marker:
            return
        time.sleep(0.5)

    raise TimeoutError(f"{provider} report not ready after {timeout}s")


def _print_to_pdf(ws, sid: str, out_path: Path):
    params = {
        "printBackground": True,
        "landscape": False,
        "scale": 1.0,
        "paperWidth": 8.5,
        "paperHeight": 11.0,
        "marginTop": 0.25,
        "marginBottom": 0.25,
        "marginLeft": 0.25,
        "marginRight": 0.25,
        "preferCSSPageSize": False,
        "displayHeaderFooter": False,
    }
    r = _cdp(ws, 200, "Page.printToPDF", params, sid)
    data_b64 = r["result"]["data"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data_b64))


def _collect_report_jobs(listings: Iterable[dict]) -> list[tuple[str, str, Path]]:
    jobs = []
    for lst in listings:
        title, vin = lst.get("title"), lst.get("vin")
        if not title or not vin:
            continue
        doc: dict = lst.get("additional_docs") or {}
        for provider, meta in PROVIDERS.items():
            url: str = doc.get(meta["key"], "")
            if not url or url == "Unavailable":
                continue
            folder = os.path.join(DOC_PATH, title, vin)
            out_path: Path = Path(folder) / meta["file"]

            if out_path.exists() and out_path.stat().st_size > 0:
                continue

            jobs.append((provider, url, out_path))
    return jobs


def _is_chrome_installed():
    # First check PATH names
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "chrome.exe",
        "chromium",
        "chromium-browser",
    ]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path

    # OS-specific checks
    system = platform.system()
    if system == "Darwin":  # macOS
        path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(path):
            return path
    elif system == "Windows":
        # common install locations
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "PROGRAMFILES(X86)", r"C:\Program Files (x86)"
        )
        paths = [
            os.path.join(program_files, "Google/Chrome/Application/chrome.exe"),
            os.path.join(program_files_x86, "Google/Chrome/Application/chrome.exe"),
        ]
        for path in paths:
            if os.path.exists(path):
                return path

    return None


def download_report_pdfs(listings: Iterable[dict]) -> None:
    if not _is_chrome_installed():
        print("Chrome not installed, cannot save documents")
        return

    jobs = _collect_report_jobs(listings)
    if not jobs:
        print("No documents to save")
        return
    Path(DOC_PATH).mkdir(parents=True, exist_ok=True)
    _bootstrap_profile(USER_DATA_DIR)
    proc = _launch_chrome(DEVTOOLS_PORT, USER_DATA_DIR)
    time.sleep(2.0)
    ws = None
    try:
        ws = _browser_connect(_browser_ws_url(DEVTOOLS_PORT))
        for provider, raw_url, out_path in tqdm(
            jobs,
            total=len(jobs),
            desc="Downloading reports",
            unit="listing",
        ):
            if out_path.exists() and out_path.stat().st_size > 0:
                continue
            url = _to_https(raw_url)
            target_id = ""
            try:
                target_id = _create_target(ws, url)
                sid = _attach(ws, target_id)
                try:
                    if provider == "carfax":
                        _wait_until_carfax_ready(ws, sid, timeout=60)
                        _set_media(ws, sid, "screen")  # guard against print CSS hiding
                    # else:
                    #     _wait_until_selector_ready(ws, sid, provider, timeout=60)
                except RuntimeError as e:
                    if "access blocked" in str(e).lower():
                        _cdp(ws, 12, "Page.reload", sid=sid)
                        # tiny pause so the reload actually kicks in
                        time.sleep(0.5)
                        if provider == "carfax":
                            _wait_until_carfax_ready(ws, sid, timeout=60)
                            _set_media(ws, sid, "screen")
                        # else:
                        #     _wait_until_selector_ready(ws, sid, provider, timeout=60)
                    else:
                        raise
                _print_to_pdf(ws, sid, out_path)

                # Only save HTML if the PDF actually exists and isn't empty
                if out_path.exists() and out_path.stat().st_size > 0:
                    html_path = out_path.with_suffix(".html")
                    html_source = _eval(ws, sid, "document.documentElement.outerHTML")
                    html_path.write_text(html_source, encoding="utf-8")

                    # Clean up old files
                    for f in out_path.parent.glob("*"):
                        if f.is_file() and UNAVAIL_PAT.search(f.name):
                            f.unlink()
            except Exception:
                try:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    unavail = PROVIDERS[provider]["unavailable"]
                    (out_path.parent / unavail).write_text(
                        "Payment wall or access blocked", encoding="utf-8"
                    )
                except Exception:
                    pass
            finally:
                if target_id:
                    _close_target(ws, target_id)
    finally:
        try:
            if ws:
                ws.close()
        finally:
            try:
                proc.terminate()
            except Exception:
                pass


def needs_poll(l: dict, cache: dict) -> bool:
    vin = l.get("vin")
    if not vin:
        return False

    docs = l.get("additional_docs", {})
    current = docs.get("carfax_url")

    cached_entry = cache.get(vin, {})
    cached_url = cached_entry.get("carfax_url")
    last_poll = cached_entry.get("last_poll")

    # 1: Rate limiting — skip if recently polled
    if last_poll:
        delta = get_time_delta(current_timestamp(), last_poll)
        if delta < timedelta(MIN_POLL_DAYS):
            return False

    # 2: If URL is missing/unavailable → poll
    if current == "Unavailable":
        return True

    # 3: If URL exists but changed → poll again
    if cached_url and current != cached_url:
        return True

    # 4: No cached record → poll to establish baseline
    if not cached_entry:
        return True

    return False


def unresolved(listings: list[dict], cache: dict) -> list[dict]:
    return [l for l in listings if needs_poll(l, cache)]


async def download_files(
    listings: list[dict], filename: str, include_reports: bool = True
) -> None:
    """
    Saves listing.json, downloads window stickers, and (optionally) Carfax/AutoCheck reports.
    Matches output structure: output/{title}/{vin}/...
    """
    # Lookup cache first to avoid extra queries
    analysis_cache = load_cache(ANALYSIS_CACHE)
    for l in listings:
        vin = l.get("vin")
        url = l.get("additional_docs", {}).get("carfax_url")
        if vin and url == "Unavailable":
            cached = analysis_cache.get(vin, {}).get("carfax_url")
            if cached:
                l.setdefault("additional_docs", {})["carfax_url"] = cached

    async with async_playwright() as p:
        if include_reports:
            missing = unresolved(listings, analysis_cache)

            if missing:
                print(f"Searching for missing report links ({len(missing)} listings)")
                await get_missing_urls(missing, p)

            # Retry misses on a new batch (covers timeouts)
            retry = unresolved(listings, analysis_cache)
            if retry:
                print(f"Retrying for ({len(retry)} listings)")
                await get_missing_urls(retry, p)

            leftover = unresolved(listings, analysis_cache)
            recovered = len(missing) - len(leftover)

            print(f'Recovered {recovered} url{"" if recovered == 1 else "s"}')

            # Save the updated listings back to the file
            # Must do a read first so we don't overwrite the metadata
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["listings"] = listings  # update only this section

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            timestamp = current_timestamp()
            # Save analysis cache
            for l in listings:
                vin = l.get("vin")
                if vin is None:
                    continue

                analysis_cache.setdefault(vin, {})["last_poll"] = timestamp

                docs = l.get("additional_docs")
                if docs:
                    url = docs.get("carfax_url")
                    if url and url != "Unavailable":
                        analysis_cache.setdefault(vin, {})["carfax_url"] = url

            save_cache(analysis_cache, ANALYSIS_CACHE)

        req = await p.request.new_context()
        try:
            sticker_count = 0
            for l in tqdm(
                listings,
                total=len(listings),
                desc="Saving listing info",
                unit="listing",
            ):
                title = l.get("title")
                vin = l.get("vin")
                if not title or not vin:
                    continue

                folder = os.path.join(DOC_PATH, title, vin)
                os.makedirs(folder, exist_ok=True)

                save_listing_json(l, folder)
                _ = await download_images(req, l, folder)
                success = await download_sticker(req, l, folder)
                if success:
                    sticker_count += 1

            if sticker_count:
                print(f"{sticker_count} stickers saved")
        finally:
            await req.dispose()

        # Carfax pass (single Chrome via CDP, no Playwright)
        if include_reports:
            download_report_pdfs(listings)


if __name__ == "__main__":
    json_files = glob.glob(os.path.join("output/raw", "*.json"))
    latest_json_file = max(json_files, key=os.path.getmtime)
    data: dict = {}
    with open(latest_json_file, "r") as file:
        data = json.load(file)
    metadata = data.get("metadata", {})
    listings = data.get("listings", {})
    asyncio.run(download_files(listings, latest_json_file))
