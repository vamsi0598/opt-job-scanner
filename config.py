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

# ---- US-only location filter ----

US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
]
US_STATE_ABBR = [
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
]
US_MARKERS = ["united states", "usa", "u.s.a", "u.s.", "us remote", "remote - us", "remote, us"]

# Common non-US markers to explicitly exclude, since "Remote" alone is
# ambiguous and plenty of job boards list e.g. "Remote - India" or just a
# foreign city with no country. This is a denylist of the most common
# non-US countries/cities that show up on US-employer job boards — not
# exhaustive, extend it if you spot false positives getting through.
NON_US_MARKERS = [
    "india", "bangalore", "hyderabad", "pune", "chennai", "mumbai", "delhi",
    "canada", "toronto", "vancouver", "montreal",
    "united kingdom", " uk", "u.k.", "london", "manchester",
    "germany", "berlin", "munich",
    "ireland", "dublin",
    "poland", "warsaw", "krakow",
    "philippines", "manila",
    "mexico", "mexico city", "guadalajara",
    "brazil", "sao paulo",
    "singapore",
    "australia", "sydney", "melbourne",
    "netherlands", "amsterdam",
    "spain", "madrid", "barcelona",
    "france", "paris",
    "china", "shanghai", "beijing",
    "japan", "tokyo",
    "israel", "tel aviv",
    "ukraine", "kyiv",
    "romania", "bucharest",
    "portugal", "lisbon",
    "argentina", "buenos aires",
    "colombia", "bogota",
    "vietnam", "hanoi", "ho chi minh",
    "emea", "apac", "latam",
]

# Postings with no location text, or just "Remote" with no country
# indicated, are ambiguous rather than confirmed non-US. Since this is
# for an OPT/CPT job search, most bare "Remote" postings on a US
# employer's board are in fact US-based, so these pass through by
# default. Set to False to be stricter and drop anything not explicitly
# marked as US.
INCLUDE_AMBIGUOUS_LOCATIONS = True


def is_us_location(location: str) -> bool:
    """Best-effort check that a job posting's location is within the US."""
    if not location or not location.strip():
        return INCLUDE_AMBIGUOUS_LOCATIONS

    loc = location.lower().strip()

    for marker in NON_US_MARKERS:
        if marker.strip() in loc:
            return False

    for marker in US_MARKERS:
        if marker in loc:
            return True

    for name in US_STATE_NAMES:
        if name in loc:
            return True

    # Abbreviations need a boundary check ("CA" shouldn't match "Canada",
    # but that's already excluded above; still guard against matching
    # inside another word).
    tokens = re.split(r"[,\s/()-]+", loc)
    for tok in tokens:
        if tok in US_STATE_ABBR:
            return True

    if "remote" in loc:
        return INCLUDE_AMBIGUOUS_LOCATIONS

    return INCLUDE_AMBIGUOUS_LOCATIONS


# ---- "posted within 24 hours" filter ----
# Each platform reports posting recency differently, so this is three
# separate checks, not one. If a job is missing the relevant field
# entirely, it passes through rather than being silently dropped —
# better to show a job with unknown age than hide a real one.

from datetime import datetime, timezone, timedelta

RECENT_WINDOW_HOURS = 24


def _parse_iso(ts):
    if not ts:
        return None
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_recent_greenhouse(job: dict) -> bool:
    """Greenhouse gives an exact 'updated_at' timestamp."""
    dt = _parse_iso(job.get("updated_at"))
    if not dt:
        return True
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=RECENT_WINDOW_HOURS)


def is_recent_lever(job: dict) -> bool:
    """Lever gives an exact 'createdAt' epoch-millisecond timestamp."""
    ts = job.get("createdAt")
    if not ts:
        return True
    try:
        dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return True
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=RECENT_WINDOW_HOURS)


def is_recent_workday(job: dict) -> bool:
    """Workday's CXS search API only gives relative, day-granularity text
    (e.g. 'Posted Today', 'Posted 3 Days Ago') — no exact timestamp. This
    is the closest available approximation to a 24-hour window."""
    posted = (job.get("postedOn") or "").strip().lower()
    if not posted:
        return True
    return posted in ("posted today", "today")
