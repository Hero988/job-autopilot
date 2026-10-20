# Browser: one shared Chrome

All web work happens in **one headful Google Chrome** on a virtual display. Its persistent profile
(`~/.local/state/job-autopilot/chrome-profile`) keeps site logins. Every agent connects to it over
CDP and works in its own tabs. The owner can watch and control it from any PC through a share link.

`scripts/browser.py` manages it. Run it as `python3 .autopilot/scripts/browser.py <command>`.

```bash
python3 .autopilot/scripts/browser.py start       # idempotent; safe to run before every task
python3 .autopilot/scripts/browser.py status      # running?, tab count, share active + expiry
python3 .autopilot/scripts/browser.py tabs        # id, title, url (query strings removed)
python3 .autopilot/scripts/browser.py close ID    # close a tab you opened and lost track of
python3 .autopilot/scripts/browser.py share       # owner link (see Handoff); --minutes N, default 60
python3 .autopilot/scripts/browser.py unshare     # end the owner link
python3 .autopilot/scripts/browser.py stop        # everything down; only when the campaign stops
```

`start` detaches (Chrome outlives your shell) and recovers from a crash. `close --all` closes every
tab but one `about:blank`; use it only when no other agent is working.

## Connecting with Playwright
Install Playwright once in the campaign venv: `python3 -m venv .venv && .venv/bin/pip install
playwright`. Do **not** run `playwright install`: you connect to the system Chrome and never
launch one.

```python
import sys
sys.path.insert(0, ".autopilot/scripts")
import browser
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b, ctx = browser.connect(p)          # starts Chrome if needed; ctx holds the saved logins
    page = browser.new_tab(ctx, "https://jobs.example.com/apply/123")   # background tab
    page.wait_for_load_state()
    page.fill("#email", "candidate@example.com")
    page.click("text=Continue")
    page.screenshot(path="evidence/acme-step1.png")
    page.close()                         # close YOUR tabs, and only yours
    b.close()                            # disconnects only; Chrome keeps running
```

These rules were checked on this machine:
- **Disconnect with `b.close()`** (or by leaving the `with` block). On a CDP connection this only
  disconnects. Chrome and the other agents' tabs keep running. Never kill Chrome processes.
- **Open tabs with `browser.new_tab`, not `ctx.new_page()`.** `ctx.new_page()` and
  `new_tab(..., background=False)` jump to the front and take over the owner's view.
  `new_tab` opens the tab behind the active one.
- **Background tabs are fully automatable** (fill, click, type, screenshots). Some clicks take
  about 1.5 s longer because hidden tabs render slowly. That is normal; don't bring the tab forward.
- **Don't trust `document.visibilityState`.** Playwright makes every tab report `visible`.
- **Several agents can be connected at once.** Each one's `ctx.pages` also lists the other agents'
  tabs. Keep your own `Page` objects and never close or navigate a tab you didn't open.
- Never print cookies, storage state or tokens. Strip query strings from URLs you log.

## Handoff: when only the owner can act
Some steps need the owner: a CAPTCHA or "are you human" check, a sign-in that needs their phone,
an SMS code typed on the page. The procedure:

1. `python3 .autopilot/scripts/browser.py share` prints `{"url": ..., "expires": ...}`. This takes
   about a minute, because it waits until the link works from the public internet. Running it again
   while the share is active prints the same link.
2. Put the page in front: `page = browser.handoff_tab(ctx, url)` opens the URL in its own maximised
   window, in front. To hand over a tab you already filled, pass the `Page` instead:
   `browser.handoff_tab(ctx, page)`. It stays where it is (state kept) and is brought to the front.
3. Ask the owner in a normal chat message (the harness's built-in chat). Include:
   - the link;
   - which site and exactly what to do ("solve the check on the Acme application page, then
     press Submit");
   - "this link gives full control of the job browser and expires at <expires>";
   - "reply 'done' when finished".
4. **Never write the link into any file**: not STATE.md, JOURNAL.md, notes, commits or logs. The
   password is in the link. In STATE.md, note the open handoff under **"Handoff open"** with the
   site and tab title only.
5. Keep working on other applications in background tabs while you wait (`new_tab` doesn't
   disturb the owner's window). Don't navigate, reload or close the handoff tab.
6. When the owner replies "done", check the page (a signed-in page, a confirmation), carry on,
   close the handoff tab, run `browser.py unshare`, and clear "Handoff open" in STATE.md.

The share ends by itself after `--minutes` (default 60). If the owner says the link doesn't work,
run `unshare` and then `share` to get a new link, and send it in a new message. The tunnel is a free
Cloudflare quick tunnel with no uptime guarantee, so a link can occasionally drop.

## Never
- **Never solve or bypass a CAPTCHA or bot check automatically.** No solver services, no
  clicking the checkbox from code, no retry loops or new tabs to dodge a check. Only the owner
  solves one, through the handoff.
- **Never launch a second browser**: no `p.chromium.launch()`, no other Chrome or Chromium, no
  headless copy. **Never use the harness's own browser tool.** Logins live only in the shared
  profile.
- Never leave your tabs open when you finish a task, and never close other agents' tabs.
- Never put the share link anywhere except the chat message to the owner.
- Never run `browser.py stop` while other agents may be working.

## When things go wrong
- **`start` fails:** read `~/.local/state/job-autopilot/logs/chrome.log` (or `xvfb.log`), run
  `browser.py stop`, then `start` again.
- **`connect` fails:** run `browser.py status`; if `running` is false, run `start`.
- **Signed out of a site:** sign in again in the shared Chrome. If a human check appears, do a
  handoff.
- **`share` fails:** it prints the reason; logs are in `~/.local/state/job-autopilot/logs/`. If
  cloudflared gets no tunnel, retry in a few minutes and keep working meanwhile.
