# OPT job scraper — DevOps / SRE

Watches the [employer list](https://deepakvutla9.github.io/employers-list/) (5,000+ H-1B
sponsors) for DevOps Engineer / Site Reliability Engineer postings and saves them, with
apply links, to `state/postings.csv`.

## How it works

Two scripts, because scraping 5,000 career pages every 30 minutes isn't realistic — most
of that time is spent figuring out *whether a company even has a scrapable API*, which
doesn't change often:

1. **`discover_ats.py`** (runs weekly) — visits every employer's careers page once and
   detects whether it's backed by Greenhouse, Lever, or Workday (the ATS platforms with a
   public JSON jobs API). Writes `state/ats_map.json`. Realistically this will match maybe
   10-20% of the 5,144 employers — most run on Workday/iCIMS/Taleo/custom systems that
   either aren't detectable this way or don't expose a clean public API. Workday support
   is best-effort (its API needs a per-tenant search payload); check `state/ats_map.json`
   after the first run and expect to prune or fix a few entries.

2. **`poll_jobs.py`** (runs every 30 min, 8am-6pm ET, weekdays) — only reads the cached
   map from step 1, hits each matched company's lightweight jobs API directly, filters for
   DevOps/SRE titles, and upserts new postings into `state/postings.csv` (deduped by apply
   link, so it doesn't rewrite everything every run).

## Deploy it (takes about 5 minutes)

1. Create a new GitHub repo and push this folder to it.
2. In the repo's **Settings → Actions → General → Workflow permissions**, select
   "Read and write permissions" (needed so the workflows can commit results back).
3. Go to the **Actions** tab, open "Discover ATS coverage", and click **Run workflow**
   once manually to build the initial `state/ats_map.json` (takes a few minutes).
4. After that, both workflows run on their own schedules — discovery weekly, polling every
   30 min during the day. Check `state/postings.csv` in the repo for results, or watch
   `state/new_this_run.json` for just what's new each run.

## Filters

- **US-only locations** — `is_us_location()` in `config.py` keeps jobs explicitly marked
  US (state names/abbreviations, "United States", "Remote - US") and bare/no-location
  postings (ambiguous, likely US on a US employer's board), and drops anything explicitly
  marked with a non-US country or city. Tune `NON_US_MARKERS` / `INCLUDE_AMBIGUOUS_LOCATIONS`
  in `config.py` if you want it stricter or looser.
- **Posted within 24 hours** — each platform reports this differently: Greenhouse and
  Lever give exact timestamps, so those are precise. Workday's public search API only
  gives relative, day-granularity text ("Posted Today", "Posted 3 Days Ago"), so for
  Workday this really means "posted today," the closest available approximation. Adjust
  `RECENT_WINDOW_HOURS` in `config.py` to change the window.

## Extending it

- Add more titles to `TITLE_PATTERN` in `config.py` (e.g. Platform Engineer, Cloud Engineer).
- Add a notification step to `poll-jobs.yml` (Slack webhook, email, etc.) that reads
  `state/new_this_run.json` and only fires when it's non-empty.
- If you want real coverage of the other ~80% of employers (custom career sites), that
  needs per-site scraping logic rather than one generic detector — worth folding into your
  existing job-aggregation-tooling adapters instead of bolting onto this repo.
