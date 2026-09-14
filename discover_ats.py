"""
Run this weekly (or whenever you want to refresh coverage).

Reads employers.json (5,000+ H-1B sponsors + careers URLs), fetches each
employer's careers page once, and detects whether it's backed by Greenhouse,
Lever, or Workday. Confirmed matches are validated against that platform's
public jobs API and written to state/ats_map.json.

This is the SLOW pass (one request per employer, ~5,000 requests). Runs
concurrently with a thread pool to keep it under a few minutes. The fast
poll_jobs.py script (every 30 min) only reads the cached map from this run —
it never re-probes career pages itself.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import EMPLOYERS_JSON_URL, REQUEST_TIMEOUT, USER_AGENT, ATS_MAP_PATH

GREENHOUSE_RE = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")
WORKDAY_RE = re.compile(r"https?://([a-zA-Z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([a-zA-Z0-9_/-]+)")

HEADERS = {"User-Agent": USER_AGENT}


def fetch_employers():
    r = requests.get(EMPLOYERS_JSON_URL, timeout=REQUEST_TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    return r.json()["rows"]


def detect_platform(company_name, careers_url):
    """Fetch one employer's careers page and try to detect its ATS.
    Returns a dict describing the match, or None if nothing was detected
    (most employers — custom career sites, iCIMS, Taleo, etc. — will return
    None here; that's expected, not an error)."""
    try:
        resp = requests.get(
            careers_url, timeout=REQUEST_TIMEOUT, headers=HEADERS, allow_redirects=True
        )
        haystack = resp.url + "\n" + resp.text[:200_000]
    except requests.RequestException:
        return None

    gh = GREENHOUSE_RE.search(haystack)
    if gh:
        slug = gh.group(1)
        api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        if _validate(api, key="jobs"):
            return {"company": company_name, "platform": "greenhouse", "slug": slug,
                     "api": api, "careers_url": careers_url}

    lv = LEVER_RE.search(haystack)
    if lv:
        slug = lv.group(1)
        api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        if _validate(api, key=None):  # lever returns a bare JSON array
            return {"company": company_name, "platform": "lever", "slug": slug,
                     "api": api, "careers_url": careers_url}

    wd = WORKDAY_RE.search(haystack)
    if wd:
        tenant, dc, site_path = wd.group(1), wd.group(2), wd.group(3).strip("/")
        site = site_path.split("/")[0]
        # Workday's CXS search API is POST-based and tenant/site specific.
        # Best-effort construction — verify this per-tenant if it comes back empty.
        api = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        return {"company": company_name, "platform": "workday", "slug": f"{tenant}/{site}",
                 "api": api, "careers_url": careers_url}

    return None


def _validate(api_url, key):
    try:
        r = requests.get(api_url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        if r.status_code != 200:
            return False
        data = r.json()
        if key:
            return isinstance(data, dict) and key in data
        return isinstance(data, list)
    except (requests.RequestException, ValueError):
        return False


def main():
    employers = fetch_employers()
    print(f"Loaded {len(employers)} employers. Probing careers pages...")

    matches = []
    checked = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {
            pool.submit(detect_platform, e["n"], e["u"]): e["n"] for e in employers
        }
        for fut in as_completed(futures):
            checked += 1
            if checked % 250 == 0:
                print(f"  {checked}/{len(employers)} checked, {len(matches)} ATS matches so far")
            result = fut.result()
            if result:
                matches.append(result)

    print(f"Done. {len(matches)} employers matched to Greenhouse/Lever/Workday out of {len(employers)}.")
    with open(ATS_MAP_PATH, "w") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "matches": matches}, f, indent=2)
    print(f"Wrote {ATS_MAP_PATH}")


if __name__ == "__main__":
    main()
