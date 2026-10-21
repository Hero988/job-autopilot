#!/usr/bin/env python3
"""jobs.py - find job openings in public job APIs and feeds, and read adverts. Python 3.8+, stdlib only. JSON out.

  python3 jobs.py search --q "customer service" [--q "customer support"].. [--where uk|europe|anywhere]
                  [--level entry|mid|senior|any] [--days 14] [--max 100] [--pages 2] [--per-company 3] [--new]
                  [--remote] [--title-only] [--full] [--keep-commission] [--json-lines]
        Ask every source at once and keep jobs whose title or description contains any --q (case-insensitive;
        --title-only: title only), that a person in --where may take (default anywhere), posted in the last
        --days (0 = any age). Deduped by URL and company+title; title matches first, then newest. Prints
        {"count","jobs":[{title,company,location,remote,url,posted,source,salary,seniority,snippet,match}],
         "errors":[..],"sources":{name:kept},"fetched":{name:raw},"dropped":{reason:n},"keyed_sources_off":[..]}
        --level        entry: Himalayas entry-level only, and drop senior/lead/manager/principal/head/director
                       titles (or feed levels) everywhere; mid: entry + mid-level, drop principal/head/director/VP titles;
                       senior: Himalayas senior and above; any (default): no filter
        --pages N      pages per query for paged sources (Himalayas 20/page, Reed 100, Adzuna 50; default 2)
        --full         print the whole description ("description") instead of a 300-character "snippet"
        --new          hide jobs whose URL or company+role is already in ./applications.csv
        --remote       keep only jobs marked remote, or unknown but saying remote/work from home
        --per-company  at most N jobs per company (default 3; 0 = no cap). Some advertisers post 40 clones.
        --keep-commission  keep adverts saying "commission only" or "self-employed sales" (dropped by default)
        --json-lines   one job per line on stdout; the summary line goes to stderr
  python3 jobs.py board ATS COMPANY [--q TEXT].. [--where ..] [--days N] [--level ..] [--full]
        A company's open jobs from its public ATS feed, same shape. COMPANY is the token in its jobs URL:
          greenhouse  job-boards.greenhouse.io/<token>   lever       jobs.lever.co/<token>
          ashby       jobs.ashbyhq.com/<token>           workable    apply.workable.com/<token>
          recruitee   <token>.recruitee.com              teamtailor  <token>.teamtailor.com
          personio    <token>.jobs.personio.de           smartrecruiters  jobs.smartrecruiters.com/<CompanyId>
  python3 jobs.py show URL [--full]        (alias: advert URL)
        Fetch one job page and print {"url","title","posting","apply_links","text"}: "posting" is the page's
        schema.org JobPosting (title, company, dates, location, salary, description) when it has one; "text" is
        the readable page text, left out when the posting has a description unless --full. Pages built by
        JavaScript with no posting, or behind a bot check, need a browser tab instead.
  python3 jobs.py sources
        Ping every source and one known board per ATS: ok, latency_s, count; says how to switch on keyed sources.

No-key sources (checked live 2026-10-21): Himalayas (seniority filter; its url is a himalayas.app page behind a bot
check: find the employer's own advert to apply), Jobicy, Workable job search, RemoteOK, We Work Remotely, Working
Nomads, Remotive (its free feed is only 10 jobs, 24 h late), Arbeitnow (mostly DE/EU). The worldwide-remote feeds
rarely allow a given country; check eligibility.
Keyed, on only when set in the environment or in ~/.config/job-autopilot/jobs.env (KEY=VALUE lines, mode 600):
REED_API_KEY (free: reed.co.uk/developers; UK; its search descriptions are cut short, so `show` the url),
ADZUNA_APP_ID + ADZUNA_APP_KEY (free: developer.adzuna.com; GB here).
Feeds ask for attribution: keep each job's url and source. LinkedIn, Indeed, Glassdoor, Totaljobs, CV-Library
forbid scraping in their terms: this script never calls them; the agent browses those by hand. Dropped: NHS Jobs
(since 2026-10-21 its CloudFront answers 403 for the whole site, API included, whatever the user agent, at least
from cloud and non-UK networks; browse it by hand), DWP Find a Job (WAF blocks scripts), Guardian Jobs RSS (empty), Remote.co, EuropeRemotely, Jobgether,
JustRemote (no feed).
Exit: 0 ok, 1 every source failed or the board/page fetch failed, 2 usage.
"""
import base64, csv, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = "job-autopilot/1.0 (+https://github.com/Hero988/job-autopilot)"
TIMEOUT = 10
UK = re.compile(r"\b(uk|u\.k\.|united kingdom|great britain|britain|gb|gbr|england|scotland|wales|northern ireland|"
                r"london|manchester|birmingham|leeds|glasgow|edinburgh|bristol|cardiff|belfast|liverpool|sheffield|"
                r"newcastle|nottingham|leicester|brighton|cambridge|oxford|reading|southampton|aberdeen|swansea)\b")
WIDE = re.compile(r"\b(worldwide|world ?wide|anywhere|global|globally|international|emea|europe|european|eu)\b")
EU = re.compile(r"\b(ireland|germany|deutschland|france|spain|portugal|italy|netherlands|nederland|belgium|"
                r"luxembourg|austria|switzerland|poland|czech|slovakia|hungary|romania|bulgaria|greece|croatia|"
                r"slovenia|serbia|denmark|sweden|norway|finland|iceland|estonia|latvia|lithuania|cyprus|malta|"
                r"ukraine|berlin|munich|hamburg|paris|madrid|barcelona|lisbon|amsterdam|dublin|vienna|warsaw|"
                r"prague|stockholm|copenhagen|z[uü]rich|cet|cest)\b")
FILLER = re.compile(r"\b(remote|remotely|fully|full|work from home|wfh|home|based|hybrid|flexible|telecommute|or|"
                    r"and|only|n/?a|none|any|location|locations|timezones?|in the world|the|world|first)\b|[^a-z]+")
REMOTE_TXT = re.compile(r"remote|work from home|home[- ]based|wfh|telecommute", re.I)
# --level: Himalayas seniority values (comma = any of), and job titles dropped for every source
LEVELS = {"entry": "Entry-level", "mid": "Entry-level,Mid-level", "senior": "Senior,Manager,Director,Executive"}
TOO_SENIOR = {"entry": re.compile(r"\b(senior|sr|snr|lead(?! gen)|leader|supervisor|principal|staff|head of|director|manager|vp|"
                                  r"vice president|chief|partner)\b", re.I),
              "mid": re.compile(r"\b(principal|head of|director|vp|vice president|chief)\b", re.I)}
COMMISSION = re.compile(r"(?<!not )(?<!non-)(?<!non )commission[- ]only|self[- ]employed sales", re.I)
KEYS_FILE = os.path.expanduser("~/.config/job-autopilot/jobs.env")
KEY_HELP = {"reed": "free key: reed.co.uk/developers", "adzuna": "free key: developer.adzuna.com"}

def load_keys(path=KEYS_FILE):
    """KEY=VALUE lines (like mail.env) for keys not already in the environment."""
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            k, eq, v = re.sub(r"^\s*export\s+", "", line.strip()).partition("=")
            k = k.strip()
            if eq and k and not k.startswith("#") and not os.environ.get(k):
                os.environ[k] = v.strip().strip("'\"")

def out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    sys.exit(code)

def err(e):
    return ("%s: %s" % (type(e).__name__, e))[:200]

def fetch(url, headers=None, kind="json"):
    h = {"User-Agent": UA, "Accept": "application/json, application/xml;q=0.9, */*;q=0.5"}
    req = urllib.request.Request(url, headers=dict(h, **(headers or {})))
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw) if kind == "json" else ET.fromstring(raw)

def qs(**kw):
    return urllib.parse.urlencode({k: v for k, v in kw.items() if v not in (None, "")})

def text(s):
    """HTML (sometimes entity-escaped twice) -> plain text."""
    s = html.unescape(html.unescape(str(s or "")))
    return re.sub(r"\s+", " ", re.sub(r"(?is)<(script|style).*?</\1>|<[^>]+>", " ", s)).strip()

def when(v):
    """epoch s/ms, ISO 8601, RFC 2822 or dd/mm/yyyy -> aware UTC datetime, else None."""
    try:
        if v in (None, "", 0):
            return None
        if isinstance(v, (int, float)) or str(v).strip().isdigit():
            x = float(v)
            return datetime.fromtimestamp(x / 1000 if x > 1e11 else x, timezone.utc)
        s = str(v).strip()
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})$", s)
        if m:
            return datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=timezone.utc)
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", s)
        if m:
            return datetime(*[int(g or 0) for g in m.groups()], tzinfo=timezone.utc)
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None

def money(lo, hi=None, cur="", per=""):
    def f(x):
        try:
            x = float(x)
        except (TypeError, ValueError):
            return ""
        return "" if x <= 0 else ("%gk" % round(x / 1000, 1) if x >= 1000 else "%g" % x)
    lo, hi = f(lo), f(hi)
    if not (lo or hi):
        return None
    rng = (lo + ("-" + hi if hi and hi != lo else "")) if lo else hi
    return " ".join(p for p in (str(cur or "").upper(), rng, "/" + str(per) if per else "") if p)

def job(title, company, location, remote, url, posted, source, salary=None, desc="", seniority=None):
    d, t = text(desc), when(posted)
    return {"title": text(title), "company": text(company), "location": text(location), "remote": remote,
            "url": url or "", "posted": t.date().isoformat() if t else None, "source": source,
            "salary": salary or None, "seniority": seniority, "snippet": d[:300], "_desc": d,
            "_t": t.timestamp() if t else 0}

def guess_remote(*parts):
    return True if REMOTE_TXT.search(" ".join(str(p or "") for p in parts)) else None

# ---------------------------------------------------------------- search sources: fn(query, args, page)
def s_himalayas(q, a, page):
    d = fetch("https://himalayas.app/jobs/api/search?" + qs(
        q=q, sort="recent", page=page, country="United Kingdom" if a["where"] == "uk" else None,
        seniority=LEVELS.get(a["level"])))
    return [job(j.get("title"), j.get("companyName"), ", ".join(j.get("locationRestrictions") or []) or "Worldwide",
                True, j.get("applicationLink") or j.get("guid"), j.get("pubDate"), "himalayas",
                money(j.get("minSalary"), j.get("maxSalary"), j.get("currency"), j.get("salaryPeriod")),
                j.get("description") or j.get("excerpt"), ", ".join(j.get("seniority") or []) or None)
            for j in d.get("jobs", [])]

def s_jobicy(q, a, page):
    w = a["where"]
    d = fetch("https://jobicy.com/api/v2/remote-jobs?" + qs(count=50, tag=q, geo=w if w != "anywhere" else ""))
    return [job(j.get("jobTitle"), j.get("companyName"), j.get("jobGeo"), True, j.get("url"), j.get("pubDate"),
                "jobicy", money(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency"), j.get("salaryPeriod")),
                j.get("jobDescription") or j.get("jobExcerpt"), j.get("jobLevel")) for j in d.get("jobs", [])]

def s_workable(q, a, page):
    """jobs.workable.com search API (its robots.txt allows /api; it disallows /search?...). --pages pages of 20."""
    p = {"query": q, "location": "United Kingdom" if a["where"] == "uk" else None,
         "workplace": "remote" if a["remote"] else None}
    rows = []
    for _ in range(a["pages"]):
        d = fetch("https://jobs.workable.com/api/v1/jobs?" + qs(**p))
        rows += d.get("jobs", [])
        p["pageToken"] = d.get("nextPageToken")
        if not p["pageToken"]:
            break
    wp = {"remote": True, "hybrid": False, "on_site": False, "onsite": False}
    return [job(j.get("title"), (j.get("company") or {}).get("title"), "; ".join(j.get("locations") or []),
                wp.get(j.get("workplace")), j.get("url"), j.get("created"), "workable", None, j.get("description"))
            for j in rows]

def s_remoteok(*_):
    return [job(j.get("position"), j.get("company"), j.get("location"), True, j.get("url"), j.get("epoch"), "remoteok",
                money(j.get("salary_min"), j.get("salary_max"), "USD", "year"), j.get("description"))
            for j in fetch("https://remoteok.com/api") if isinstance(j, dict) and j.get("position")]

def s_weworkremotely(*_):
    rows = []
    for i in fetch("https://weworkremotely.com/remote-jobs.rss", kind="xml").iter("item"):
        f = i.findtext
        company, _, title = (f("title") or "").partition(": ")
        rows.append(job(title or company, company if title else "", f("region") or f("country"), True, f("link"),
                        f("pubDate"), "weworkremotely", None, f("description")))
    return rows

def s_workingnomads(*_):
    d = fetch("https://www.workingnomads.com/api/exposed_jobs/")
    return [job(j.get("title"), j.get("company_name"), j.get("location"), True, j.get("url"), j.get("pub_date"),
                "workingnomads", None, j.get("description")) for j in d]

def s_remotive(*_):
    return [job(j.get("title"), j.get("company_name"), j.get("candidate_required_location"), True, j.get("url"),
                j.get("publication_date"), "remotive", j.get("salary"), j.get("description"))
            for j in fetch("https://remotive.com/api/remote-jobs").get("jobs", [])]

def s_arbeitnow(*_):
    return [job(j.get("title"), j.get("company_name"), j.get("location"),
                bool(j.get("remote") or REMOTE_TXT.search((j.get("title") or "") + " " + (j.get("location") or ""))),
                j.get("url"), j.get("created_at"), "arbeitnow", None, j.get("description"))
            for j in fetch("https://www.arbeitnow.com/api/job-board-api").get("data", [])]

def s_reed(q, a, page):
    """Reed API docs: basic auth, username = key, empty password. UK only. Descriptions are cut short."""
    auth = base64.b64encode((os.environ.get("REED_API_KEY", "") + ":").encode()).decode()
    d = fetch("https://www.reed.co.uk/api/1.0/search?" + qs(keywords=q, resultsToTake=100,
                                                            resultsToSkip=(page - 1) * 100 or None),
              headers={"Authorization": "Basic " + auth})
    return [job(j.get("jobTitle"), j.get("employerName"), (j.get("locationName") or "") + ", UK",
                guess_remote(j.get("jobTitle"), j.get("locationName"), j.get("jobDescription")), j.get("jobUrl"),
                j.get("date"), "reed", money(j.get("minimumSalary"), j.get("maximumSalary"), j.get("currency") or
                                             "GBP"), j.get("jobDescription")) for j in d.get("results", [])]

def s_adzuna(q, a, page):
    """Adzuna API docs: /v1/api/jobs/gb/search/<page>?app_id&app_key&what. Great Britain only here."""
    d = fetch("https://api.adzuna.com/v1/api/jobs/gb/search/%d?" % page + qs(
        app_id=os.environ.get("ADZUNA_APP_ID"), app_key=os.environ.get("ADZUNA_APP_KEY"), what=q,
        results_per_page=50, sort_by="date", **{"content-type": "application/json"}))
    return [job(j.get("title"), (j.get("company") or {}).get("display_name"),
                ((j.get("location") or {}).get("display_name") or "") + ", UK",
                guess_remote(j.get("title"), j.get("description")), j.get("redirect_url"), j.get("created"), "adzuna",
                money(j.get("salary_min"), j.get("salary_max"), "GBP"), j.get("description"))
            for j in d.get("results", [])]

# name: (fn, calls: "page" = per query and page (--pages), "query" = once per query, "once" = one call; env vars)
SOURCES = {"himalayas": (s_himalayas, "page", ()), "jobicy": (s_jobicy, "query", ()),
           "workable": (s_workable, "query", ()), "remoteok": (s_remoteok, "once", ()),
           "weworkremotely": (s_weworkremotely, "once", ()), "workingnomads": (s_workingnomads, "once", ()),
           "remotive": (s_remotive, "once", ()), "arbeitnow": (s_arbeitnow, "once", ()),
           "reed": (s_reed, "page", ("REED_API_KEY",)), "adzuna": (s_adzuna, "page", ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"))}

def keyed_off():
    return [n for n, (_, _, env) in SOURCES.items() if not all(os.environ.get(k) for k in env)]

def paged(fn, q, a):
    """Pages 1..--pages in order; stop at an empty page (the end of the results; Himalayas pages vary in size)."""
    rows = []
    for p in range(1, a["pages"] + 1):
        try:
            got = fn(q, a, p)
        except Exception:
            if p == 1:
                raise
            break  # keep the pages already fetched
        if not got:
            break
        rows += got
    return rows

# ---------------------------------------------------------------- ATS boards: fn(company, queries)
def b_greenhouse(c, _):
    d = fetch("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % c)
    return [job(j.get("title"), j.get("company_name") or c, (j.get("location") or {}).get("name"),
                guess_remote((j.get("location") or {}).get("name")), j.get("absolute_url"),
                j.get("first_published") or j.get("updated_at"), "greenhouse", None, j.get("content"))
            for j in d.get("jobs", [])]

def b_lever(c, _):
    try:
        d = fetch("https://api.lever.co/v0/postings/%s?mode=json" % c)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        d = fetch("https://api.eu.lever.co/v0/postings/%s?mode=json" % c)  # EU-hosted accounts
    rows, wp = [], {"remote": True, "hybrid": False, "onsite": False}
    for j in d:
        cat, s = j.get("categories") or {}, j.get("salaryRange") or {}
        loc = "; ".join(cat.get("allLocations") or []) or cat.get("location") or ""
        per = re.sub(r"^per-|-salary$", "", s.get("interval") or "")
        rows.append(job(j.get("text"), c, loc, wp.get(j.get("workplaceType"), guess_remote(loc)), j.get("hostedUrl"),
                        j.get("createdAt"), "lever", money(s.get("min"), s.get("max"), s.get("currency"), per),
                        j.get("descriptionPlain")))
    return rows

def b_ashby(c, _):
    rows = []
    for j in fetch("https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % c).get("jobs", []):
        locs = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        rows.append(job(j.get("title"), c, "; ".join(l for l in locs if l), j.get("isRemote"), j.get("jobUrl"),
                        j.get("publishedAt"), "ashby", (j.get("compensation") or {}).get("compensationTierSummary"),
                        j.get("descriptionPlain")))
    return rows

def b_smartrecruiters(c, queries):
    rows = []
    for q in list(queries) or [""]:
        for off in range(0, 500, 100):  # at most 500 per query
            d = fetch("https://api.smartrecruiters.com/v1/companies/%s/postings?" % c + qs(limit=100, offset=off, q=q))
            for j in d.get("content", []):
                l = j.get("location") or {}
                loc = ", ".join(x for x in (l.get("city"), l.get("region"), (l.get("country") or "").upper()) if x)
                rows.append(job(j.get("name"), (j.get("company") or {}).get("name") or c, loc, l.get("remote"),
                                "https://jobs.smartrecruiters.com/%s/%s" % (c, j.get("id")), j.get("releasedDate"),
                                "smartrecruiters", None, (j.get("function") or {}).get("label")))
            if off + 100 >= d.get("totalFound", 0):
                break
    return rows

def b_workable(c, _):
    d, rows = fetch("https://apply.workable.com/api/v1/widget/accounts/%s" % c), []
    for j in d.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x) for l in locs)
        rows.append(job(j.get("title"), d.get("name") or c, loc, True if j.get("telecommuting") else None, j.get("url"),
                        j.get("published_on") or j.get("created_at"), "workable", None, j.get("description")))
    return rows

def b_recruitee(c, _):
    rows = []
    for j in fetch("https://%s.recruitee.com/api/offers/" % c).get("offers", []):
        s = j.get("salary") or {}
        rows.append(job(j.get("title"), j.get("company_name") or c, j.get("location"),
                        True if j.get("remote") else (False if j.get("on_site") or j.get("hybrid") else None),
                        j.get("careers_url"), j.get("published_at") or j.get("created_at"), "recruitee",
                        money(s.get("min"), s.get("max"), s.get("currency"), s.get("period")), j.get("description")))
    return rows

def b_teamtailor(c, _):
    rows, ns = [], "{https://teamtailor.com/locations}"
    for i in fetch("https://%s.teamtailor.com/jobs.rss" % c, kind="xml").iter("item"):
        loc = "; ".join(x.text or "" for x in i.iter(ns + "name"))
        rows.append(job(i.findtext("title"), i.findtext("company_name") or c, loc,
                        {"fully": True, "temporary": True, "hybrid": False}.get(i.findtext("remoteStatus")),
                        i.findtext("link"), i.findtext("pubDate"), "teamtailor", None, i.findtext("description")))
    return rows

def b_personio(c, _):
    rows = []
    for p in fetch("https://%s.jobs.personio.de/xml" % c, kind="xml").iter("position"):
        loc = "; ".join(o.text for o in p.iter("office") if o.text)
        rows.append(job(p.findtext("name"), p.findtext("subcompany") or c, loc, guess_remote(loc),
                        "https://%s.jobs.personio.de/job/%s" % (c, p.findtext("id")), p.findtext("createdAt"),
                        "personio", None, " ".join(v.text or "" for v in p.iter("value"))))
    return rows

BOARDS = {"greenhouse": b_greenhouse, "lever": b_lever, "ashby": b_ashby, "smartrecruiters": b_smartrecruiters,
          "workable": b_workable, "recruitee": b_recruitee, "teamtailor": b_teamtailor, "personio": b_personio}
BOARD_SAMPLES = {"greenhouse": "gitlab", "lever": "spotify", "ashby": "linear", "smartrecruiters": "Ubisoft2",
                 "workable": "huggingface", "recruitee": "bunq", "teamtailor": "polestar", "personio": "personio"}

# ---------------------------------------------------------------- filtering
def eligible(loc, where):
    """Can someone in `where` take a job advertised for `loc`? Blank or bare 'Remote' = no restriction."""
    if where == "anywhere":
        return True
    l = (loc or "").lower().replace("new south wales", "nsw")
    if UK.search(l) or WIDE.search(l) or (where == "europe" and EU.search(l)):
        return True
    return not FILLER.sub("", l)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from track import norm_url, norm_text  # same duplicate rules as the tracker

def norm_key(company, title):
    return norm_text(company) + "|" + norm_text(title)

def tracked(path="applications.csv"):
    if not os.path.isfile(path):
        return set(), set()
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return ({norm_url(r.get("url")) for r in rows} - {""},
            {norm_key(r.get("company"), r.get("role")) for r in rows} - {"|"})

def refine(rows, a, cap=0):
    """Filter, dedupe and cap. Returns (jobs, dropped) where dropped counts level and commission-only drops."""
    words = [re.sub(r"[\s\-/]+", " ", q.lower()).strip() for q in a["q"]]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=a["days"])).timestamp() if a["days"] else 0
    urls, keys = tracked() if a["new"] else (set(), set())
    senior = TOO_SENIOR.get(a["level"])
    per, kept, dropped = {}, [], Counter()
    for j in sorted(rows, key=lambda j: -j["_t"]):
        title = re.sub(r"[\s\-/]+", " ", j["title"].lower())
        desc = "" if a["title_only"] else re.sub(r"[\s\-/]+", " ", j["_desc"].lower())
        j["match"] = "title" if any(w in title for w in words or [""]) else \
            "description" if desc and any(w in desc for w in words) else None
        remote = j["remote"] or (j["remote"] is None and REMOTE_TXT.search(j["title"] + " " + j["location"]))
        if not (j["title"] and j["url"]) or not j["match"] or (a["remote"] and not remote) \
                or not eligible(j["location"], a["where"]) or (cutoff and j["_t"] and j["_t"] < cutoff):
            continue
        u, k, c = norm_url(j["url"]), norm_key(j["company"], j["title"]), j["company"].lower()
        if u in urls or k in keys or (cap and per.get(c, 0) >= cap):
            continue
        if senior and senior.search(j["title"] + " | " + (j["seniority"] or "")):  # title or the feed's own level
            dropped["level"] += 1
            continue
        if not a["keep_commission"] and COMMISSION.search(j["title"] + " " + j["_desc"]):
            dropped["commission_only"] += 1
            continue
        urls.add(u)
        keys.add(k)
        per[c] = per.get(c, 0) + 1
        if a["full"]:
            del j["snippet"]
            j["description"] = j["_desc"]
        kept.append({x: y for x, y in j.items() if not x.startswith("_")})
    return sorted(kept, key=lambda j: j["match"] != "title"), dict(dropped)  # stable: newest first in each group

# ---------------------------------------------------------------- reading one advert page
class Page(HTMLParser):
    """Readable text, <title>, links and JSON-LD blocks of an HTML page."""
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "button", "select", "option"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "h1", "h2",
             "h3", "h4", "h5", "h6", "dd", "dt", "dl", "blockquote", "pre", "hr", "main", "form", "label"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.ld, self.title = [], [], [], ""
        self.skip, self.where, self.link = 0, None, None

    def handle_starttag(self, tag, attrs):
        at = dict(attrs)
        if tag == "script" and (at.get("type") or "").lower() == "application/ld+json":
            self.where = "ld"
            self.ld.append("")
        elif tag == "title" and not self.skip and not self.title:  # not an <svg><title>
            self.where = "title"
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n- " if tag == "li" else "\n")
        if tag == "a" and at.get("href"):
            self.link = [at["href"], ""]

    def handle_endtag(self, tag):
        if tag in ("script", "title"):
            self.where = None
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and self.link:
            self.links.append(self.link)
            self.link = None

    def handle_data(self, d):
        if self.where == "ld":
            self.ld[-1] += d
        elif self.where == "title":
            self.title += d
        elif not self.skip:
            self.parts.append(d)
            if self.link:
                self.link[1] += d

    def text(self):
        lines = (re.sub(r"[ \t\r\f\v\xa0]+", " ", l).strip() for l in "".join(self.parts).split("\n"))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in lines if l not in ("", "-")))

def job_posting(blocks):
    """The first schema.org JobPosting in the page's JSON-LD, trimmed to what an applicant needs."""
    todo = []
    for b in blocks:
        try:
            todo.append(json.loads(b))
        except ValueError:
            continue
    while todo:
        x = todo.pop(0)
        if isinstance(x, list):
            todo += x
            continue
        if not isinstance(x, dict):
            continue
        todo += x.get("@graph") or []
        kind = x.get("@type")
        if "JobPosting" not in (kind if isinstance(kind, list) else [kind]):
            continue
        name = lambda v: v.get("name") if isinstance(v, dict) else v
        def place(v):
            v = v.get("address", v) if isinstance(v, dict) else v
            if isinstance(v, dict):
                return ", ".join(str(name(v.get(k))) for k in ("streetAddress", "addressLocality", "addressRegion",
                                                               "postalCode", "addressCountry") if v.get(k))
            return v
        many = lambda v: [w for w in (v if isinstance(v, list) else [v]) if w]
        sal = x.get("baseSalary") or {}
        val = sal.get("value") if isinstance(sal, dict) else None
        if isinstance(val, dict):
            sal = money(val.get("minValue") or val.get("value"), val.get("maxValue"), sal.get("currency"),
                        str(val.get("unitText") or "").lower())
        p = {"title": x.get("title"), "company": name(x.get("hiringOrganization")),
             "posted": x.get("datePosted"), "closes": x.get("validThrough"),
             "employment_type": x.get("employmentType"), "location_type": x.get("jobLocationType"),
             "locations": [place(l) for l in many(x.get("jobLocation"))],
             "applicant_locations": [place(l) for l in many(x.get("applicantLocationRequirements"))],
             "salary": sal or None, "description": text(x.get("description"))}
        return {k: v for k, v in p.items() if v not in (None, "", [])}
    return None

def cmd_show(a):
    if len(a["pos"]) != 2 or not re.match(r"https?://", a["pos"][1]):
        usage()
    url = a["pos"][1]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; " + UA + ")",
                                               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw, final, cs = r.read(3_000_000), r.geturl(), r.headers.get_content_charset() or "utf-8"
    except Exception as e:
        hint = " (a bot check or block: open it in a browser tab)" if isinstance(e, urllib.error.HTTPError) \
            and e.code in (401, 403, 429) else ""
        out({"url": url, "error": err(e) + hint}, 1)
    try:
        page = raw.decode(cs, errors="replace")
    except LookupError:  # a charset Python does not know (e.g. "utf8mb4")
        page = raw.decode("utf-8", errors="replace")
    pg = Page()
    pg.feed(page)
    body, posting = pg.text(), job_posting(pg.ld)
    res = {"url": final, "title": re.sub(r"\s+", " ", pg.title).strip(), "posting": posting,
           "apply_links": list(dict.fromkeys(urllib.parse.urljoin(final, h) for h, t in pg.links
                                             if re.search(r"apply", t + " " + h, re.I)))[:10], "text": body}
    if posting and posting.get("description") and not a["full"]:
        del res["text"]  # the JobPosting description is the advert; --full keeps the page text too
    if re.search(r"just a moment|attention required|verify you are human", res["title"], re.I):
        res["note"] = "bot check page, not the advert: open it in a browser tab"
    elif len(body) < 500 and not posting:
        res["note"] = "little text: the page is probably built by JavaScript; open it in a browser tab"
    out(res)

# ---------------------------------------------------------------- commands
def usage():
    print(__doc__.strip())
    sys.exit(2)

def parse(argv):
    a = {"q": [], "where": "anywhere", "level": "any", "days": None, "max": 100, "pages": 2, "per_company": 3,
         "new": False, "remote": False, "title_only": False, "full": False, "keep_commission": False,
         "json_lines": False, "pos": []}
    choices = {"--where": ("uk", "europe", "anywhere"), "--level": ("entry", "mid", "senior", "any")}
    it = iter(argv)
    for t in it:
        k = t[2:].replace("-", "_")
        if t in ("--q", "--where", "--level", "--days", "--max", "--pages", "--per-company"):
            v = next(it, None)
            if v is None or (t in choices and v not in choices[t]):
                usage()
            if t == "--q":
                a["q"].append(v)
            else:
                a[k] = v if t in choices else (int(v) if v.isdigit() else usage())
        elif t in ("--new", "--remote", "--title-only", "--full", "--keep-commission", "--json-lines"):
            a[k] = True
        elif t.startswith("-"):
            usage()
        else:
            a["pos"].append(t)
    a["pages"] = max(1, a["pages"])
    return a

def enable_hint(off):
    return {n: "set %s in the environment or %s (%s)" % (" + ".join(SOURCES[n][2]), KEYS_FILE, KEY_HELP[n])
            for n in off}

def cmd_search(a):
    if not a["q"]:
        usage()
    a["days"] = 14 if a["days"] is None else a["days"]
    tasks = []
    for name, (fn, calls, env) in [x for x in SOURCES.items() if x[0] not in keyed_off()]:
        if calls == "page":
            tasks += [(name, paged, (fn, q, a)) for q in a["q"]]
        elif calls == "query":
            tasks += [(name, fn, (q, a, 1)) for q in a["q"]]
        else:
            tasks.append((name, fn, ()))
    rows, fetched, errors = [], {}, []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for name, f in [(n, ex.submit(fn, *args)) for n, fn, args in tasks]:
            try:
                got = f.result()
                rows += got
                fetched[name] = fetched.get(name, 0) + len(got)
            except Exception as e:  # one broken source never stops the run
                errors.append({"source": name, "error": err(e)})
    jobs, dropped = refine(rows, a, a["per_company"])
    jobs = jobs[:a["max"]]
    off = keyed_off()
    summary = {"count": len(jobs), "errors": errors, "sources": dict(Counter(j["source"] for j in jobs)),
               "fetched": fetched, "dropped": dropped, "keyed_sources_off": off}
    if off:
        summary["enable"] = enable_hint(off)
    code = 1 if errors and not fetched else 0
    if not a["json_lines"]:
        out(dict(summary, jobs=jobs), code)
    for j in jobs:
        print(json.dumps(j, ensure_ascii=False))
    sys.stderr.write(json.dumps(summary, ensure_ascii=False) + "\n")
    sys.exit(code)

def cmd_board(a):
    if len(a["pos"]) != 3 or a["pos"][1] not in BOARDS:
        usage()
    ats, company = a["pos"][1:]
    a["days"] = a["days"] or 0
    try:
        rows = BOARDS[ats](company, a["q"])
    except Exception as e:
        out({"ats": ats, "company": company, "error": err(e)}, 1)
    jobs, dropped = refine(rows, a)
    out({"ats": ats, "company": company, "count": len(jobs), "total": len(rows), "dropped": dropped, "jobs": jobs})

def cmd_sources(a):
    def ping(fn, *args):
        t = time.time()
        return {"ok": True, "count": len(fn(*args)), "latency_s": round(time.time() - t, 2)}

    a["pages"] = 1
    tasks = {n: (fn,) + (("customer", dict(a, where="uk"), 1) if calls != "once" else ())
             for n, (fn, calls, _) in SOURCES.items()}
    tasks.update({"board:%s:%s" % (k, c): (BOARDS[k], c, ()) for k, c in BOARD_SAMPLES.items()})
    res, off = {}, keyed_off()
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {k: ex.submit(ping, *v) for k, v in tasks.items() if k not in off}
        for k in tasks:
            if k not in futs:
                res[k] = {"ok": None, "note": "off: " + enable_hint([k])[k]}
                continue
            try:
                res[k] = futs[k].result()
            except Exception as e:
                res[k] = {"ok": False, "error": err(e)}
    out({"sources": res, "ok": sum(1 for v in res.values() if v["ok"]),
         "failed": [k for k, v in res.items() if v["ok"] is False], "keyed_sources_off": off})

def main():
    load_keys()
    cmd = {"search": cmd_search, "board": cmd_board, "sources": cmd_sources, "show": cmd_show,
           "advert": cmd_show}.get((sys.argv + [""])[1])
    if not cmd:
        usage()
    cmd(parse(sys.argv[1:]))

if __name__ == "__main__":
    main()
