# tabs only; Python 3.13
import base64, json, os, platform, requests, shutil, subprocess, time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from websocket import create_connection
from playwright.async_api import async_playwright

# --------- CONFIG (edit if needed) ----------
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = str(
    f"{os.path.abspath(os.getcwd())}/.chrome_profile"
)  # dedicated profile for Carfax auth, must use absolute path
DEVTOOLS_PORT = 9223  # pick an open port

OUTPUT_ROOT = "output"  # matches scraper.py
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

# -------------------------------------------


# ===== Shared I/O (moved from scraper.py) =====
def save_listing_json(listing: dict, folder: str) -> str:
    path = os.path.join(folder, "listing.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(listing, f, indent=2, ensure_ascii=False)
    return path


async def download_sticker(req, listing: dict, folder: str) -> bool:
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


# ===== Carfax PDF (CDP — single Chrome, many tabs) =====
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


def _collect_report_jobs(listings: Iterable[dict]):
    jobs = []
    for lst in listings:
        title, vin = lst.get("title"), lst.get("vin")
        if not title or not vin:
            continue
        doc = lst.get("additional_docs") or {}
        for provider, meta in PROVIDERS.items():
            url = doc.get(meta["key"])
            if not url or url == "Unavailable":
                continue
            folder = os.path.join(OUTPUT_ROOT, title, vin)
            out_path = Path(folder) / meta["file"]
            unavail = Path(folder) / meta["unavailable"]

            if (out_path.exists() and out_path.stat().st_size > 0) or unavail.exists():
                continue

            jobs.append((provider, url, out_path))
    return jobs


def download_report_pdfs(listings: Iterable[dict]) -> None:

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

    if not _is_chrome_installed():
        print("Chrome not installed, cannot save documents")
        return

    jobs = _collect_report_jobs(listings)
    if not jobs:
        print("No documents to save")
        return
    Path(OUTPUT_ROOT).mkdir(parents=True, exist_ok=True)
    _bootstrap_profile(USER_DATA_DIR)
    proc = _launch_chrome(DEVTOOLS_PORT, USER_DATA_DIR)
    time.sleep(2.0)
    ws = None
    try:
        ws = _browser_connect(_browser_ws_url(DEVTOOLS_PORT))
        for provider, raw_url, out_path in jobs:
            if out_path.exists() and out_path.stat().st_size > 0:
                continue
            url = _to_https(raw_url)
            target_id = ""
            try:
                target_id = _create_target(ws, url)
                sid = _attach(ws, target_id)
                try:
                    if provider == "carfax":
                        _wait_until_carfax_ready(ws, sid, timeout=90)
                        _set_media(ws, sid, "screen")  # guard against print CSS hiding
                    else:
                        _wait_until_selector_ready(ws, sid, provider, timeout=90)
                except RuntimeError as e:
                    if "access blocked" in str(e).lower():
                        _cdp(ws, 12, "Page.reload", sid=sid)
                        # tiny pause so the reload actually kicks in
                        time.sleep(0.5)
                        if provider == "carfax":
                            _wait_until_carfax_ready(ws, sid, timeout=60)
                            _set_media(ws, sid, "screen")
                        else:
                            _wait_until_selector_ready(ws, sid, provider, timeout=60)
                    else:
                        raise
                _print_to_pdf(ws, sid, out_path)
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


# ===== Orchestrator for all downloads =====
async def download_files(listings: list[dict], include_reports: bool = True) -> None:
    """
    Saves listing.json, downloads window stickers, and (optionally) Carfax/AutoCheck reports.
    Matches output structure: output/{title}/{vin}/...
    """
    async with async_playwright() as pw:
        req = await pw.request.new_context()
        try:
            for lst in listings:
                title = lst.get("title")
                vin = lst.get("vin")
                if not title or not vin:
                    continue

                folder = os.path.join(OUTPUT_ROOT, title, vin)
                os.makedirs(folder, exist_ok=True)

                save_listing_json(lst, folder)
                await download_sticker(req, lst, folder)
        finally:
            await req.dispose()

    # Carfax pass (single Chrome via CDP, no Playwright)
    if include_reports:
        download_report_pdfs(listings)
