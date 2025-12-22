import os, re

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
