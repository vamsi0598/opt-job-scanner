"""
Run this every 30 minutes (GitHub Actions cron). It only does work if the
current time in America/New_York is within the active window (8am-6pm) —
outside that it exits immediately, so it's safe for the cron trigger itself
to be a bit generous about timing/DST.

Reads state/ats_map.json (built by discover_ats.py), hits each mapped
employer's lightweight jobs API directly, filters titles matching
TITLE_PATTERN (DevOps / SRE), and upserts results into state/postings.csv.
Anything genuinely new this run also gets written to state/new_this_run.json.
"""
import csv
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from config import (
    TITLE_PATTERN, ACTIVE_START_HOUR, ACTIVE_END_HOUR, REQUEST_TIMEOUT,
    USER_AGENT, ATS_MAP_PATH, POSTINGS_CSV_PATH, NEW_POSTINGS_PATH,
    is_us_location, is_recent_greenhouse, is_recent_lever, is_recent_workday,
)

HEADERS = {"User-Agent": USER_AGENT}
FIELDS = ["company", "title", "location", "apply_link", "source", "first_seen", "last_seen"]


def in_active_window():
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:  # skip weekends — postings rarely change then
        return False
    return ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR


def fetch_greenhouse(match):
    r = requests.get(match["api"], timeout=REQUEST_TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    out = []
    for j in jobs:
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        if TITLE_PATTERN.search(title) and is_us_location(loc) and is_recent_greenhouse(j):
            out.append({
                "company": match["company"], "title": title, "location": loc,
                "apply_link": j.get("absolute_url", ""), "source": "greenhouse",
            })
    return out


def fetch_lever(match):
    r = requests.get(match["api"], timeout=REQUEST_TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    jobs = r.json()
    out = []
    for j in jobs:
        title = j.get("text", "")
        loc = (j.get("categories") or {}).get("location", "")
        if TITLE_PATTERN.search(title) and is_us_location(loc) and is_recent_lever(j):
            out.append({
                "company": match["company"], "title": title, "location": loc,
                "apply_link": j.get("hostedUrl", ""), "source": "lever",
            })
    return out


def fetch_workday(match):
    # Workday's CXS API is POST and expects a small search payload.
    payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "DevOps"}
    out = []
    for query in ("DevOps", "Site Reliability"):
        payload["searchText"] = query
        try:
            r = requests.post(match["api"], json=payload, timeout=REQUEST_TIMEOUT, headers=HEADERS)
            if r.status_code != 200:
                continue
            postings = r.json().get("jobPostings", [])
        except (requests.RequestException, ValueError):
            continue
        for j in postings:
            title = j.get("title", "")
            loc = j.get("locationsText", "")
            if TITLE_PATTERN.search(title) and is_us_location(loc) and is_recent_workday(j):
                path = j.get("externalPath", "")
                link = match["careers_url"].split("/wday", 1)[0] + path if path else match["careers_url"]
                out.append({
                    "company": match["company"], "title": title,
                    "location": loc,
                    "apply_link": link, "source": "workday",
                })
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "workday": fetch_workday}


def load_existing():
    if not os.path.exists(POSTINGS_CSV_PATH):
        return {}
    with open(POSTINGS_CSV_PATH, newline="") as f:
        return {row["apply_link"]: row for row in csv.DictReader(f)}


def main():
    if not in_active_window():
        print("Outside the 8am-6pm ET weekday window — nothing to do.")
        return

    if not os.path.exists(ATS_MAP_PATH):
        print(f"{ATS_MAP_PATH} not found — run discover_ats.py first.")
        sys.exit(1)

    with open(ATS_MAP_PATH) as f:
        matches = json.load(f)["matches"]

    existing = load_existing()
    now_str = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    new_postings = []

    for match in matches:
        fetcher = FETCHERS.get(match["platform"])
        if not fetcher:
            continue
        try:
            found = fetcher(match)
        except requests.RequestException as e:
            print(f"  skip {match['company']} ({match['platform']}): {e}")
            continue
        for job in found:
            link = job["apply_link"]
            if not link:
                continue
            if link in existing:
                existing[link]["last_seen"] = now_str
            else:
                job["first_seen"] = now_str
                job["last_seen"] = now_str
                existing[link] = job
                new_postings.append(job)

    with open(POSTINGS_CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in existing.values():
            writer.writerow({k: row.get(k, "") for k in FIELDS})

    with open(NEW_POSTINGS_PATH, "w") as f:
        json.dump(new_postings, f, indent=2)

    print(f"Checked {len(matches)} companies. {len(new_postings)} new DevOps/SRE postings this run. "
          f"{len(existing)} total tracked.")


if __name__ == "__main__":
    main()
