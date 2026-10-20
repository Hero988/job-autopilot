#!/usr/bin/env python3
"""browser.py - ONE local headful Chrome shared by every agent and helper on this machine.
Python 3.8+, standard library only for the CLI; the importable helpers need Playwright.

Chrome runs on a virtual display (Xvfb, plus openbox if installed) and keeps its profile (logins)
in ~/.local/state/job-autopilot/chrome-profile. Agents connect over CDP; the owner can see and
control the same browser from any PC through `share`. Prints JSON; never prints cookies, and the VNC
password only inside the share URL (which goes to the owner in chat, never into a file).

  python3 browser.py start                 start Xvfb + Chrome if not running (idempotent, detached)
  python3 browser.py status                running?, tabs, share active + expiry
  python3 browser.py tabs                  list tabs: id, title, url (no query string)
  python3 browser.py close ID [ID..]       close tabs by id
  python3 browser.py close --all           close every tab, leaving one about:blank
  python3 browser.py stop                  stop share, Chrome (gracefully), window manager, Xvfb
  python3 browser.py share [--minutes 60]  remote view+control link (noVNC over a cloudflared quick
                                           tunnel); auto-unshares after --minutes; same link if active
  python3 browser.py unshare               stop x11vnc, websockify, cloudflared

Env: JOBS_DISPLAY (:77) JOBS_CDP_PORT (9333) JOBS_VNC_PORT (5977) JOBS_WS_PORT (6077)

Python helpers (agent code):
  sys.path.insert(0, ".autopilot/scripts"); import browser
  browser.connect(playwright) -> (browser, context)    starts Chrome if needed; context = the profile
  browser.new_tab(context, url, background=True) -> Page   new tab that does not steal focus
  browser.handoff_tab(context, url_or_page) -> Page    URL: own new window, in front, for the owner;
                                                       Page: activated and raised where it is (state kept)
  Disconnect with browser.close() (disconnects only; Chrome keeps running). Close only your own tabs.
"""
import contextlib, io, json, os, re, secrets, shutil, signal, socket, string, subprocess, sys, tarfile, time
import urllib.error, urllib.request

STATE = os.path.expanduser("~/.local/state/job-autopilot")
PROFILE, LOGS = os.path.join(STATE, "chrome-profile"), os.path.join(STATE, "logs")
BJSON, SJSON, PWFILE = (os.path.join(STATE, n) for n in ("browser.json", "share.json", "vncpass"))
DISPLAY = os.environ.get("JOBS_DISPLAY", ":77")
PORT, VNC, WS = (int(os.environ.get(k, d)) for k, d in
                 (("JOBS_CDP_PORT", 9333), ("JOBS_VNC_PORT", 5977), ("JOBS_WS_PORT", 6077)))
CDP = f"http://127.0.0.1:{PORT}"
XSOCK = "/tmp/.X11-unix/X" + DISPLAY.lstrip(":").split(".")[0]
W, H = 1600, 1000
NOVNC_URL = "https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz"
SHARE_PROCS = (("watchdog", "_watchdog"), ("cloudflared", "cloudflared"), ("websockify", "websockify"),
               ("x11vnc", "x11vnc"))
OPENBOX_RC = """<?xml version="1.0"?><openbox_config xmlns="http://openbox.org/3.4/rc">
<focus><focusNew>yes</focusNew><followMouse>no</followMouse></focus>
<applications><application class="*"><maximized>yes</maximized></application></applications>
<keyboard><keybind key="A-Tab"><action name="NextWindow"/></keybind></keyboard>
<mouse><context name="Client"><mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/>
</mousebind></context><context name="Titlebar"><mousebind button="Left" action="Press"><action name="Focus"/>
<action name="Raise"/></mousebind><mousebind button="Right" action="Press"><action name="ShowMenu">
<menu>client-list-combined-menu</menu></action></mousebind></context></mouse>
</openbox_config>"""  # minimal on purpose: no root menu, so a share link can't open a terminal


class Fail(Exception):
    pass


def out(obj, code=0):
    print(json.dumps(obj, indent=2))
    sys.exit(code)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def private(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(text)


def save(path, obj):
    private(path + ".tmp", json.dumps(obj, indent=2))
    os.replace(path + ".tmp", path)


def rm(*paths):
    for p in paths:
        with contextlib.suppress(OSError):
            os.unlink(p)


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def tool(name, *extra):
    """Executable on PATH, else the first existing extra location."""
    return next((c for c in [shutil.which(name)] + [os.path.expanduser(e) for e in extra]
                 if c and os.access(c, os.X_OK)), None)


def alive(pid, name):
    """pid exists, is not a zombie, and its command line contains name (guards against pid reuse)."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            cmd = f.read()
        with open(f"/proc/{int(pid)}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, TypeError, ValueError, IndexError):
        return False
    return state != "Z" and name.encode() in cmd


def kill(pid, name, wait=10):
    if pid == os.getpid() or not alive(pid, name):
        return False
    os.kill(pid, signal.SIGTERM)  # Chrome treats SIGTERM as a normal quit (profile saved)
    if not until(lambda: not alive(pid, name), wait, 0.2):
        os.kill(pid, signal.SIGKILL)
    return True


def spawn(args, log, mode="ab", env=None):
    """Start fully detached (own session), so it survives the calling shell."""
    os.makedirs(LOGS, mode=0o700, exist_ok=True)
    with open(os.path.join(LOGS, log), mode) as f:
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True, env=env).pid


def until(check, timeout, step=0.3):
    end = time.time() + timeout
    while not check():
        if time.time() > end:
            return False
        time.sleep(step)
    return True


def reach(addr):
    """Something listens on a localhost TCP port (int) or a unix socket path (str)."""
    s = socket.socket(socket.AF_UNIX if isinstance(addr, str) else socket.AF_INET)
    s.settimeout(0.5)
    try:
        return s.connect_ex(addr if isinstance(addr, str) else ("127.0.0.1", addr)) == 0
    except OSError:
        return False
    finally:
        s.close()


def http(path, method="GET", timeout=3):
    try:
        with urllib.request.urlopen(urllib.request.Request(CDP + path, method=method), timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError:
        raise
    except OSError:
        raise Fail("Chrome is not running (python3 browser.py start)")
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")


def cdp_ok():
    try:
        return "Browser" in http("/json/version", timeout=2)
    except (Fail, OSError, ValueError, TypeError):
        return False


def pages():
    return [t for t in http("/json/list") if t.get("type") == "page"]


def lock_pid():
    """pid in Chrome's SingletonLock ("host-pid"); 0 if missing, -1 if another host's."""
    try:
        host, _, pid = os.readlink(os.path.join(PROFILE, "SingletonLock")).rpartition("-")
    except OSError:
        return 0
    return int(pid) if host == socket.gethostname() and pid.isdigit() else -1


def start():
    st = load(BJSON)
    if not cdp_ok():
        os.makedirs(LOGS, mode=0o700, exist_ok=True)
        os.chmod(STATE, 0o700), os.chmod(LOGS, 0o700)
        env = dict({k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}, DISPLAY=DISPLAY)
        if not reach(XSOCK):
            st["xvfb_pid"] = spawn(["Xvfb", DISPLAY, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"], "xvfb.log")
            if not until(lambda: reach(XSOCK), 10):
                raise Fail(f"Xvfb did not start on {DISPLAY}; see {LOGS}/xvfb.log")
        if alive(st.get("xvfb_pid"), "Xvfb") and not alive(st.get("wm_pid"), "openbox") and tool("openbox"):
            private(os.path.join(STATE, "openbox-rc.xml"), OPENBOX_RC)
            st["wm_pid"] = spawn(["openbox", "--config-file", os.path.join(STATE, "openbox-rc.xml")],
                                 "openbox.log", env=env)
        pid = lock_pid()
        if pid > 0 and alive(pid, "chrom"):  # running (maybe still starting) on our profile
            if not until(cdp_ok, 15):
                raise Fail(f"Chrome pid {pid} holds the profile but CDP port {PORT} does not answer; run stop")
        else:  # no lock, or a stale one left by a crash: clear it, then launch
            rm(*(os.path.join(PROFILE, "Singleton" + n) for n in ("Lock", "Socket", "Cookie")))
            exe = tool("google-chrome") or tool("google-chrome-stable") or tool("chromium")
            if not exe:
                raise Fail("No google-chrome or chromium on PATH")
            st["pid"] = spawn([exe, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
                               "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                               "--password-store=basic", "--ozone-platform=x11", f"--window-size={W},{H}",
                               "--window-position=0,0", "--start-maximized", "--hide-crash-restore-bubble",
                               "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                               "--disable-backgrounding-occluded-windows", "about:blank"], "chrome.log", env=env)
            if not until(cdp_ok, 30):
                raise Fail(f"Chrome did not open CDP on {PORT}; see {LOGS}/chrome.log")
            st["started"] = iso(time.time())
        st.update(pid=lock_pid() if lock_pid() > 0 else st.get("pid"), display=DISPLAY, cdp=CDP)
        save(BJSON, st)
    return {"cdp": CDP, "display": DISPLAY, "tabs": len(pages()), "pid": st.get("pid")}


def share_alive(sh):
    return bool(sh) and time.time() < sh.get("expires_ts", 0) and all(
        alive(sh.get(k), n) for k, n in SHARE_PROCS[1:])


def status():
    st, sh, up = load(BJSON), load(SJSON), cdp_ok()
    on = share_alive(sh)
    return {"running": up, "cdp": CDP, "display": DISPLAY, "pid": st.get("pid") if up else None,
            "tabs": len(pages()) if up else 0, "share": {"active": on, "expires": sh.get("expires") if on else None}}


def close(ids, all_tabs=False):
    if all_tabs:
        keep = http("/json/new?about:blank", "PUT")["id"]
        ids = [t["id"] for t in pages() if t["id"] != keep]
    done, missing = [], []
    for i in ids:
        try:
            http("/json/close/" + i)
            done.append(i)
        except urllib.error.HTTPError:
            missing.append(i)
    until(lambda: not set(done) & {t["id"] for t in pages()}, 3, 0.1)  # closing is asynchronous
    return {"closed": done, "not_found": missing, "tabs": len(pages())}


def stop():
    st = load(BJSON)
    stopped = unshare()["stopped"]
    for pid in {st.get("pid"), lock_pid()}:
        if pid and pid > 0 and kill(pid, "chrom", 15):
            stopped.append("chrome")
    stopped += [n for k, n in (("wm_pid", "openbox"), ("xvfb_pid", "Xvfb")) if kill(st.get(k), n)]
    rm(BJSON)
    return {"stopped": stopped, "running": cdp_ok()}


def novnc_dir():
    if os.path.isfile("/usr/share/novnc/vnc.html"):
        return "/usr/share/novnc"
    d = os.path.expanduser("~/.local/share/job-autopilot/novnc")
    if not os.path.isfile(os.path.join(d, "vnc.html")):
        with urllib.request.urlopen(NOVNC_URL, timeout=60) as r, tarfile.open(fileobj=io.BytesIO(r.read())) as t:
            ms = [m for m in t.getmembers() if "/" in m.name]
            for m in ms:
                m.name = m.name.split("/", 1)[1]
            kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            t.extractall(d, members=[m for m in ms if m.name], **kw)
    return d


def public_ok(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=10) as r:
            return r.status == 200 and b"noVNC" in r.read()
    except (OSError, ValueError):
        return False


def tunnel_url(pid):
    """The quick-tunnel URL from cloudflared's log; "" while waiting, None if cloudflared died."""
    with open(os.path.join(LOGS, "cloudflared.log"), errors="replace") as f:
        urls = [u for u in re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read()) if "//api." not in u]
    return urls[0] if urls else ("" if alive(pid, "cloudflared") else None)


def share(minutes):
    sh = load(SJSON)
    if share_alive(sh):
        return {"url": sh["url"], "expires": sh["expires"]}
    unshare()
    start()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # campaign folder
    exe = {"x11vnc": tool("x11vnc"), "cloudflared": tool("cloudflared", "~/.local/bin/cloudflared"),
           "websockify": tool("websockify", ".venv/bin/websockify", os.path.join(root, ".venv/bin/websockify"))}
    if not all(exe.values()):
        raise Fail(f"Not installed: {[k for k, v in exe.items() if not v]} (run scripts/setup.sh); no remote view")
    for p in (VNC, WS):
        if reach(p):
            raise Fail(f"Port {p} is already in use by another program")
    web = novnc_dir()
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))  # VNC max is 8
    private(PWFILE, pw + "\n")  # x11vnc deletes it after reading ("rm:")
    sh = {"started": iso(time.time())}
    try:
        sh["x11vnc"] = spawn([exe["x11vnc"], "-display", DISPLAY, "-rfbport", str(VNC), "-localhost", "-passwdfile",
                              "rm:" + PWFILE, "-shared", "-forever", "-noxdamage", "-quiet"], "x11vnc.log")
        if not until(lambda: reach(VNC), 15):
            raise Fail(f"x11vnc did not start; see {LOGS}/x11vnc.log")
        sh["websockify"] = spawn([exe["websockify"], "--web", web, f"127.0.0.1:{WS}", f"127.0.0.1:{VNC}"],
                                 "websockify.log")
        if not until(lambda: reach(WS), 15):
            raise Fail(f"websockify did not start; see {LOGS}/websockify.log")
        empty = os.path.join(STATE, "cloudflared-empty.yml")  # ignore any named-tunnel config on this machine
        private(empty, "")
        sh["cloudflared"] = spawn([exe["cloudflared"], "tunnel", "--config", empty, "--no-autoupdate", "--url",
                                   f"http://127.0.0.1:{WS}"], "cloudflared.log", mode="wb")
        until(lambda: tunnel_url(sh["cloudflared"]) != "", 60, 0.5)
        base = tunnel_url(sh["cloudflared"])
        if not base:
            raise Fail(f"cloudflared gave no trycloudflare.com URL; see {LOGS}/cloudflared.log")
        if not until(lambda: public_ok(base + "/vnc.html"), 120, 3):
            raise Fail("The tunnel URL never served noVNC from the public side; try share again later")
    except BaseException:
        for k, n in SHARE_PROCS[1:]:
            kill(sh.get(k), n)
        rm(PWFILE)
        raise
    exp = time.time() + minutes * 60
    sh.update(url=f"{base}/vnc.html?autoconnect=1&resize=scale&password={pw}", expires=iso(exp), expires_ts=exp)
    save(SJSON, sh)
    sh["watchdog"] = spawn([sys.executable, os.path.abspath(__file__), "_watchdog"], "watchdog.log")
    save(SJSON, sh)
    return {"url": sh["url"], "expires": sh["expires"]}


def unshare():
    sh = load(SJSON)
    stopped = [k for k, n in SHARE_PROCS if kill(sh.get(k), n)]
    rm(SJSON, PWFILE)
    return {"unshared": True, "stopped": stopped}


def watchdog():
    """Detached: unshare at expiry, or when any share process dies."""
    started = load(SJSON).get("started")
    while load(SJSON).get("started") == started:  # stop watching if a newer share replaced ours
        if not share_alive(load(SJSON)):
            unshare()
            return
        time.sleep(10)


# ---- Playwright helpers (Playwright is only imported by the caller) ----

def connect(playwright):
    """(browser, context) on the shared Chrome. Finish with browser.close(): disconnects, Chrome stays up."""
    if not cdp_ok():
        start()
    b = playwright.chromium.connect_over_cdp(CDP)
    return b, b.contexts[0]


def _target_id(context, page):
    s = context.new_cdp_session(page)
    try:
        return s.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        s.detach()


def _open(context, url, **opts):
    seen, cdp = set(context.pages), context.browser.new_browser_cdp_session()
    tid = cdp.send("Target.createTarget", dict(url=url, **opts))["targetId"]
    end = time.time() + 20
    while time.time() < end:
        for p in [p for p in context.pages if p not in seen]:
            seen.add(p)
            if _target_id(context, p) == tid:  # other agents' new tabs show up here too
                return p, cdp, tid
        with contextlib.suppress(Exception):
            context.wait_for_event("page", timeout=300)
    cdp.detach()
    raise Fail("New tab did not appear in Playwright")


def new_tab(context, url="about:blank", background=True):
    """Open url in a new tab of the shared window without stealing focus. Returns the Page."""
    page, cdp, _ = _open(context, url, background=background)
    cdp.detach()
    return page


def handoff_tab(context, url_or_page):
    """For the owner: a URL opens in its own new maximised window in front; a Page is activated and raised
    where it is (CDP can't move a live tab to another window, and reopening would lose its state)."""
    if isinstance(url_or_page, str):
        page, cdp, tid = _open(context, url_or_page, newWindow=True)
    else:
        page, cdp = url_or_page, context.browser.new_browser_cdp_session()
        tid = _target_id(context, page)
    try:
        win = cdp.send("Browser.getWindowForTarget", {"targetId": tid})["windowId"]
        for b in ({"left": 0, "top": 0, "width": W, "height": H}, {"windowState": "maximized"}):
            with contextlib.suppress(Exception):  # maximise needs a window manager; bounds cover its absence
                cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": b})
    finally:
        cdp.detach()
    page.bring_to_front()
    return page


def main(a):
    cmd = a[0] if a else None
    if cmd == "tabs":
        out([{"id": t["id"], "title": t.get("title", "")[:100], "url": re.split(r"[?#]", t.get("url", ""))[0]}
             for t in pages()])
    if cmd == "close" and len(a) > 1:
        out(close([x for x in a[1:] if x != "--all"], "--all" in a))
    if cmd == "share":
        m = a[a.index("--minutes") + 1] if "--minutes" in a[:-1] else "60"
        if not m.isdigit() or int(m) < 1:
            out({"error": "--minutes must be a positive whole number"}, 2)
        out(share(int(m)))
    if cmd == "_watchdog":
        sys.exit(watchdog())
    if cmd in ("start", "status", "stop", "unshare"):
        out(globals()[cmd]())
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Fail as e:
        out({"error": str(e)}, 1)
