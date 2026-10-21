# Browser: one shared Chrome

All web work happens in **one headful Google Chrome** on a virtual display. Its persistent profile
(`~/.local/state/job-autopilot/chrome-profile`) keeps site logins. Every agent connects to it over
CDP and works in its own tabs. The owner can watch and control it from any PC through a share link.

`scripts/browser.py` manages it. Run it as `python3 .autopilot/scripts/browser.py <command>`.

```bash
python3 .autopilot/scripts/browser.py start       # idempotent; safe to run before every task
python3 .autopilot/scripts/browser.py status      # running?, tabs, Chrome start time, restarts, share
python3 .autopilot/scripts/browser.py tabs        # id, title, url (query strings removed)
python3 .autopilot/scripts/browser.py close ID    # close a tab by id (works on frozen tabs too)
python3 .autopilot/scripts/browser.py share       # owner link (see Handoff); --minutes N, default 60
python3 .autopilot/scripts/browser.py unshare     # end the owner link
python3 .autopilot/scripts/browser.py stop        # everything down; only when the campaign stops
```

`start` detaches, so Chrome outlives your shell. Closing the last tab does not quit Chrome.

## Parallel use
Several workers use the browser **at the same time**, each in its own tabs. There are no turns
and no "sole browser worker".

## Connecting with Playwright
Run browser code with `./.venv/bin/python` (setup.sh installed Playwright there; system `python3`
has no Playwright). Do **not** run `playwright install`: you connect to the system Chrome and never
launch one.

```python
import sys
sys.path.insert(0, ".autopilot/scripts")
import browser
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b, ctx = browser.connect(p)          # starts Chrome if needed; ctx holds the saved logins
    page = browser.new_tab(ctx, "https://jobs.example.com/apply/123")   # background tab
    tid = browser.target_id(ctx, page)   # save this (e.g. in work/<ID>.tab)
    page.wait_for_load_state()
    page.fill("#email", "candidate@example.com")
    b.close()                            # disconnects only; Chrome and the tab stay

# a later script (a new process):
with sync_playwright() as p:
    b, ctx = browser.connect(p)
    page = browser.find_tab(ctx, tid)    # None if the tab is gone
    page.click("text=Continue")
    page.close()                         # close YOUR tab when the job is done
    b.close()
```

Rules (checked on this machine):
- **Each script is a new process.** A `Page` object dies with it. Save `browser.target_id(ctx, page)`
  and get the tab back with `browser.find_tab(ctx, id)`. **Never pick tabs by URL or title**:
  other agents have tabs on the same sites. Never close or navigate a tab you didn't open.
- **Disconnect with `b.close()`**: on a CDP connection it only disconnects. Never kill Chrome.
- **Open tabs with `browser.new_tab`, not `ctx.new_page()`.** `ctx.new_page()` and
  `new_tab(..., background=False)` jump to the front and take over the owner's view.
- **Background tabs are automatable** (fill, click, type). Some clicks take about 1.5 s longer;
  don't bring the tab forward. Screenshots of hidden tabs can time out: use
  `page.screenshot(path=..., timeout=15000)` once, and if it fails save
  `page.inner_text("body")` to a `.txt` file instead. Either is proof; don't retry in a loop.
- **Don't trust `document.visibilityState`.** Playwright makes every tab report `visible`.
- Never print cookies, storage state or tokens. Strip query strings from URLs you log.

## Handoff: when only the owner can act
CAPTCHAs, "are you human" checks, phone sign-ins and SMS codes typed on a page are the owner's.
They are **batched**: when one blocks a form, set the job WAITING with a `CHECK:` note, close the
tab and move on. When the owner is active (or in the daily summary), offer the whole batch in one
session:

1. `python3 .autopilot/scripts/browser.py share` prints `{"url": ..., "expires": ...}`. It takes
   about a minute. Running it again, even while the first call is starting, returns the same
   link. Use `--minutes` to cover the batch.
2. Open the checks one at a time with `page = browser.handoff_tab(ctx, url)` (its own maximised
   window, in front). A tab you already filled: `browser.handoff_tab(ctx, page)` raises it as is.
3. Ask the owner in one chat message: the link; the list of sites and what to do on each ("solve
   the check, then press Submit"); "this link gives full control of the job browser and expires
   at <expires>"; "reply 'done' when finished".
4. **Never write the link into any file** (STATE.md, JOURNAL.md, notes, commits, logs). The
   password is in it.
5. Keep applying in background tabs meanwhile. Don't navigate, reload or close handoff tabs.
6. When the owner replies, check each page, finish the job, close its tab, then `unshare`.

Ask at once, outside the batch, only for a time-critical code in a flow the owner started. The
share ends by itself after `--minutes`. If the link doesn't work (free quick tunnels can drop),
`unshare`, `share` again and send the new link.

## Never
- **Never solve or bypass a CAPTCHA or bot check automatically.** No solver services, no
  clicking the checkbox from code, no retry loops or new tabs to dodge a check.
- **Never launch a second browser**: no `p.chromium.launch()`, no other Chrome, no headless copy,
  no harness browser tool. Logins live only in the shared profile. Never write your own CDP client.
- Never leave your tabs open when you finish a job, and never close other agents' tabs.
- Never run `browser.py stop` while other agents may be working.

## When things go wrong
- **`connect` fails after 30 s with a list of tabs:** one tab is hung (frozen page or open dialog)
  and blocks every agent. If it is yours, `browser.py close ID`; otherwise tell the lead.
  `connect(p, timeout=...)` takes milliseconds.
- **Chrome restarted:** `start`/`connect` print a notice on stderr, and `status` shows the new
  start time and a restart count. The last tabs are reopened under **new ids**, so saved ids
  return `None` and filled forms may be empty: reopen the job from its URL. If you had clicked
  Submit, look for proof before redoing it: an acknowledgement email, the site's "my
  applications", or the success URL in Chrome's history:
  ```bash
  cp ~/.local/state/job-autopilot/chrome-profile/Default/History /tmp/h.sqlite   # read a copy
  python3 -c "import sqlite3; print(sqlite3.connect('/tmp/h.sqlite').execute(\"SELECT url FROM urls WHERE url LIKE '%example.com/apply%' ORDER BY last_visit_time DESC LIMIT 5\").fetchall())"
  ```
- **`start` fails:** read `~/.local/state/job-autopilot/logs/chrome.log` (or `xvfb.log`), run
  `browser.py stop`, then `start` again.
- **Signed out of a site:** sign in again in the shared Chrome; a human check goes to the batch.
- **`share` fails:** it prints the reason (logs as above). `unshare` also stops processes left by
  an interrupted share. If cloudflared gets no tunnel, retry in a few minutes.
