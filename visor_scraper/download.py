# tabs only; Python 3.13
import os, json, base64, time, subprocess
from pathlib import Path
from typing import Iterable
import requests
from urllib.parse import urlparse
from websocket import create_connection
from playwright.async_api import async_playwright

# --------- CONFIG (edit if needed) ----------
CHROME_EXE    = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = str(f"{os.path.abspath(os.getcwd())}/.chrome_profile")   # dedicated profile for Carfax auth, must use absolute path
DEVTOOLS_PORT = 9223                      # pick an open port

OUTPUT_ROOT   = "output"                  # matches scraper.py
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
	return url.replace("http://", "https://", 1) if url.lower().startswith("http://") else url

def _bootstrap_profile(user_data_dir: str):
	p = Path(user_data_dir); p.mkdir(parents=True, exist_ok=True)
	# mark "First Run" and welcome as completed (quiet startup)
	try: (p / "First Run").write_text("", encoding="utf-8")
	except Exception: pass
	local_state = p / "Local State"
	try:
		state = {}
		if local_state.exists():
			try: state = json.loads(local_state.read_text(encoding="utf-8"))
			except Exception: state = {}
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
	if sid: msg["sessionId"] = sid
	ws.send(json.dumps(msg))
	while True:
		m = json.loads(ws.recv())
		if m.get("id") == _id:
			return m

def _create_target(ws, url: str) -> str:
	r = _cdp(ws, 1, "Target.createTarget", {"url": url, "newWindow": False, "background": False})
	return r["result"]["targetId"]

def _attach(ws, target_id: str) -> str:
	r = _cdp(ws, 2, "Target.attachToTarget", {"targetId": target_id, "flatten": True})
	return r["result"]["sessionId"]

def _close_target(ws, target_id: str):
	try: _cdp(ws, 3, "Target.closeTarget", {"targetId": target_id})
	except Exception: pass

def _eval(ws, sid: str, expr: str):
	r = _cdp(ws, 100, "Runtime.evaluate", {"expression": expr, "returnByValue": True}, sid)
	return r["result"]["result"].get("value")

def _wait_until_report_ready(ws, sid: str, timeout=90):
	_cdp(ws, 10, "Page.enable", sid=sid)
	_cdp(ws, 11, "Runtime.enable", sid=sid)
	end = time.time() + timeout
	while time.time() < end:
		info = _eval(ws, sid, "({t: document.title, href: location.href, ready: document.readyState})")
		t = (info.get("t") or "").lower()
		href = (info.get("href") or "").lower()
		ready = (info.get("ready") or "").lower()
		if "access blocked" in t or "/record-check" in href:
			raise RuntimeError("access blocked")
		if "vehicle history report" in t and "carfax" in t and ready == "complete":
			return
		time.sleep(0.5)
	raise TimeoutError("report not ready")

def _print_to_pdf(ws, sid: str, out_path: Path):
	params = {
		"printBackground": True,
		"landscape": False,
		"scale": 1.0,
		"paperWidth": 8.5, "paperHeight": 11.0,
		"marginTop": 0.25, "marginBottom": 0.25, "marginLeft": 0.25, "marginRight": 0.25,
		"preferCSSPageSize": False,
		"displayHeaderFooter": False,
	}
	r = _cdp(ws, 200, "Page.printToPDF", params, sid)
	data_b64 = r["result"]["data"]
	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_path.write_bytes(base64.b64decode(data_b64))

def _collect_carfax_jobs(listings: Iterable[dict]):
	jobs = []
	for lst in listings:
		title = lst.get("title")
		vin = lst.get("vin")
		if not title or not vin:
			continue
		url = (lst.get("additional_docs") or {}).get("carfax_url")
		if not url or url == "Unavailable":
			continue
		folder = os.path.join(OUTPUT_ROOT, title, vin)
		out_path = Path(folder) / "carfax.pdf"
		jobs.append((url, out_path))
	return jobs

def download_carfax_pdfs(listings: Iterable[dict]) -> None:
	jobs = _collect_carfax_jobs(listings)
	if not jobs:
		return

	Path(OUTPUT_ROOT).mkdir(parents=True, exist_ok=True)
	_bootstrap_profile(USER_DATA_DIR)

	proc = _launch_chrome(DEVTOOLS_PORT, USER_DATA_DIR)
	time.sleep(2.0)  # allow Chrome to start

	ws = None
	try:
		ws = _browser_connect(_browser_ws_url(DEVTOOLS_PORT))
		for i, (raw_url, out_path) in enumerate(jobs, 1):
			if out_path.exists() and out_path.stat().st_size > 0:
				# skip already downloaded
				continue

			url = _to_https(raw_url)
			target_id = ""
			try:
				target_id = _create_target(ws, url)
				sid = _attach(ws, target_id)
				try:
					_wait_until_report_ready(ws, sid, timeout=90)
				except RuntimeError as e:
					if "access blocked" in str(e).lower():
						_cdp(ws, 12, "Page.reload", sid=sid)
						_wait_until_report_ready(ws, sid, timeout=60)
					else:
						raise
				_print_to_pdf(ws, sid, out_path)
			except Exception:
				# leave a breadcrumb so your risk logic can detect failures later
				try:
					out_path.parent.mkdir(parents=True, exist_ok=True)
					(out_path.parent / "carfax_unavailable.txt").write_text("Payment wall or access blocked", encoding="utf-8")
				except Exception:
					pass
			finally:
				if target_id:
					_close_target(ws, target_id)
	finally:
		try:
			if ws: ws.close()
		finally:
			try: proc.terminate()
			except Exception: pass

# ===== Orchestrator for all downloads =====
async def download_files(listings: list[dict], include_carfax: bool = True) -> None:
	"""
	Saves listing.json, downloads window stickers, and (optionally) Carfax PDFs.
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
	if include_carfax:
		download_carfax_pdfs(listings)
