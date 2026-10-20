#!/usr/bin/env python3
"""track.py - the application tracker (./applications.csv) and site accounts (./secrets/accounts.json).
Python 3.8+, stdlib only. Run from the campaign folder. File-locked, so parallel helpers are safe.

  python3 track.py add --company C --role R --url U [--source S] [--notes N] [--status QUEUED]
        -> {"id": ...} or {"duplicate": id, "status": ...}   (same URL, or same company + role)
  python3 track.py claim ID [--by NAME]       QUEUED/WAITING/FAILED -> WORKING (refused if another worker has it, < 2h)
  python3 track.py set ID STATUS [--method M] [--email E] [--confirmation TEXT] [--ask Qn] [--note TEXT]
  python3 track.py find TEXT                  search company, role, url, notes
  python3 track.py next [--n 5]               oldest QUEUED jobs
  python3 track.py list [--status S] [--days N]
  python3 track.py today                      counts for today (Europe/London, or $JOBS_TZ), and submitted list
  python3 track.py stats                      totals by status, submissions per day

  python3 track.py acct new SITE [--email E] [--url U]   unique strong password, saved to secrets/accounts.json
        (mode 600). Default email: plus-alias of MAIL_USER, e.g. you+workday-acme@gmail.com. Prints no password.
  python3 track.py acct list | acct get SITE  (never prints passwords; your browser code reads the json file)

Statuses: QUEUED WORKING WAITING SUBMITTED FAILED SKIPPED INTERVIEW REJECTED OFFER
  QUEUED found and matches TARGET.md, not started   WAITING needs the owner (--ask Qn)
  SUBMITTED proven (success page or acknowledgement) FAILED could not be completed (--note why)
  SKIPPED doesn't match (--note the one-line reason) INTERVIEW / REJECTED / OFFER from the mailbox
"""
import csv, fcntl, json, os, re, secrets, string, sys, time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

CSV_PATH = "applications.csv"
ACCOUNTS = os.path.join("secrets", "accounts.json")
FIELDS = ["id", "found", "company", "role", "url", "source", "status", "updated", "applied", "method",
          "email", "confirmation", "ask", "notes"]
STATUSES = ["QUEUED", "WORKING", "WAITING", "SUBMITTED", "FAILED", "SKIPPED", "INTERVIEW", "REJECTED", "OFFER"]
SENT = {"SUBMITTED", "INTERVIEW", "REJECTED", "OFFER"}


def out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    sys.exit(code)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(iso=None):
    tz = os.environ.get("JOBS_TZ", "Europe/London")
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if iso else datetime.now(timezone.utc)
    return (t.astimezone(ZoneInfo(tz)) if ZoneInfo else t).strftime("%Y-%m-%d")


def norm_url(u):
    if not u:
        return ""
    p = urlsplit(u.strip())
    path = re.sub(r"/(apply|application|applynow)/?$", "", p.path.rstrip("/"), flags=re.I)
    return re.sub(r"^www\.", "", p.netloc.lower()) + path.lower()


def norm_text(s):
    s = re.sub(r"\(.*?\)|\b(ltd|limited|plc|inc|llc|uk|the|remote|home ?based|work from home|wfh)\b", " ", (s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


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


def opt(a, name, default=None):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def get(rows, rid):
    for r in rows:
        if r["id"] == rid:
            return r
    out({"error": f"no application {rid}"}, 1)


def brief(r):
    return {k: r[k] for k in ("id", "status", "company", "role", "url", "updated", "ask", "notes") if r.get(k)}


def cmd_add(a):
    company, role, url = opt(a, "--company"), opt(a, "--role"), opt(a, "--url", "")
    if not company or not role:
        out({"error": "--company and --role are required"}, 2)
    status = opt(a, "--status", "QUEUED").upper()
    if status not in STATUSES:
        out({"error": f"status must be one of {STATUSES}"}, 2)
    nu, key = norm_url(url), (norm_text(company), norm_text(role))
    with locked() as box:
        for r in box["rows"]:
            if (nu and norm_url(r["url"]) == nu) or (norm_text(r["company"]), norm_text(r["role"])) == key:
                out({"duplicate": r["id"], "status": r["status"], "company": r["company"], "role": r["role"]})
        n = max([int(r["id"][1:]) for r in box["rows"] if re.fullmatch(r"A\d+", r["id"])] + [0]) + 1
        row = {k: "" for k in FIELDS}
        row.update(id=f"A{n}", found=now(), updated=now(), company=company, role=role, url=url,
                   source=opt(a, "--source", ""), status=status, notes=opt(a, "--notes", ""))
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
    status = a[2].upper()
    with locked() as box:
        r = get(box["rows"], a[1])
        r["status"], r["updated"] = status, now()
        if status != "WORKING":
            r["notes"] = re.sub(r"\s*\[by [^\]]+\]", "", r["notes"])
        if status == "SUBMITTED" and not r["applied"]:
            r["applied"] = now()
        for flag, field in (("--method", "method"), ("--email", "email"), ("--confirmation", "confirmation"),
                            ("--ask", "ask")):
            if opt(a, flag) is not None:
                r[field] = opt(a, flag)
        if opt(a, "--note"):
            r["notes"] = (r["notes"] + " | " if r["notes"] else "") + f"{local_date()}: {opt(a, '--note')}"
        box["dirty"] = True
    out(brief(r))


def read_rows():
    with locked() as box:
        return box["rows"]


def cmd_find(a):
    q = " ".join(a[1:]).lower()
    out([brief(r) for r in read_rows() if q in " ".join([r["company"], r["role"], r["url"], r["notes"]]).lower()][:50])


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
    out([brief(r) for r in rows][-200:])


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


def cmd_stats(a):
    rows, by, per_day = read_rows(), {}, {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
        if r["applied"]:
            d = local_date(r["applied"])
            per_day[d] = per_day.get(d, 0) + 1
    out({"total": len(rows), "by_status": by, "submitted_ever": sum(r["status"] in SENT for r in rows),
         "submitted_per_day": dict(sorted(per_day.items())[-14:])})


def mail_user():
    path = os.path.expanduser(os.environ.get("JOBS_MAIL_ENV", "~/.config/job-autopilot/mail.env"))
    if os.path.isfile(path):
        for line in open(path):
            k, _, v = re.sub(r"^export\s+", "", line.strip()).partition("=")
            if k.strip() == "MAIL_USER":
                return v.strip().strip("'\"")
    return ""


def cmd_acct(a):
    sub = a[1] if len(a) > 1 else ""
    os.makedirs("secrets", mode=0o700, exist_ok=True)
    os.chmod("secrets", 0o700)
    data = json.load(open(ACCOUNTS)) if os.path.exists(ACCOUNTS) else {}
    if sub == "list":
        out([{"site": s, "email": v["email"], "url": v.get("url", ""), "created": v["created"]} for s, v in data.items()])
    if sub == "get" and len(a) > 2:
        v = data.get(a[2])
        out({"site": a[2], "email": v["email"], "url": v.get("url", ""), "created": v["created"],
             "password_length": len(v["password"]), "file": ACCOUNTS} if v else {"error": f"no account for {a[2]}"},
            0 if v else 1)
    if sub == "new" and len(a) > 2:
        site = re.sub(r"[^a-z0-9]+", "-", a[2].lower()).strip("-")
        if site in data:
            out({"exists": site, "email": data[site]["email"], "file": ACCOUNTS})
        email = opt(a, "--email")
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
        fd = os.open(ACCOUNTS + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(ACCOUNTS + ".tmp", ACCOUNTS)
        out({"site": site, "email": email, "password_length": len(pw), "file": ACCOUNTS,
             "use": f"json.load(open('{ACCOUNTS}'))['{site}']['password'] inside your browser code; never print it"})
    out({"error": "usage: acct new SITE [--email E] [--url U] | acct list | acct get SITE"}, 2)


def main(a):
    cmds = {"add": cmd_add, "claim": cmd_claim, "set": cmd_set, "find": cmd_find, "next": cmd_next,
            "list": cmd_list, "today": cmd_today, "stats": cmd_stats, "acct": cmd_acct}
    if not a or a[0] not in cmds:
        print(__doc__)
        sys.exit(2)
    cmds[a[0]](a)


if __name__ == "__main__":
    main(sys.argv[1:])
