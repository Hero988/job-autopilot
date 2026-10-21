#!/usr/bin/env python3
"""track.py - the application tracker (./applications.csv) and site accounts (./secrets/accounts.json).
Python 3.8+, stdlib only. Run from the campaign folder. File-locked, so parallel helpers are safe.
Add -h or --help to any command for its usage.

  python3 track.py add --company C --role R --url U [--source S] [--note N] [--status QUEUED] [--force]
        -> {"id": ...} or {"duplicate": id, ...}. Same URL = same job (tracking parameters such as utm_* are
           ignored; ?id=, ?jk=, ?gh_jid= are kept). Same company + role at another URL is reported too (trailing
           reference numbers ignored); if it really is a separate vacancy, re-run with --force.
  python3 track.py claim ID [--by NAME]       QUEUED/WAITING/FAILED -> WORKING (refused if another worker has it, < 2h)
  python3 track.py set ID STATUS [--method M] [--email E] [--confirmation TEXT] [--note TEXT] [--force]
        --note is appended with the date. WAITING needs a --note with an owner prefix (see Statuses).
        A sent row (SUBMITTED/INTERVIEW/REJECTED/OFFER) goes back to QUEUED/WAITING/WORKING only with --force.
  python3 track.py show ID                    every field of one row, full notes
  python3 track.py find TERM [TERM...] [--notes]   rows matching every term (company, role, url, exact id; + notes)
  python3 track.py next [--n 5]               oldest QUEUED jobs
  python3 track.py list [--status S] [--days N]   newest 200
  python3 track.py waiting                    WAITING rows grouped by prefix, one line each: the owner's list
  python3 track.py today                      counts for today (Europe/London, or $JOBS_TZ), and submitted list
  python3 track.py stats                      totals by status, submissions per day and by source, skip reasons

  python3 track.py acct new SITE [--email E] [--url U]   unique strong password, saved to secrets/accounts.json
        (mode 600). Default email: plus-alias of MAIL_USER, e.g. you+workday-acme@gmail.com. Prints no password.
        For an existing SITE, --email changes its address (e.g. the site rejected "+"); the password stays.
  python3 track.py acct list | acct get SITE  (never prints passwords; your browser code reads the json file)

Notes: --note is one line, at most 300 characters (longer is cut, with a warning); details belong in evidence/.
  Skip notes are "criterion: reason" (location: 3 office days, experience: needs 3y SaaS, closed).
  --source is a short board name (reed, linkedin, greenhouse, company-site, alert-email), at most 40 characters.
  list, find, next and waiting clip notes to the latest one; show ID prints them whole.

Statuses: QUEUED WORKING WAITING SUBMITTED FAILED SKIPPED INTERVIEW REJECTED OFFER
  QUEUED found and matches TARGET.md, not started   WORKING claimed by a worker (claim)
  WAITING an owner action only. The --note starts with ASK: (a fact), CHECK: (CAPTCHA, SMS or ID check),
          TEST: (owner-only assessment or recording) or APPROVE: (a reply or other approval)
  SUBMITTED proven (success page or acknowledgement) FAILED could not be completed (--note why)
  SKIPPED doesn't match (--note "criterion: reason") INTERVIEW / REJECTED / OFFER from the mailbox
"""
import csv, fcntl, json, os, re, secrets, string, sys, time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

CSV_PATH = "applications.csv"
ACCOUNTS = os.path.join("secrets", "accounts.json")
FIELDS = ["id", "found", "company", "role", "url", "source", "status", "updated", "applied", "method",
          "email", "confirmation", "notes"]
STATUSES = ["QUEUED", "WORKING", "WAITING", "SUBMITTED", "FAILED", "SKIPPED", "INTERVIEW", "REJECTED", "OFFER"]
SENT = {"SUBMITTED", "INTERVIEW", "REJECTED", "OFFER"}
OWNER_PREFIXES = ("ASK", "CHECK", "TEST", "APPROVE")  # WAITING notes start with one of these and ":"
NOTE_MAX, SOURCE_MAX, CLIP = 300, 40, 120


def out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    sys.exit(code)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(iso=None):
    tz = os.environ.get("JOBS_TZ", "Europe/London")
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if iso else datetime.now(timezone.utc)
    return (t.astimezone(ZoneInfo(tz)) if ZoneInfo else t).strftime("%Y-%m-%d")


# Query parameters that never identify a job (tracking, referral, display). Everything else is kept.
TRACKING = re.compile(r"utm_.*|_ga|_gl|gclid|gbraid|wbraid|fbclid|msclkid|mc_[ce]id|source|src|ref_?src|gh_src|lever-.*|trk|"
                      r"trackingid|refid|from|referrer|campaign|lang|language|locale|previouslocale|in_iframe|"
                      r"iis|iisn|t", re.I)


def norm_url(u):
    """Same job, same key: host + path + the query minus tracking parameters (sorted)."""
    if not u:
        return ""
    p = urlsplit(u.strip())
    path = re.sub(r"/(apply|application|applynow)/?$", "", p.path.rstrip("/"), flags=re.I)
    keep = sorted((k.lower(), v) for k, v in parse_qsl(p.query) if not TRACKING.fullmatch(k))
    return re.sub(r"^www\.", "", p.netloc.lower()) + path.lower() + ("?" + urlencode(keep) if keep else "")


# A trailing reference such as " - JR10005402", " REQ-26388", " 2026-3417" (3+ digits in the token).
ID_TAIL = re.compile(r"[\s,;|:#\u2013\u2014-]+(?:ref(?:erence)?|req|job ?(?:id|ref)|vacancy|id|no)?[\s.:#-]*([\w/-]+)\s*$")


def norm_text(s):
    s = re.sub(r"\(.*?\)|\[.*?\]|\b(ltd|limited|plc|inc|llc|uk|the|remote|home ?based|work from home|wfh)\b",
               " ", (s or "").lower())
    while True:
        m = ID_TAIL.search(s)
        if not m or sum(c.isdigit() for c in m.group(1)) < 3 or not re.search(r"[a-z]", s[:m.start()]):
            break
        s = s[:m.start()]
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def one_line(s, limit, what):
    """Collapse whitespace; cut to `limit` characters with a warning on stderr."""
    s = " ".join((s or "").replace("|", "/").split())
    if len(s) > limit:
        print(f"warning: {what} cut to {limit} characters (was {len(s)}); keep it to one short line", file=sys.stderr)
        s = s[:limit - 1].rstrip() + "\u2026"
    return s


def clean_source(s):
    """A short lower-case board name: 'Alert Email' -> 'alert-email'; cut to 40 characters with a warning."""
    s = re.sub(r"[^a-z0-9.+]+", "-", (s or "").lower()).strip("-")
    if len(s) > SOURCE_MAX:
        print(f"warning: --source cut to {SOURCE_MAX} characters; use the board or site name only", file=sys.stderr)
    return s[:SOURCE_MAX].strip("-")


def latest_note(notes):
    """The newest note segment, without its date and any [by NAME] tag."""
    last = re.sub(r"\s*\[by [^\]]+\]", "", notes or "").split(" | ")[-1]
    return re.sub(r"^\d{4}-\d\d-\d\d: ", "", last).strip()


def owner_prefix(note):
    m = re.match(r"(%s):" % "|".join(OWNER_PREFIXES), note or "", re.I)
    return m.group(1).upper() if m else None


def check_waiting(note):
    """WAITING is for owner actions only; returns the note with its prefix upper-cased."""
    p = owner_prefix(note)
    if not p:
        out({"error": "WAITING is only for something the owner must do: --note must start with ASK: (a fact), "
                      "CHECK: (CAPTCHA, SMS or ID check), TEST: (owner-only assessment or recording) or "
                      "APPROVE: (a reply or other approval). Unsure about fit is not WAITING: decide (apply or SKIPPED)."}, 2)
    return p + note[len(p):]


@contextmanager
def locked():
    """Hold an exclusive lock, yield the rows, write them back."""
    lock = open(CSV_PATH + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        rows = []
        if os.path.exists(CSV_PATH):
            with open(CSV_PATH, newline="", encoding="utf-8") as f:
                rows = [dict({k: "" for k in FIELDS}, **{k: v for k, v in r.items() if k in FIELDS})
                        for r in csv.DictReader(f)]
        box = {"rows": rows, "dirty": False}
        yield box
        if box["dirty"]:
            tmp = CSV_PATH + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(box["rows"])
            os.replace(tmp, CSV_PATH)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


ALIASES = {"--notes": "--note"}
KNOWN = {"--company", "--role", "--url", "--source", "--note", "--status", "--by", "--method", "--email",
         "--confirmation", "--n", "--days", "--force"}


def opt(a, name, default=None):
    for i, t in enumerate(a):
        if ALIASES.get(t, t) == ALIASES.get(name, name):
            if name == "--force":
                return True
            if i + 1 >= len(a) or a[i + 1].startswith("--"):
                out({"error": f"{name} needs a value"}, 2)
            return a[i + 1]
    return default


def check_flags(a):
    bad = [t for t in a if t.startswith("--") and ALIASES.get(t, t) not in KNOWN]
    if bad:
        out({"error": f"unknown option {bad[0]}; run track.py with no arguments for usage"}, 2)


def get(rows, rid):
    for r in rows:
        if r["id"] == rid:
            return r
    out({"error": f"no application {rid}"}, 1)


def clip(s, n):
    return s if len(s) <= n else s[:n - 1].rstrip() + "\u2026"


def brief(r):
    """A row for lists: the latest note only, clipped (show ID prints everything)."""
    d = {k: r[k] for k in ("id", "status", "company", "role", "url", "updated") if r.get(k)}
    if r.get("notes"):
        by = re.search(r"\[by [^\]]+\]", r["notes"])
        note = ("\u2026 " if " | " in r["notes"] else "") + clip(latest_note(r["notes"]), CLIP)
        d["notes"] = (note + " " + by.group(0) if by else note).strip()
    return d


def cmd_add(a):
    company, role, url = opt(a, "--company"), opt(a, "--role"), opt(a, "--url", "")
    if not company or not role:
        out({"error": "--company and --role are required"}, 2)
    status = opt(a, "--status", "QUEUED").upper()
    if status not in STATUSES:
        out({"error": f"status must be one of {STATUSES}"}, 2)
    note, source = one_line(opt(a, "--note", ""), NOTE_MAX, "--note"), clean_source(opt(a, "--source", ""))
    if status == "WAITING":
        note = check_waiting(note)
    nu, key = norm_url(url), (norm_text(company), norm_text(role))
    with locked() as box:
        for r in box["rows"]:
            if nu and norm_url(r["url"]) == nu:
                out({"duplicate": r["id"], "status": r["status"], "company": r["company"], "role": r["role"]})
        for r in box["rows"]:
            if (norm_text(r["company"]), norm_text(r["role"])) == key and not opt(a, "--force"):
                out({"duplicate": r["id"], "status": r["status"], "company": r["company"], "role": r["role"],
                     "url": r["url"], "hint": "same company and role; if this is a separate vacancy, add --force"})
        n = max([int(r["id"][1:]) for r in box["rows"] if re.fullmatch(r"A\d+", r["id"])] + [0]) + 1
        row = {k: "" for k in FIELDS}
        row.update(id=f"A{n}", found=now(), updated=now(), company=company, role=role, url=url,
                   source=source, status=status, notes=note)
        box["rows"].append(row)
        box["dirty"] = True
    out({"id": row["id"], "status": status})


def cmd_claim(a):
    if len(a) < 2:
        out({"error": "usage: claim ID [--by NAME]"}, 2)
    by = opt(a, "--by", "lead")
    with locked() as box:
        r = get(box["rows"], a[1])
        if r["status"] == "WORKING":
            age = time.time() - datetime.strptime(r["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            owner = re.search(r"\[by ([^\]]+)\]", r["notes"])
            if age < 7200 and (not owner or owner.group(1) != by):
                out({"error": f"{r['id']} is being worked on by {owner.group(1) if owner else 'someone'} "
                              f"({int(age // 60)} min ago)", "status": r["status"]}, 1)
        elif r["status"] not in ("QUEUED", "WAITING", "FAILED"):
            out({"error": f"{r['id']} is {r['status']}, not claimable"}, 1)
        r["status"], r["updated"] = "WORKING", now()
        rest = re.sub(r"\s*\[by [^\]]+\]", "", r["notes"])
        r["notes"] = (rest + " " if rest else "") + f"[by {by}]"
        box["dirty"] = True
    out(brief(r))


def cmd_set(a):
    if len(a) < 3 or a[2].upper() not in STATUSES:
        out({"error": f"usage: set ID STATUS ...; STATUS one of {STATUSES}"}, 2)
    status, note = a[2].upper(), one_line(opt(a, "--note", ""), NOTE_MAX, "--note")
    if status == "WAITING":
        note = check_waiting(note)
    with locked() as box:
        r = get(box["rows"], a[1])
        if r["status"] in SENT and status in ("QUEUED", "WAITING", "WORKING") and not opt(a, "--force"):
            out({"error": f"{r['id']} was sent ({r['status']}); it stays sent. A next stage is INTERVIEW, an owner "
                          f"follow-up goes in --note. Use --force only to correct a mistake."}, 1)
        r["status"], r["updated"] = status, now()
        if status != "WORKING":
            r["notes"] = re.sub(r"\s*\[by [^\]]+\]", "", r["notes"])
        if status == "SUBMITTED" and not r["applied"]:
            r["applied"] = now()
        for flag, field in (("--method", "method"), ("--email", "email"), ("--confirmation", "confirmation")):
            if opt(a, flag) is not None:
                r[field] = opt(a, flag)
        if note:
            r["notes"] = (r["notes"] + " | " if r["notes"] else "") + f"{local_date()}: {note}"
        box["dirty"] = True
    out(brief(r))


def read_rows():
    with locked() as box:
        return box["rows"]


def capped(rows, n, newest=False):
    """At most n rows (the first n, or the newest n); says on stderr how many were left out."""
    if len(rows) > n:
        print(f"note: {len(rows)} rows, {n} shown", file=sys.stderr)
    return rows[-n:] if newest else rows[:n]


def cmd_show(a):
    if len(a) < 2:
        out({"error": "usage: show ID"}, 2)
    out(get(read_rows(), a[1]))


def cmd_find(a):
    terms = [t.lower() for t in a[1:] if not t.startswith("--")]
    if not terms:
        out({"error": "usage: find TERM [TERM...] [--notes]"}, 2)
    fields = ["company", "role", "url"] + (["notes"] if any(ALIASES.get(t, t) == "--note" for t in a) else [])
    hits = [brief(r) for r in read_rows()
            if all(t == r["id"].lower() or t in " ".join(r[f] for f in fields).lower() for t in terms)]
    out(capped(hits, 50))


def cmd_next(a):
    n = int(opt(a, "--n", "5"))
    out([brief(r) for r in read_rows() if r["status"] == "QUEUED"][:n])


def cmd_list(a):
    st, days = opt(a, "--status"), opt(a, "--days")
    rows = read_rows()
    if st:
        rows = [r for r in rows if r["status"] == st.upper()]
    if days:
        cut = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = [r for r in rows if r["updated"] >= cut]
    out(capped([brief(r) for r in rows], 200, newest=True))


def cmd_waiting(a):
    """WAITING rows grouped by owner prefix, one short line each (paste into OWNER.md)."""
    old = "no prefix (old)"
    groups = {k: [] for k in OWNER_PREFIXES + (old,)}
    for r in read_rows():
        if r["status"] == "WAITING":
            note = latest_note(r["notes"])
            p = owner_prefix(note)
            text = note[len(p) + 1:].strip() if p else note
            groups[p or old].append(clip(f"{r['id']} {r['company']} - {r['role']}: {text}", 160))
    groups = {k: v for k, v in groups.items() if v}
    out(dict({"total": sum(map(len, groups.values())), "counts": {k: len(v) for k, v in groups.items()}}, **groups))


def cmd_today(a):
    rows, day = read_rows(), local_date()
    touched = [r for r in rows if r["updated"] and local_date(r["updated"]) == day]
    sent = [r for r in rows if r["applied"] and local_date(r["applied"]) == day]
    counts = {}
    for r in touched:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out({"date": day, "submitted_today": len(sent), "touched_by_status": counts,
         "queued_total": sum(r["status"] == "QUEUED" for r in rows),
         "waiting_total": sum(r["status"] == "WAITING" for r in rows),
         "submitted": [f"{r['id']} {r['company']} - {r['role']}" for r in sent]})


def skip_reason(note):
    """'location: 3 office days' -> 'location'; a bare short word ('closed') counts as itself; else 'other'."""
    note = re.sub(r"^skip(ped)?\s*[:-]\s*", "", note, flags=re.I)
    m = re.match(r"([a-z][a-z /&+-]{0,24}):", note, re.I) or re.fullmatch(r"([a-z][a-z -]{0,24})\.?", note, re.I)
    return m.group(1).strip().lower() if m else "other"


def top(counts, n=10):
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:n])


def cmd_stats(a):
    rows, by, per_day, by_source, reasons = read_rows(), {}, {}, {}, {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
        if r["applied"]:
            d = local_date(r["applied"])
            per_day[d] = per_day.get(d, 0) + 1
        if r["status"] in SENT:
            src = clip(r["source"].strip().lower(), SOURCE_MAX) or "(none)"
            by_source[src] = by_source.get(src, 0) + 1
        if r["status"] == "SKIPPED":
            k = skip_reason(latest_note(r["notes"]))
            reasons[k] = reasons.get(k, 0) + 1
    out({"total": len(rows), "by_status": by, "submitted_ever": sum(r["status"] in SENT for r in rows),
         "submitted_per_day": dict(sorted(per_day.items())[-14:]), "submitted_by_source": top(by_source),
         "skipped_by_reason": top(reasons)})


def mail_user():
    path = os.path.expanduser(os.environ.get("JOBS_MAIL_ENV", "~/.config/job-autopilot/mail.env"))
    if os.path.isfile(path):
        for line in open(path):
            k, _, v = re.sub(r"^export\s+", "", line.strip()).partition("=")
            if k.strip() == "MAIL_USER":
                return v.strip().strip("'\"")
    return ""


def slug(site):
    return re.sub(r"[^a-z0-9]+", "-", site.lower()).strip("-")


def save_accounts(data):
    tmp = f"{ACCOUNTS}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, ACCOUNTS)


def cmd_acct(a):
    sub = a[1] if len(a) > 1 else ""
    os.makedirs("secrets", mode=0o700, exist_ok=True)
    os.chmod("secrets", 0o700)
    lock = open(ACCOUNTS + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # parallel helpers must never lose a password; released on exit
    data = json.load(open(ACCOUNTS)) if os.path.exists(ACCOUNTS) else {}
    if sub == "list":
        out([{"site": s, "email": v["email"], "url": v.get("url", ""), "created": v["created"]} for s, v in data.items()])
    if sub == "get" and len(a) > 2:
        site = slug(a[2])
        v = data.get(site)
        out({"site": site, "email": v["email"], "url": v.get("url", ""), "created": v["created"],
             "password_length": len(v["password"]), "file": ACCOUNTS} if v else {"error": f"no account for {site}"},
            0 if v else 1)
    if sub == "new" and len(a) > 2:
        site, email = slug(a[2]), opt(a, "--email")
        if site in data:
            if email and email != data[site]["email"]:
                data[site]["email"] = email
                save_accounts(data)
                out({"site": site, "email": email, "updated": "email", "file": ACCOUNTS})
            out({"exists": site, "email": data[site]["email"], "file": ACCOUNTS})
        if not email:
            user = mail_user()
            if "@" not in user:
                out({"error": "no --email and no MAIL_USER in ~/.config/job-autopilot/mail.env"}, 1)
            local, domain = user.split("@", 1)
            email = f"{local}+{site[:30]}@{domain}"
        alphabet = string.ascii_letters + string.digits
        pw = "".join(secrets.choice(alphabet) for _ in range(14)) + secrets.choice("!#%*-_=+") + \
            secrets.choice(string.digits) + secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase)
        data[site] = {"email": email, "password": pw, "url": opt(a, "--url", ""), "created": now()}
        save_accounts(data)
        out({"site": site, "email": email, "password_length": len(pw), "file": ACCOUNTS,
             "use": f"json.load(open('{ACCOUNTS}'))['{site}']['password'] inside your browser code; never print it"})
    out({"error": "usage: acct new SITE [--email E] [--url U] | acct list | acct get SITE"}, 2)


def usage(cmd):
    """The docstring lines for one command (and the notes/statuses that apply to it)."""
    lines, keep = [], False
    for line in __doc__.splitlines():
        if line.startswith("  python3 track.py "):
            keep = line.split()[2] == cmd
        elif not line.startswith("        "):
            keep = False
        if keep:
            lines.append(line)
    tail = __doc__.split("\nNotes:")[1]
    if cmd in ("add", "set"):
        lines.append("\nNotes:" + tail.rstrip())
    return "\n".join(lines)


def wants_help(a):
    valued = KNOWN - {"--force"}
    return any(t in ("-h", "--help") and (i == 0 or ALIASES.get(a[i - 1], a[i - 1]) not in valued)
               for i, t in enumerate(a))


def main(a):
    cmds = {"add": cmd_add, "claim": cmd_claim, "set": cmd_set, "show": cmd_show, "find": cmd_find,
            "next": cmd_next, "list": cmd_list, "waiting": cmd_waiting, "today": cmd_today, "stats": cmd_stats,
            "acct": cmd_acct}
    if not a or a[0] not in cmds or wants_help(a):
        print(usage(a[0]) if a and a[0] in cmds else __doc__)
        sys.exit(0 if a and (a[0] in cmds or a[0] in ("-h", "--help", "help")) else 2)
    check_flags(a)
    try:
        cmds[a[0]](a)
    except ValueError as e:
        out({"error": f"bad value: {e}"}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
