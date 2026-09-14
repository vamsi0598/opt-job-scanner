"""
Run this weekly (or whenever you want to refresh coverage).

Reads employers.json (5,000+ H-1B sponsors + careers URLs) and tries to map
each one to a job-board platform with a public JSON API: Greenhouse, Lever,
Workday, Ashby, SmartRecruiters, or Workable. Matches are written to
state/ats_map.json.

Two detection strategies run for every employer:

1. PASSIVE — fetch the careers page once, grep the HTML for a known
   platform's URL pattern. Misses anything that only appears after
   JavaScript runs (a lot of modern career pages), since this uses plain
   HTTP requests, not a browser.

2. SLUG GUESS — independent of what's on the page: derive a likely slug from
   the company name (e.g. "Acme Corp, Inc." -> "acme-corp" / "acmecorp") and
   try it directly against each platform's public API. Catches companies the
   passive scan misses, at the cost of a few extra requests per employer.

This is the SLOW pass (multiple requests per employer, ~5,000 employers).
Runs concurrently with a thread pool. The fast poll_jobs.py script (every 30
min) only reads the cached map from this run — it never re-probes anything.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import EMPLOYERS_JSON_URL, REQUEST_TIMEOUT, USER_AGENT, ATS_MAP_PATH

HEADERS = {"User-Agent": USER_AGENT}

# ---- Passive detection patterns ----
GREENHOUSE_RE = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")
WORKDAY_RE = re.compile(r"https?://([a-zA-Z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([a-zA-Z0-9_/-]+)")
ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)")
SMARTRECRUITERS_RE = re.compile(r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)")
WORKABLE_RE = re.compile(r"([a-zA-Z0-9-]+)\.workable\.com")


def fetch_employers():
    r = requests.get(EMPLOYERS_JSON_URL, timeout=REQUEST_TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    return r.json()["rows"]


def slug_candidates(company_name):
    """A handful of plausible slugs for a company name. Order matters —
    most-likely-first, since discover_ats stops at the first validated hit
    per platform."""
    base = re.sub(r"\b(inc|llc|ltd|corp|co|corporation|company|group|holdings)\b\.?", "",
                  company_name.lower())
    base = re.sub(r"[^a-z0-9\s-]", "", base).strip()
    words = base.split()
    candidates = []
    if words:
        candidates.append("".join(words))          # "acmecorp"
        candidates.append("-".join(words))          # "acme-corp"
        if len(words) > 1:
            candidates.append("".join(words[:2]))   # trim trailing "technologies"/"inc" noise
    seen = set()
    out = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def detect_platform(company_name, careers_url):
    """Try passive detection first (cheap — one request), then fall back to
    slug guessing (more requests, but catches JS-rendered career pages)."""
    result = _detect_passive(company_name, careers_url)
    if result:
        return result
    return _detect_by_guessing(company_name, careers_url)


def _detect_passive(company_name, careers_url):
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
            return _match(company_name, "greenhouse", slug, api, careers_url)

    lv = LEVER_RE.search(haystack)
    if lv:
        slug = lv.group(1)
        api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        if _validate(api, key=None):
            return _match(company_name, "lever", slug, api, careers_url)

    ab = ASHBY_RE.search(haystack)
    if ab:
        slug = ab.group(1)
        api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        if _validate(api, key="jobs"):
            return _match(company_name, "ashby", slug, api, careers_url)

    sr = SMARTRECRUITERS_RE.search(haystack)
    if sr:
        slug = sr.group(1)
        api = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        if _validate(api, key="content"):
            return _match(company_name, "smartrecruiters", slug, api, careers_url)

    wk = WORKABLE_RE.search(haystack)
    if wk:
        slug = wk.group(1)
        api = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        if _validate(api, key="jobs"):
            return _match(company_name, "workable", slug, api, careers_url)

    wd = WORKDAY_RE.search(haystack)
    if wd:
        tenant, dc, site_path = wd.group(1), wd.group(2), wd.group(3).strip("/")
        site = site_path.split("/")[0]
        # Workday's CXS search API is POST-based and tenant/site specific.
        # Best-effort construction — verify per-tenant if it comes back empty.
        api = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        return _match(company_name, "workday", f"{tenant}/{site}", api, careers_url)

    return None


def _detect_by_guessing(company_name, careers_url):
    """Doesn't touch the careers page at all — tries likely slugs directly
    against each platform's API. Independent of whether the page itself is
    JS-rendered."""
    for slug in slug_candidates(company_name):
        gh_api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        if _validate(gh_api, key="jobs"):
            return _match(company_name, "greenhouse", slug, gh_api, careers_url)

        lv_api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        if _validate(lv_api, key=None):
            return _match(company_name, "lever", slug, lv_api, careers_url)

        ab_api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        if _validate(ab_api, key="jobs"):
            return _match(company_name, "ashby", slug, ab_api, careers_url)

        sr_api = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        if _validate(sr_api, key="content"):
            return _match(company_name, "smartrecruiters", slug, sr_api, careers_url)

        wa_api = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        if _validate(wa_api, key="jobs"):
            return _match(company_name, "workable", slug, wa_api, careers_url)

    return None


def _match(company_name, platform, slug, api, careers_url):
    return {"company": company_name, "platform": platform, "slug": slug,
            "api": api, "careers_url": careers_url}


def _validate(api_url, key):
    try:
        r = requests.get(api_url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        if r.status_code != 200:
            return False
        data = r.json()
        if key:
            return isinstance(data, dict) and key in data and bool(data.get(key))
        return isinstance(data, list)
    except (requests.RequestException, ValueError):
        return False


def main():
    employers = fetch_employers()
    print(f"Loaded {len(employers)} employers. Probing careers pages + guessing slugs...")

    matches = []
    by_platform = {}
    checked = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {
            pool.submit(detect_platform, e["n"], e["u"]): e["n"] for e in employers
        }
        for fut in as_completed(futures):
            checked += 1
            if checked % 250 == 0:
                print(f"  {checked}/{len(employers)} checked, {len(matches)} matches so far")
            result = fut.result()
            if result:
                matches.append(result)
                by_platform[result["platform"]] = by_platform.get(result["platform"], 0) + 1

    print(f"Done. {len(matches)} employers matched out of {len(employers)}.")
    for platform, count in sorted(by_platform.items(), key=lambda x: -x[1]):
        print(f"  {platform}: {count}")

    with open(ATS_MAP_PATH, "w") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "matches": matches}, f, indent=2)
    print(f"Wrote {ATS_MAP_PATH}")


if __name__ == "__main__":
    main()
