#!/usr/bin/env python3
"""mail.py - manage the job mailbox (Gmail via IMAP + SMTP with an app password). Python 3.8+, stdlib only.
JSON on stdout. Never prints the password. Exit 0 ok, 1 error, 2 usage.

  python3 mail.py check                          login works? capabilities, special folders, SMTP login
  python3 mail.py search "GMAIL QUERY" [--max 20]  e.g. 'newer_than:3d -label:autopilot/seen'
  python3 mail.py show ID [--chars 4000]         headers, text body, links, codes, attachments, bounce info
  python3 mail.py codes [--minutes 30] [--to X] [--from Y]   newest verification codes + verify/confirm links
  python3 mail.py attachments ID [--out cv/]     save attachments; prints paths, sizes, sha256
  python3 mail.py labels | mklabel NAME          list / create labels (nested with "/")
  python3 mail.py label ID.. [--add L].. [--remove L]..   add/remove labels (missing labels are created)
  python3 mail.py archive|trash|untrash|spam|read|unread|star|unstar ID..
                                                 trash/spam refuse mail labelled Jobs/..., starred or important
                                                 (--force only when the owner asked for that message to go)
  python3 mail.py delete ID.. --permanent        delete for ever (only messages already in Trash/Spam)
  python3 mail.py bounces [--hours 72]           delivery failures: failed recipient, reason, original subject
  python3 mail.py send --to X --subject S (--body TEXT | --body-file F) [--attach F].. [--cc X]
                                                 NEW messages only (refuses Re:/Fwd: subjects; use reply)
  python3 mail.py reply ID (--body TEXT | --body-file F) --approved "OWNER'S WORDS" [--attach F]..
                                                 threaded reply to Reply-To/From; logs the approval in JOURNAL.md
  python3 mail.py draft [--to X] [--subject S] (--body TEXT | --body-file F) [--attach F].. [--reply-to-id ID]
                                                 saved to Drafts, never sent

IDs are Gmail message ids in hex, as in Gmail web URLs (a full mail.google.com link is accepted too); "thread" is
the Gmail thread id. On non-Gmail servers IDs are FOLDER:UID and labels are folders (labelling copies the message
into that folder). Search takes Gmail query syntax; on non-Gmail servers a subset is translated: from: to: cc:
subject: newer_than: older_than: after: before: is:unread/read/starred has:attachment label: in: -label:
rfc822msgid: and plain words.

Config: ~/.config/job-autopilot/mail.env (mode 600), or the file named by $JOBS_MAIL_ENV:
  MAIL_USER=you@gmail.com   MAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx (spaces are stripped)
  optional: MAIL_IMAP_HOST=imap.gmail.com MAIL_IMAP_PORT=993 MAIL_IMAP_SSL=1
            MAIL_SMTP_HOST=smtp.gmail.com MAIL_SMTP_PORT=465 MAIL_SMTP_SSL=1 (0 = plain, STARTTLS if offered)
            MAIL_FROM_NAME="Full Name"   (display name on sent mail)
Python API: load_config(); `with imap() as b:` b.search(q, mx), b.show(id), b.label(id, add, remove), b.act(verb, id);
  send_message(to, subject, body, attachments=(), cc=None, reply_to_id=None) -> dict.
"""
import email, email.policy, hashlib, html, imaplib, json, mimetypes, os, re, smtplib, socket, ssl, sys, time
from contextlib import contextmanager
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parsedate_to_datetime

SPECIAL = ("All", "Trash", "Drafts", "Sent", "Junk", "Archive")
FALLBACK = {"Trash": ["Trash", "Deleted Items", "Deleted Messages", "Bin"], "Drafts": ["Drafts"], "Junk": ["Junk", "Spam"],
            "Sent": ["Sent", "Sent Items", "Sent Messages"], "Archive": ["Archive"]}
SYSTEM = {"inbox": "\\Inbox", "starred": "\\Starred", "important": "\\Important"}
LINK_HINT = re.compile(r"verif|confirm|magic|token|login|log-in|signin|sign-in|invite|activat|auth|password|reset", re.I)
CODE_RE = re.compile(r"(?<![\w#/.£$€-])(\d{4,8}|[A-Z0-9]{3,4}-[A-Z0-9]{3,4})(?![\w/%-]|\.\d)")
CODE_HINT = re.compile(r"code|verif|otp|one[- ]time|passcode|\bpin\b|security", re.I)
BOUNCE_SUBJ = re.compile(r"undeliver|delivery status notification|failure notice|returned mail|delivery fail|"
                         r"could not be delivered|mail delivery failed|delivery has failed", re.I)
HEADER_MSGID = "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"
socket.setdefaulttimeout(60)


class MailError(Exception):
    pass


def load_config():
    path = os.path.expanduser(os.environ.get("JOBS_MAIL_ENV", "~/.config/job-autopilot/mail.env"))
    c = {"path": path}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            k, eq, v = line.partition("=")
            if eq and k.strip() and not k.strip().startswith("#"):
                c[k.strip()] = v.strip().strip("'\"")
    c["MAIL_APP_PASSWORD"] = c.get("MAIL_APP_PASSWORD", "").replace(" ", "")
    return c


def flag(v, default):
    return default if v in (None, "") else str(v).strip().lower() in ("1", "true", "yes", "on")


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    sys.exit(code)


def scrub(text, cfg):
    pw = cfg.get("MAIL_APP_PASSWORD") or ""
    for s in {pw, " ".join(pw[i:i + 4] for i in range(0, len(pw), 4))} - {""}:
        text = text.replace(s, "***")
    return text


def clean(text):
    lines = [re.sub(r"[ \t ​‌͏]+", " ", l).strip() for l in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_text(h):
    links = re.findall(r"""href\s*=\s*["'](https?://[^"']+)""", h, re.I)
    h = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>|<!--.*?-->", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</?(p|div|tr|li|ul|ol|h\d|table|section|header|footer)\b[^>]*>", "\n", h)
    h = re.sub(r"(?i)</t[dh]\s*>", " ", h)
    return html.unescape(re.sub(r"<[^>]+>", "", h)), [html.unescape(l) for l in links]


def parse(raw):
    return email.message_from_bytes(raw or b"", policy=email.policy.default)


def hdr(msg, name):
    if msg is None:
        return ""
    try:
        return str(msg.get(name, "") or "")
    except Exception:
        return str((msg.get_all(name) or [""])[0])


def part_text(part):
    try:
        return part.get_content()
    except Exception:
        return (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")


def atts_of(msg):
    return [p for p in msg.walk() if not p.is_multipart() and (p.get_filename() or p.get_content_disposition() == "attachment")]


def text_of(msg):
    plain, htm, links = [], [], []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        if part.get_content_type() == "text/plain":
            plain.append(part_text(part))
        elif part.get_content_type() == "text/html":
            t, l = html_text(part_text(part))
            htm.append(t)
            links += l
    text = clean("\n".join(plain) if any(p.strip() for p in plain) else "\n".join(htm))
    links += re.findall(r"https?://[^\s<>\"')\]]+", text)
    return text, list(dict.fromkeys(l.rstrip(".,;") for l in links))


def find_codes(text):
    found = [mo.group(1) for mo in CODE_RE.finditer(text) if any(ch.isdigit() for ch in mo.group(1))
             and not re.fullmatch(r"(19|20)\d\d", mo.group(1)) and CODE_HINT.search(text[max(0, mo.start() - 100):mo.end() + 30])]
    return list(dict.fromkeys(found))[:5]


def is_bounce(msg):
    frm = hdr(msg, "From").lower()
    return (msg.get_content_type() == "multipart/report" or "mailer-daemon" in frm or "postmaster" in frm
            or bool(msg.get("X-Failed-Recipients")) or bool(BOUNCE_SUBJ.search(hdr(msg, "Subject"))))


def bounce_info(msg):
    rcpts, diag, status, orig = [], "", "", None
    for v in msg.get_all("X-Failed-Recipients") or []:
        rcpts += [a.strip() for a in str(v).split(",") if a.strip()]
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "message/delivery-status":
            for block in part.get_payload() or []:
                fr = block.get("Final-Recipient") or block.get("Original-Recipient")
                if fr:
                    rcpts.append(str(fr).split(";")[-1].strip())
                diag = diag or str(block.get("Diagnostic-Code", "")).split(";", 1)[-1].strip()
                status = status or str(block.get("Status", "") or "")
        elif ct == "message/rfc822" and orig is None and isinstance(part.get_payload(), list):
            orig = part.get_payload()[0]
        elif ct == "text/rfc822-headers" and orig is None:
            orig = email.message_from_string(part_text(part), policy=email.policy.default)
    text = text_of(msg)[0]
    if not rcpts:
        rcpts = re.findall(r"(?:delivered to|delivery to|recipients?:?|failed:)\s*<?([\w.+-]+@[\w-]+\.[\w.-]+)", text, re.I)[:3]
    if not diag:
        mo = re.search(r"\b([45]\d\d[ -](?:[45]\.\d+\.\d+)?.{0,200})", text)
        diag = mo.group(1).strip() if mo else ""
    return {"failed_recipients": list(dict.fromkeys(r.lower() for r in rcpts)), "status": status, "reason": diag[:300],
            "original_subject": hdr(orig, "Subject"), "original_message_id": hdr(orig, "Message-ID")}


def gm_labels(meta):
    i, labels = meta.find(b"X-GM-LABELS ("), []
    for mo in re.finditer(r'\s*(?:"((?:[^"\\]|\\.)*)"|([^\s()"]+)|(\)))', meta[i + 13:].decode("utf-8", "replace") if i >= 0 else ")"):
        if mo.group(3):
            break
        labels.append(re.sub(r"\\(.)", r"\1", mo.group(1)) if mo.group(1) is not None else mo.group(2))
    return labels


class Box:
    """One logged-in IMAP connection. Gmail: UIDs in the \\All folder, ids = X-GM-MSGID hex. Others: FOLDER:UID."""

    def __init__(self, cfg):
        self.cfg, user, pw = cfg, cfg.get("MAIL_USER"), cfg.get("MAIL_APP_PASSWORD")
        if not user or not pw:
            raise MailError(f"MAIL_USER / MAIL_APP_PASSWORD missing in {cfg['path']}")
        use_ssl, host = flag(cfg.get("MAIL_IMAP_SSL"), True), cfg.get("MAIL_IMAP_HOST") or "imap.gmail.com"
        port = int(cfg.get("MAIL_IMAP_PORT") or (993 if use_ssl else 143))
        self.where = f"{host}:{port}"
        self.m = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        self.m.login(user, pw)
        typ, dat = self.m.capability()
        self.caps = set((dat[0] or b"").decode().upper().split()) if typ == "OK" else set(self.m.capabilities)
        self.gmail, self.sel = "X-GM-EXT-1" in self.caps, None
        self.load_folders()

    def load_folders(self):
        """Folder map from LIST; special folders by SPECIAL-USE flag, never by name ("[Google Mail]/Bin" exists)."""
        self.folders, self.special, self.delim = {}, {}, "/"
        for raw in self.m.list()[1]:
            if isinstance(raw, tuple):
                raw = raw[0] + b'"' + raw[1].replace(b'"', b'\\"') + b'"'
            mo = re.match(rb'\((.*?)\) (?:"((?:[^"\\]|\\.)*)"|NIL) (.*)$', raw or b"")
            if not mo:
                continue
            flags, d, name = mo.group(1).decode().split(), mo.group(2), mo.group(3).strip().decode("utf-8", "replace")
            if name.startswith('"'):
                name = re.sub(r'\\(.)', r'\1', name[1:-1])
            self.delim = d.decode() if d else self.delim
            self.folders[name] = flags
            for f in flags:
                if f[1:].capitalize() in SPECIAL:
                    self.special[f[1:].capitalize()] = name

    def folder(self, kind, create=False):
        if kind in self.special:
            return self.special[kind]
        lower = {n.lower(): n for n in self.folders}
        for cand in [c for n in FALLBACK.get(kind, []) for c in (n, "INBOX" + self.delim + n)]:
            if cand.lower() in lower:
                return lower[cand.lower()]
        if create and not self.gmail:
            self.m.create(q(kind))
            self.load_folders()
            return kind
        return None

    def specials(self):
        return {k: self.folder(k) for k in SPECIAL if self.folder(k)}

    def label_folder(self, label):
        kinds = {"trash": "Trash", "bin": "Trash", "spam": "Junk", "sent": "Sent", "drafts": "Drafts", "draft": "Drafts"}
        if label.lower() in ("inbox", "\\inbox"):
            return "INBOX"
        if label.lower() in kinds:
            return self.folder(kinds[label.lower()]) or kinds[label.lower()]
        return label if self.gmail else label.replace("/", self.delim)

    def select(self, folder, ro=True):
        if self.sel != (folder, ro):
            typ, dat = self.m.select(q(folder), readonly=ro)
            if typ != "OK":
                raise MailError(f"cannot open folder {folder}: {dat}")
            self.sel = (folder, ro)

    def uid(self, *args):
        typ, dat = self.m.uid(*args)
        if typ != "OK":
            raise MailError(f"{args[0]} failed: {dat}")
        return dat

    def uid_search(self, *crit):
        return (self.uid("SEARCH", *crit)[0] or b"").split()

    def gm_search(self, query):
        if query.isascii():
            return self.uid_search("X-GM-RAW", q(query))
        self.m.literal = query.encode()
        return self.uid_search("CHARSET", "UTF-8", "X-GM-RAW")

    def fetch(self, uids, body="BODY.PEEK[]"):
        """FETCH (one literal per message at most) -> [{"meta": bytes, "body": bytes}] in the order of uids."""
        if not uids:
            return []
        items = "UID FLAGS INTERNALDATE" + (" X-GM-MSGID X-GM-THRID X-GM-LABELS" if self.gmail else "") + (" " + body if body else "")
        res, cur = [], None
        for part in self.uid("FETCH", b",".join(uids).decode(), f"({items})"):
            if isinstance(part, tuple):
                cur = {"meta": part[0], "body": part[1]}
                res.append(cur)
            elif part and re.match(rb"\d+ \(", part):
                cur = {"meta": part, "body": b""}
                res.append(cur)
            elif part and cur is not None:
                cur["meta"] += part
        byuid = {mo.group(1): r for r in res for mo in [re.search(rb"UID (\d+)", r["meta"])] if mo}
        return [byuid[u] for u in uids if u in byuid]

    def msgid_of(self, u):
        recs = self.fetch([u], HEADER_MSGID)
        return hdr(parse(recs[0]["body"]), "Message-ID") if recs else ""

    def ident(self, folder, rec):
        if self.gmail:
            return format(int(re.search(rb"X-GM-MSGID (\d+)", rec["meta"]).group(1)), "x")
        return f"{folder}:{re.search(rb'UID (\d+)', rec['meta']).group(1).decode()}"

    def locate(self, mid):
        mid = mid.strip()
        if self.gmail:
            mid = mid.rstrip("/").rsplit("/", 1)[-1].split("#")[-1]
            if not re.fullmatch(r"[0-9a-fA-F]{1,16}", mid):
                raise MailError(f"bad message id {mid!r}")
            for kind in ("All", "Trash", "Junk", "Drafts"):
                if self.special.get(kind):
                    self.select(self.special[kind])
                    found = self.uid_search("X-GM-MSGID", str(int(mid, 16)))
                    if found:
                        return self.special[kind], found[-1]
            raise MailError(f"message {mid} not found")
        folder, _, u = mid.rpartition(":")
        if not folder or not u.isdigit():
            raise MailError(f"bad message id {mid!r} (expected FOLDER:UID)")
        if folder not in self.folders:
            raise MailError(f"message {mid} not found (no folder {folder})")
        self.select(folder)
        if not self.uid_search("UID", u):
            raise MailError(f"message {mid} not found")
        return folder, u.encode()

    def summary(self, folder, rec):
        msg, meta = parse(rec["body"]), rec["meta"]
        flags = (re.search(rb"FLAGS \(([^)]*)\)", meta) or re.search(b"()", b"")).group(1).decode()
        itime = imaplib.Internaldate2tuple(meta) or time.localtime()
        try:
            date = parsedate_to_datetime(hdr(msg, "Date")).isoformat()
        except Exception:
            date = time.strftime("%Y-%m-%dT%H:%M:%S", itime)
        d = {"id": self.ident(folder, rec), "thread": None, "date": date, "from": hdr(msg, "From"), "to": hdr(msg, "To"),
             "subject": hdr(msg, "Subject"), "labels": gm_labels(meta) if self.gmail else [folder.replace(self.delim, "/")],
             "unread": "\\Seen" not in flags, "starred": "\\Flagged" in flags,
             "snippet": re.sub(r"\s+", " ", text_of(msg)[0])[:200], "has_attachments": bool(atts_of(msg)),
             "_t": time.mktime(itime), "_mid": hdr(msg, "Message-ID")}
        if self.gmail:
            d["thread"] = format(int(re.search(rb"X-GM-THRID (\d+)", meta).group(1)), "x")
            d["link"] = "https://mail.google.com/mail/u/0/#all/" + d["id"]
        return d

    def search(self, query, mx=20):
        if self.gmail:  # X-GM-RAW searches the selected folder; trash and spam are not in All Mail
            folder = self.special["All"]
            if re.search(r"(^|\s)in:(trash|bin)\b", query):
                folder = self.special.get("Trash", folder)
            elif re.search(r"(^|\s)in:spam\b", query):
                folder = self.special.get("Junk", folder)
            self.select(folder)
            uids = sorted(self.gm_search(query), key=int)[-mx:][::-1]
            res = [self.summary(folder, r) for r in self.fetch(uids, "BODY.PEEK[]<0.300000>")]
        else:
            res = self.plain_search(query, mx)
        for r in res:
            r.pop("_t"), r.pop("_mid")
        return res

    def plain_search(self, query, mx):
        """Gmail query -> IMAP SEARCH over folders (labels); results deduped by Message-ID, newest first."""
        crit, want, excl, after, has_att = [], None, [], 0, False
        for neg, key, val in re.findall(r'(-?)(?:([a-z_]+):)?("[^"]*"|\S+)', query):
            val, key, c = val.strip('"'), key.lower(), []
            if key in ("from", "to", "cc", "subject"):
                c = ["HEADER", key.capitalize(), q(val)]  # substring match everywhere (some servers' FROM is exact)
            elif key in ("newer_than", "older_than"):
                mo = re.fullmatch(r"(\d+)([hdmy])", val)
                when = time.time() - (int(mo.group(1)) * {"h": 3600, "d": 86400, "m": 2592000, "y": 31536000}[mo.group(2)] if mo else 86400)
                c = ["SINCE" if key == "newer_than" else "BEFORE", time.strftime("%d-%b-%Y", time.localtime(when))]
                after = max(after, when) if key == "newer_than" else after
            elif key in ("after", "before"):
                c = ["SINCE" if key == "after" else "BEFORE", datetime.strptime(val, "%Y/%m/%d").strftime("%d-%b-%Y")]
            elif key == "is":
                c = [{"unread": "UNSEEN", "read": "SEEN", "starred": "FLAGGED"}.get(val, "ALL")]
            elif key == "has":
                has_att = val == "attachment"
            elif key in ("label", "in"):
                excl.append(val) if neg else None
                want = want if neg else val
            elif key == "rfc822msgid":
                c = ["HEADER", "Message-ID", q(val)]
            else:
                c = ["TEXT", q(val if not key else key + ":" + val)]
            crit += ["NOT"] + c if neg and c else c
        if want and want != "anywhere":
            folders = [self.label_folder(want)]
        else:
            skip = {self.folder(k) for k in (("Drafts",) if want == "anywhere" else ("Trash", "Junk", "Drafts"))}
            folders = [f for f, fl in self.folders.items() if "\\Noselect" not in fl and f not in skip]
        skip_mids = set()
        for f in [self.label_folder(l) for l in excl]:
            if f in self.folders:
                self.select(f)
                skip_mids |= {hdr(parse(r["body"]), "Message-ID") for r in self.fetch(self.uid_search("ALL"), HEADER_MSGID)}
        res = {}
        for f in [f for f in folders if f in self.folders]:
            self.select(f)
            for r in self.fetch(self.uid_search(*(crit or ["ALL"]))[-mx * 3:], "BODY.PEEK[]<0.300000>"):
                s = self.summary(f, r)
                if s["_t"] < after or (has_att and not s["has_attachments"]) or (s["_mid"] and s["_mid"] in skip_mids):
                    continue
                if (s["_mid"] or s["id"]) in res:
                    res[s["_mid"] or s["id"]]["labels"] += s["labels"]
                else:
                    res[s["_mid"] or s["id"]] = s
        return sorted(res.values(), key=lambda s: -s["_t"])[:mx]

    def message(self, mid):
        folder, u = self.locate(mid)
        self.select(folder)
        recs = self.fetch([u])
        if not recs:
            raise MailError(f"message {mid} not found")
        return folder, recs[0], parse(recs[0]["body"])

    def show(self, mid, chars=4000):
        folder, rec, msg = self.message(mid)
        text, links = text_of(msg)
        d = self.summary(folder, rec)
        d.pop("_t"), d.pop("_mid")
        d.update({"cc": hdr(msg, "Cc"), "reply_to": hdr(msg, "Reply-To"), "message_id": hdr(msg, "Message-ID"),
                  "folder": folder, "text": text[:chars], "truncated": len(text) > chars, "links": links[:40],
                  "codes": find_codes(d["subject"] + "\n" + text),
                  "attachments": [{"index": i, "name": p.get_filename() or f"part{i}", "type": p.get_content_type(),
                                   "size": len(p.get_payload(decode=True) or b"")} for i, p in enumerate(atts_of(msg))]})
        if is_bounce(msg):
            d["bounce"] = bounce_info(msg)
        return d

    def find_msgid(self, folder, mid, tries=1):
        for i in range(tries):
            self.select(folder)
            self.m.noop()
            uids = self.gm_search("rfc822msgid:" + mid.strip("<>")) if self.gmail else self.uid_search("HEADER", "Message-ID", q(mid))
            if uids:
                return self.ident(folder, self.fetch(uids[-1:], "")[0])
            time.sleep(2 if i + 1 < tries else 0)
        return None

    def ensure_label(self, label):
        f = self.label_folder(label)
        if f not in self.folders and f != "INBOX":
            typ, dat = self.m.create(q(f))
            if typ != "OK" and b"exist" not in b" ".join(x for x in dat if x).lower():
                raise MailError(f"cannot create label {label}: {dat}")
            self.load_folders()
        return f

    def store(self, mid, op, value):
        folder, u = self.locate(mid)
        self.select(folder, ro=False)
        self.uid("STORE", u, op, value)

    def expunge(self, u):  # UID EXPUNGE only touches this message
        self.uid("STORE", u, "+FLAGS.SILENT", "(\\Deleted)")
        self.uid("EXPUNGE", u) if "UIDPLUS" in self.caps else self.m.expunge()

    def move(self, mid, dest):
        folder, u = self.locate(mid)
        if folder == dest:
            return mid
        self.select(folder, ro=False)
        msgid = None if self.gmail else self.msgid_of(u)
        if "MOVE" in self.caps:
            self.uid("MOVE", u, q(dest))
        else:
            self.uid("COPY", u, q(dest))
            self.expunge(u)
        return mid if self.gmail else (self.find_msgid(dest, msgid) if msgid else None)

    def label(self, mid, add=(), remove=()):
        if self.gmail:
            arg = lambda ls: "(" + " ".join(SYSTEM.get(l.lower().lstrip("\\")) or q(l) for l in ls) + ")"
            for l in add:
                SYSTEM.get(l.lower().lstrip("\\")) or self.ensure_label(l)
            if add:
                self.store(mid, "+X-GM-LABELS", arg(add))
            if remove:
                self.store(mid, "-X-GM-LABELS", arg(remove))
            return mid
        for l in add:
            f, (folder, u) = self.ensure_label(l), self.locate(mid)
            self.select(folder)
            self.uid("COPY", u, q(f))
        for l in remove:
            f, (folder, u) = self.label_folder(l), self.locate(mid)
            if f == "INBOX" == folder:
                mid = self.move(mid, self.folder("Archive", create=True))
            elif f in self.folders and f != folder:
                self.select(folder)
                msgid = self.msgid_of(u)
                self.select(f, ro=False)
                for x in self.uid_search("HEADER", "Message-ID", q(msgid)) if msgid else []:
                    self.expunge(x)
        return mid

    def protected(self, mid):
        """Why this message must not be trashed or marked spam (a Jobs/ label, starred, important), or ''."""
        folder, u = self.locate(mid)
        recs = self.fetch([u], body=None)
        if not recs:
            return ""
        meta = recs[0]["meta"]
        labels = gm_labels(meta) if self.gmail else [folder.replace(self.delim, "/")]
        why = [l for l in labels if l.lower().startswith("jobs/") or l in ("\\Starred", "\\Important")]
        if b"\\Flagged" in meta:
            why.append("starred")
        return ", ".join(dict.fromkeys(why))

    def act(self, verb, mid):
        """Returns the message's id afterwards (changes on non-Gmail servers when it moves folder)."""
        if verb in ("read", "unread", "star", "unstar"):
            self.store(mid, ("-" if verb.startswith("un") else "+") + "FLAGS.SILENT", "(\\Seen)" if "read" in verb else "(\\Flagged)")
            return mid
        if verb == "archive":
            return self.label(mid, remove=["Inbox"])
        if verb in ("trash", "spam"):  # Gmail: moving out of All Mail into Bin/Spam is what the web UI does
            return self.move(mid, self.folder("Trash" if verb == "trash" else "Junk", create=True))
        folder, u = self.locate(mid)
        if folder not in (self.folder("Trash"), self.folder("Junk")):
            raise MailError(f"{mid} is not in Trash/Spam" + ("; trash it first" if verb == "delete" else ""))
        if verb == "untrash":
            return self.move(mid, "INBOX")
        self.select(folder, ro=False)
        self.expunge(u)

    def append(self, kind, msg, flags):
        folder = self.folder(kind, create=True)
        typ, dat = self.m.append(q(folder), flags, imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise MailError(f"APPEND to {folder} failed: {dat}")
        return self.find_msgid(folder, msg["Message-ID"], tries=3)

    def bounces(self, hours):
        query = f"newer_than:{max(1, hours)}h " + (
            '{from:mailer-daemon from:postmaster subject:undeliverable subject:"delivery status notification" '
            'subject:"failure notice" subject:"returned mail" subject:"delivery failure" subject:undelivered}' if self.gmail else "in:inbox")
        res, cutoff = [], time.time() - hours * 3600
        for s in self.search(query, 50):
            folder, rec, msg = self.message(s["id"])
            if is_bounce(msg) and time.mktime(imaplib.Internaldate2tuple(rec["meta"]) or time.localtime()) >= cutoff:
                res.append(dict({k: s[k] for k in ("id", "date", "from", "subject")}, **bounce_info(msg)))
        return res


@contextmanager
def imap(cfg=None):
    b = Box(cfg or load_config())
    try:
        yield b
    finally:
        try:
            b.m.logout()
        except Exception:
            pass


def build(cfg, to, subject, body, attachments=(), cc=None, orig=None):
    msg, user = EmailMessage(), cfg["MAIL_USER"]
    msg["From"] = formataddr((cfg["MAIL_FROM_NAME"], user)) if cfg.get("MAIL_FROM_NAME") else user
    msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
    if cc:
        msg["Cc"] = ", ".join(cc) if isinstance(cc, (list, tuple)) else cc
    msg["Subject"], msg["Date"] = subject, formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=user.split("@")[-1])  # local id, so the copy in Sent can be found
    if hdr(orig, "Message-ID"):
        msg["In-Reply-To"] = hdr(orig, "Message-ID")
        msg["References"] = (re.sub(r"\s+", " ", hdr(orig, "References")) + " " + hdr(orig, "Message-ID")).strip()
    msg.set_content(body)
    for path in attachments:
        ctype = (mimetypes.guess_type(path)[0] or "application/octet-stream").split("/")
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=ctype[0], subtype=ctype[1], filename=os.path.basename(path))
    return msg


def smtp_send(cfg, msg):
    """Send msg (or just test the login when msg is None). Returns refused recipients."""
    use_ssl, host = flag(cfg.get("MAIL_SMTP_SSL"), True), cfg.get("MAIL_SMTP_HOST") or "smtp.gmail.com"
    port = int(cfg.get("MAIL_SMTP_PORT") or (465 if use_ssl else 587))
    s = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) if use_ssl else smtplib.SMTP(host, port)
    try:
        if not use_ssl:
            s.ehlo()
            if s.has_extn("starttls"):
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
        s.login(cfg["MAIL_USER"], cfg["MAIL_APP_PASSWORD"])
        return s.send_message(msg) if msg is not None else {}
    finally:
        try:
            s.quit()
        except Exception:
            pass


def send_message(to, subject, body, attachments=(), cc=None, reply_to_id=None, cfg=None):
    cfg = cfg or load_config()
    with imap(cfg) as b:
        msg = build(cfg, to, subject, body, attachments, cc, b.message(reply_to_id)[2] if reply_to_id else None)
        refused = smtp_send(cfg, msg)
        # Gmail files sent mail itself; other servers need a copy APPENDed to Sent
        mid = b.find_msgid(b.special["All"], msg["Message-ID"], tries=5) if b.gmail else b.append("Sent", msg, "(\\Seen)")
        res = {"sent": True, "message_id": msg["Message-ID"], "id": mid, "to": msg["To"], "subject": subject,
               "attachments": [os.path.basename(a) for a in attachments], "refused": refused}
        if b.gmail and mid:
            res["link"] = "https://mail.google.com/mail/u/0/#all/" + mid
        return res


def parse_args(argv, flags=("--permanent", "--force")):
    pos, opt, i = [], {}, 0
    while i < len(argv):
        if argv[i] in flags:
            opt.setdefault(argv[i], []).append(True)
        elif argv[i].startswith("--") and i + 1 < len(argv):
            opt.setdefault(argv[i], []).append(argv[i + 1])
            i += 1
        else:
            pos.append(argv[i])
        i += 1
    return pos, opt


CMDS = ("check search show codes attachments labels mklabel label archive trash untrash spam read unread star unstar "
        "delete bounces send reply draft").split()


def main(argv):
    cmd = argv[0] if argv else None
    if cmd not in CMDS:
        print(__doc__)
        sys.exit(2)
    pos, opt = parse_args(argv[1:])
    one = lambda k, d=None: opt.get(k, [d])[-1]
    body = open(one("--body-file"), encoding="utf-8").read() if one("--body-file") else one("--body")
    cfg = load_config()
    if cmd == "send":
        if not (one("--to") and one("--subject") and body is not None):
            out({"error": "send needs --to, --subject and --body or --body-file"}, 2)
        if re.match(r"\s*(re|fwd?|fw)\s*:", one("--subject"), re.I):
            out({"error": "send is for NEW messages only; use reply ID ... --approved"}, 2)
        out(send_message(opt["--to"], one("--subject"), body, opt.get("--attach", []), opt.get("--cc")))
    if cmd == "reply":
        approved = " ".join((one("--approved") or "").split())
        if not pos or body is None:
            out({"error": "reply ID (--body TEXT | --body-file F) --approved \"OWNER'S WORDS\""}, 2)
        if not approved:
            out({"error": "refused: reply needs the owner's approval words in --approved. Draft it and ask the owner."}, 2)
        with imap(cfg) as b:
            orig = b.message(pos[0])[2]
        subj = hdr(orig, "Subject") if re.match(r"\s*re\s*:", hdr(orig, "Subject"), re.I) else "Re: " + hdr(orig, "Subject")
        quoted = "\n".join("> " + l for l in text_of(orig)[0][:3000].splitlines())
        full = f"{body.rstrip()}\n\nOn {hdr(orig, 'Date')}, {hdr(orig, 'From')} wrote:\n{quoted}\n"
        res = send_message(hdr(orig, "Reply-To") or hdr(orig, "From"), subj, full, opt.get("--attach", []), reply_to_id=pos[0], cfg=cfg)
        if os.path.isfile("JOURNAL.md"):
            with open("JOURNAL.md", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime())} REPLY {pos[0]} approved: {approved}\n")
            res["journal"] = True
        out(res)
    with imap(cfg) as b:
        if cmd == "check":
            res = {"ok": True, "user": cfg["MAIL_USER"], "password_length": len(cfg["MAIL_APP_PASSWORD"]), "imap": b.where,
                   "gmail": b.gmail, "capabilities": sorted(b.caps), "folders": len(b.folders), "special": b.specials()}
            try:
                smtp_send(cfg, None)
                res["smtp"] = "ok"
            except Exception as e:
                res["smtp"] = "failed: " + scrub(str(e), cfg)[:200]
            out(res)
        if cmd == "search" and pos:
            res = b.search(pos[0], int(one("--max", 20)))
            out({"count": len(res), "messages": res})
        if cmd == "show" and pos:
            out(b.show(pos[0], int(one("--chars", 4000))))
        if cmd == "codes":
            mins = int(one("--minutes", 30))
            query = f"newer_than:{max(1, -(-mins // 60))}h" + "".join(f" {k}:{one('--' + k)}" for k in ("to", "from") if one("--" + k))
            res = []
            for s in b.search(query, 30):
                d = b.show(s["id"], 20000)
                links = [l for l in d["links"] if LINK_HINT.search(l)][:5]
                if (d["codes"] or links) and datetime.fromisoformat(d["date"]).timestamp() >= time.time() - mins * 60:
                    res.append(dict({k: d[k] for k in ("id", "date", "from", "to", "subject", "codes")}, links=links))
            out({"count": len(res), "messages": res[:10]})
        if cmd == "attachments" and pos:
            outdir, saved = one("--out", "."), []
            os.makedirs(outdir, exist_ok=True)
            for i, p in enumerate(atts_of(b.message(pos[0])[2])):
                data = p.get_payload(decode=True) or b""
                name = re.sub(r"[^\w.\- ()–]+", "_", os.path.basename(p.get_filename() or f"part{i}")).strip(" .") or f"part{i}"
                path, n = os.path.join(outdir, name), 1
                while os.path.exists(path):
                    path, n = os.path.join(outdir, f"{os.path.splitext(name)[0]}-{n}{os.path.splitext(name)[1]}"), n + 1
                with open(path, "wb") as f:
                    f.write(data)
                saved.append({"index": i, "path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            out({"saved": saved})
        if cmd == "labels":
            hide = set(b.specials().values()) | {"INBOX"}
            out({"labels": [f if b.gmail else f.replace(b.delim, "/") for f in sorted(b.folders)
                            if f not in hide and f.split("/")[0] not in ("[Gmail]", "[Google Mail]")], "special": b.specials()})
        if cmd == "mklabel" and pos:
            existed = b.label_folder(pos[0]) in b.folders
            b.ensure_label(pos[0])
            out({"label": pos[0], "created": not existed})
        if cmd == "label" and pos and (opt.get("--add") or opt.get("--remove")):
            out({"labelled": [{"id": i, "now": b.label(i, opt.get("--add", []), opt.get("--remove", []))} for i in pos],
                 "added": opt.get("--add", []), "removed": opt.get("--remove", [])})
        if cmd in ("trash", "spam") and pos and "--force" not in opt:
            for i in pos:
                why = b.protected(i)
                if why:
                    out({"error": f"refused: {i} is protected ({why}). Application, interview, offer and starred mail "
                                  "is kept. Use --force only if the owner asked for this message to go."}, 2)
        if cmd in ("archive", "trash", "untrash", "spam", "read", "unread", "star", "unstar") and pos:
            out({"action": cmd, "done": [{"id": i, "now": b.act(cmd, i)} for i in pos]})
        if cmd == "delete" and pos:
            if "--permanent" not in opt:
                out({"error": "refused: delete is permanent; pass --permanent (only works on messages in Trash/Spam)"}, 2)
            out({"deleted": [b.act("delete", i) or i for i in pos]})
        if cmd == "bounces":
            res = b.bounces(int(one("--hours", 72)))
            out({"count": len(res), "bounces": res})
        if cmd == "draft":
            orig = b.message(one("--reply-to-id"))[2] if one("--reply-to-id") else None
            to = opt.get("--to") or ([hdr(orig, "Reply-To") or hdr(orig, "From")] if orig is not None else None)
            subj = one("--subject") or ("Re: " + re.sub(r"^\s*re\s*:\s*", "", hdr(orig, "Subject"), flags=re.I) if orig is not None else None)
            if not (to and subj and body is not None):
                out({"error": "draft needs --to, --subject (or --reply-to-id) and --body or --body-file"}, 2)
            msg = build(cfg, to, subj, body, opt.get("--attach", []), opt.get("--cc"), orig)
            out({"draft": True, "id": b.append("Drafts", msg, "(\\Draft \\Seen)"), "to": msg["To"], "subject": subj,
                 "message_id": msg["Message-ID"]})
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (MailError, imaplib.IMAP4.error, smtplib.SMTPException, OSError, ValueError, KeyError) as e:
        out({"error": scrub(f"{type(e).__name__}: {e}", load_config())[:500]}, 1)
