"""Shared config for the discovery and polling scripts."""

# Source of truth for the employer list (Deepak Vutla's H-1B sponsor directory).
EMPLOYERS_JSON_URL = "https://deepakvutla9.github.io/employers-list/employers.json"

# Title match — DevOps / SRE roles. Word-boundary so "sre" doesn't match inside
# other words. Extend this if you also want Platform Engineer / Cloud Engineer etc.
import re
TITLE_PATTERN = re.compile(
    r"\b(devops|dev ops|site reliability engineer|\bsre\b|platform reliability)\b",
    re.IGNORECASE,
)

# Active polling window (local to America/New_York). The poll script no-ops
# outside this window, so the GitHub Actions cron can be a little generous
# and this is what actually enforces "8am-6pm EST" correctly across DST.
ACTIVE_START_HOUR = 8   # 8:00 AM ET
ACTIVE_END_HOUR = 18    # 6:00 PM ET (exclusive)

REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; opt-job-scraper/1.0; personal job search tool)"

ATS_MAP_PATH = "state/ats_map.json"
POSTINGS_CSV_PATH = "state/postings.csv"
NEW_POSTINGS_PATH = "state/new_this_run.json"
