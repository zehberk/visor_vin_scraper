# test_script.py — Chrome DevTools (browser WS sessions) -> reliable PDF saves
import json, time, base64, subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from websocket import create_connection

# ---------- CONFIG ----------
CHROME_EXE    = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = r"C:\ChromeScraper_CDP"     # dedicated profile folder (Chrome creates it)
DEVTOOLS_PORT = 9223                        # pick an open port
OUT_DIR       = Path(r"out\carfax_reports")
URLS = [
	"https://www.carfax.com/vehiclehistory/ar20/X2W7aNATUTe40EQjMVjNHIYxvoMc5BtybkFhLvpUwGrY2s3GKowEw8Wk5hYf3Y2KRWO7SdL5Rma1C0oLaxVf7NhTNyksEq7UaRo",
	"http://www.carfax.com/VehicleHistory/p/Report.cfx?partner=DVW_1&vin=4S4BTAAC3R3152070",
	"https://www.carfax.com/vehiclehistory/ar20/psE2v6QN0he21xW-vH3t73fEfmYRaHM-6C3ha66_vIEt34zV118uPkhISlOesFF5x4hL7pYJ2QEYxvBaN5PXlLSsi6O-TuOH19JLx17w",
	"https://www.carfax.com/vehiclehistory/ar20/sSgGTjYop2gK7iT6LyzoPf07n5jQS-rwliWfQOB7j8lDfSeLyC9KDcTwzrvkgve4ojRyUDI1BthVgk54lVnDOgTT2LD0b0wUYQo",
	"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=CVN_0&vin=4S4BTADC2R3136728",
	"https://www.carfax.com/vehiclehistory/ar20/eGr_duu4wNjpIZmUA84TGVYnwuab_hVLOuZSu86qsDM-oaWEs_GLHKGrQLo5NtgQ1hBzWAyl1yflCsGhcsFfbCmB7StnGoaitSk",
	"https://www.carfax.com/vehiclehistory/ar20/lzduOrNylxIArMnJUqbPmGtpt2fNjA-d3VEUxkIw3uksTUDYAdztM9b8uqiHIdcHZtjbVAsmSzslOQiiZ508TODfXBH3z6eJk2k",
	"https://www.carfax.com/vehiclehistory/ar20/M7yl9-2YVXup8GMt1eInOqZ_HZnVqMbvMXqYWi0XaozpzMcyI8iyCPGwZ_-RK87W5yPeAR862FQHVwTcD_sTNuQXsB5a_bE-8-k",
	"https://www.carfax.com/vehiclehistory/ar20/79clmqEbo_hfN4Z06AqqwX5E0nsIDp3Kf7EPFHI-qArE1fFYW4Pp2EFBxSdcyjh5GoWazN2GtfnkD3fWRtBH_eeTptb5-At_LYk",
	"https://www.carfax.com/vehiclehistory/ar20/Mq06cIe6cJSDOv5QHeN76CImC6kKl4IWLujyp6OHnU-vyXpI5PAWdoo2npKRq-1_VKhAVVcWsPc8DxsobUbpZZ223Pt-Y_zekU4",
	"https://www.carfax.com/vehiclehistory/ar20/aiVHNs7705ZymFygE0gFk-kzksFYzQLn8N2qFfI19_5ULhwXD1NzHnmFLl8LnA88lUayKb9uhejpqBDqhIzbXIVNWsQcKfSg4cI",
	"https://www.carfax.com/vehiclehistory/ar20/gQG27VFniu_zEigKDdCFO7SThyrJahewvH3utTiNCZvhKU5tiQW-iBjQjfBZy3KddUIM_3voN5NsKJLqL3wOSmIXZfq8-dP375g",
	"https://www.carfax.com/vehiclehistory/ar20/AL9l8qh7x2iBFydccFCuXSLq9u3Es-WquietbWPhifGzNfi1HBOSUc7W-1NZ9sPVpF_vV2bg3ANnJA6YtsLwcIBf1JjGwWZj160",
	"https://www.carfax.com/vehiclehistory/ar20/4c0C5_I9-9MmkbBHgu3puyJM7FDrjt6H14LOETFIkw3ChGbWqLlNqcn36fswMG7klEvyYqkA2g2HaufOOYppNtGBeYIj8mpOMXo",
	"https://www.carfax.com/vehiclehistory/ar20/-wR1naCCiO5eN0KeGaDMcGZar5gENeMkCqEzKaKIg0ncanVgdBCuE-pbnr7Hh4aYSZVaaS3E5goHik8iamVn7sCf3XO8yMFgLIsLnzgf",
	"https://www.carfax.com/vehiclehistory/ar20/RKhLtWuW2sajhoumwFJSjp8xGBDxoE_2Bwcq4vODcDEC6NiS1VhIVCgIzipVIXRM3seMkm9_mL_Hsw4ISQ6p3tarwf70YkRttQIJQ9bZ",
	"https://www.carfax.com/vehiclehistory/ar20/joSlDRdgTTg-3IG2I3TD5qdM--biwUcZhjq4H_W4TXnzrA80z7Wzn_kFUOFCCHPOpmDI_gXFG8901wZxz5cqiOrF6-V3i_I3KNg",
	"https://www.carfax.com/vehiclehistory/ar20/2cEvDI9flr1aCy_lJgyVdYlmqegqXM5Vg35Tvyk-fIUiqSjK028PDzcmYjzyYDno_-fIHqXHQC1_nznrX0ParjQ7VHO36qXfqm4",
	"https://www.carfax.com/vehiclehistory/ar20/9IUfnaqSkJfyyA8dEn6RhVK-WswdWLvESzcXo3_YpMaT5nyX-kc_J05RyIx0JRZSgRtUbABJX8jsayT-jbstcJMxyq0Zhlgx6yg",
	"https://www.carfax.com/vehiclehistory/ar20/UWvN4nWT9IndC_JkVTKvDBGnf2_VZe_4a0tuIUezeP4KqWGvdW75f64SF51Pi1n9sYLum8N8xcOGxC0l4osi_vVXeBLTk6RIDu0",
	"https://www.carfax.com/vehiclehistory/ar20/jou9xf6uRZdWkpAn6hCIyrGb3-dHEoujRoOGNPJ3adkJRGkrO41ok16y2Rcrko36zqXUT5hbvv5ad7I2WTvpwUJiumBMhAxbYno",
	"https://www.carfax.com/vehiclehistory/ar20/yfG2gGEwNr3lp1EPFwJtGC2Ses36V6H_ctgssBkv_xAUPW6wSfR1tySyjOiURJI5bMtm7CAHuRL7Usg5ea43FqAdxxg-0IaGfYE",
	"https://www.carfax.com/vehiclehistory/ar20/XyPSoDFjVQJdYH4bKh6vysALK2oc9TYv9Ct0XvfSKnIXWVtmQUFUN26CWYiQwrxi6g6fH2a23NNMRt0BNlDP-nkTYn9ZxLrytJI",
	"https://www.carfax.com/vehiclehistory/ar20/VYNrPTEk2jZoX5iQneLBmUZOEidTDrcVGt3yEWZ5mLJWOiFSPb1j_EUreGGpGBHMNmJdBD-VlVf883mN4uEDNI52Mis7pDic0zI",
	"https://www.carfax.com/vehiclehistory/ar20/LBd1V1Mz9GybIYgr65fMI9C2cN-ASD0PaZpQBV2RYqmLB_DRewZ4mM_LtqQyg5ijhtBPuAHqZRgTfBqbG9FPA1W2icAnKWBrR6Q"
]


# ---------- UTIL ----------
def to_https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.lower().startswith("http://") else url

def bootstrap_profile(user_data_dir: str):
    """Skip first-run UI (optional, but avoids welcome/sign-in)."""
    p = Path(user_data_dir); p.mkdir(parents=True, exist_ok=True)
    try: (p / "First Run").write_text("", encoding="utf-8")
    except Exception: pass
    # Mark welcome page seen
    local_state = p / "Local State"
    try:
        state = {}
        if local_state.exists():
            try: state = json.loads(local_state.read_text(encoding="utf-8"))
            except Exception: state = {}
        state.setdefault("browser", {})["has_seen_welcome_page"] = True
        local_state.write_text(json.dumps(state), encoding="utf-8")
    except Exception: pass

# ---------- CHROME LAUNCH ----------
def launch_chrome(port: int, user_data_dir: str):
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

# ---------- BROWSER WEBSOCKET (single connection) ----------
def browser_ws_url(port: int) -> str:
    info = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=5).json()
    return info["webSocketDebuggerUrl"]

def browser_connect(ws_url: str):
    u = urlparse(ws_url)
    origin = f"http://{u.hostname}:{u.port or 80}"
    return create_connection(ws_url, timeout=20, origin=origin)

def cdp_send_recv(ws, _id: int, method: str, params=None, sid: str | None = None):
    msg = {"id": _id, "method": method, "params": params or {}}
    if sid: msg["sessionId"] = sid
    ws.send(json.dumps(msg))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id:
            return m  # {"result": {...}}

# ---------- TARGET/TAB LIFECYCLE ----------
def create_target(ws, url: str) -> str:
    r = cdp_send_recv(ws, 1, "Target.createTarget",
                      {"url": url, "newWindow": False, "background": False})
    return r["result"]["targetId"]

def attach_target(ws, target_id: str) -> str:
    r = cdp_send_recv(ws, 2, "Target.attachToTarget",
                      {"targetId": target_id, "flatten": True})
    return r["result"]["sessionId"]

def close_target(ws, target_id: str):
    try: cdp_send_recv(ws, 3, "Target.closeTarget", {"targetId": target_id})
    except Exception: pass

# ---------- PAGE HELPERS ----------
def _eval(ws, sid: str, expr: str):
    r = cdp_send_recv(ws, 100, "Runtime.evaluate",
                      {"expression": expr, "returnByValue": True}, sid)
    return r["result"]["result"].get("value")

def wait_until_report_ready(ws, sid: str, timeout=90):
    cdp_send_recv(ws, 10, "Page.enable", sid=sid)
    cdp_send_recv(ws, 11, "Runtime.enable", sid=sid)
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

def print_to_pdf(ws, sid: str, out_path: Path):
    params = {
        "printBackground": True,
        "landscape": False,
        "scale": 1.0,
        "paperWidth": 8.5, "paperHeight": 11.0,
        "marginTop": 0.25, "marginBottom": 0.25, "marginLeft": 0.25, "marginRight": 0.25,
        "preferCSSPageSize": False,
        "displayHeaderFooter": False,
    }
    r = cdp_send_recv(ws, 200, "Page.printToPDF", params, sid)
    data_b64 = r["result"]["data"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data_b64))

# ---------- MAIN ----------
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bootstrap_profile(USER_DATA_DIR)

    if not URLS:
        print("No URLs provided"); return

    proc = launch_chrome(DEVTOOLS_PORT, USER_DATA_DIR)
    time.sleep(2.0)  # let Chrome start

    bws = None
    try:
        bws = browser_connect(browser_ws_url(DEVTOOLS_PORT))

        for i, raw in enumerate(URLS, 1):
            url = to_https(raw)
            out_path = OUT_DIR / f"report{i}.pdf"
            print(f"[{i}/{len(URLS)}] {url} -> {out_path.name}")

            target_id = ""
            try:
                target_id = create_target(bws, url)
                sid = attach_target(bws, target_id)

                # wait past "Verifying device…" or other interstitials
                try:
                    wait_until_report_ready(bws, sid, timeout=90)
                except RuntimeError as e:
                    # one gentle reload if we hit an "access blocked" title
                    if "access blocked" in str(e).lower():
                        cdp_send_recv(bws, 12, "Page.reload", sid=sid)
                        wait_until_report_ready(bws, sid, timeout=60)
                    else:
                        raise

                print_to_pdf(bws, sid, out_path)
                print("  ↳ saved")

            except Exception as e:
                print(f"  ↳ skipped ({e})")
            finally:
                if target_id:
                    close_target(bws, target_id)

    finally:
        try:
            if bws: bws.close()
        finally:
            try: proc.terminate()
            except Exception: pass

if __name__ == "__main__":
    main()
