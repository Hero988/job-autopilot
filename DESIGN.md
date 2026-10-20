# Design notes

Why job-autopilot is built the way it is. Not read by campaigns.

## Inputs
1. **The ChatGPT run** (shared chat "Resume job search monitoring", 19–20 October 2026). A cloud
   browser, the Gmail connector and two scheduled automations made about 7 real applications in a
   day against a target of 40–50. On the next day it made none.
2. **The bug-bounty work** (`Hero988/bounty-playbook`, `bounty-notes`). Its first two repos died of
   complexity: 32,000 lines of code, 28,000 lines of tests, and 43% of agent effort spent on the tool
   itself. The rebuild that worked is a short method file, two small helper scripts, owner asks that
   never block, and plus-address aliases read over IMAP.

## What went wrong in the ChatGPT run, and the fix here
| Problem | Fix |
|---|---|
| Stopped whenever the current batch was blocked ("no further suitable unblocked vacancy"), and needed "please continue" four times. | The loop has no end but an offer. A blocked item is never a reason to stop. The goal prompt keeps the agent going; no keep-alive script. |
| Asked about the same facts more than once; answers lived only inside a scheduled-task prompt. | `PROFILE.md` Standard answers, copied to `~/.config/job-autopilot/PROFILE.md` for later campaigns. |
| Counted three email applications that had bounced (550 recipient rejected). | `mail.py bounces` after every email application. A bounce sets FAILED. |
| Could not hand over CAPTCHAs: the cloud browser's takeover was rejected repeatedly. | One local Chrome, and `browser.py share` makes a link that opens it from any computer. The agent keeps working in background tabs. |
| Required proof that training was remote before applying, which emptied the queue. | Judge on the advert: "remote" with no office, training or travel requirement counts as a match. Doubt becomes a note, not a skip. |
| Rechecked closed or ineligible adverts. | SKIPPED rows with a one-line reason; `track.py add` refuses duplicates; `jobs.py --new` hides tracked jobs. |
| Spent long stretches on one broken form. | Two methods, then FAILED with the reason, then the next job. |
| No safe way to run things in parallel. | `track.py claim` uses a file lock, so helpers can each take a job and their own tabs. |
| Counts drifted between days and sessions. | `applications.csv` with timestamps; `track.py today` uses UK dates. |

What it did well is kept: truthful knockout answers, reading the full advert, proof for every
submission, notifying the owner once per interview or assessment, and never replying or booking for
the owner.

## Choices
- **Local Chrome, not Browserbase.** One persistent profile under `~/.local/state/job-autopilot/`
  keeps every login. Headful Chrome in Xvfb looks like a normal browser, and it costs nothing. The
  agent and its helpers attach over CDP (Playwright `connect_over_cdp`). New tabs open in the
  background so they don't disturb a handoff.
- **Remote access: noVNC, then websockify, then a Cloudflare quick tunnel.** It needs no account,
  and the URL is random and https. The VNC password goes in the link and the share expires (60
  minutes by default). It is the same model as Browserbase Live View: the link gives full control,
  so it only ever goes into an owner ask.
- **IMAP/SMTP with an app password, not a connector.** It works in every harness. It can do all the
  management the owner wants (labels, archive, trash, drafts, send) and it shows codes that
  connectors hide. Special folders are found by flag, because UK accounts use `[Google Mail]/`.
- **Aliases.** Gmail plus-addressing: `ameer1111888+<site>@gmail.com` reaches the same inbox, keeps
  each site's mail separate, and shows which site leaked an address. If a site rejects `+`, the
  agent uses the plain address.
- **Owner asks use the harness's own chat** (the owner's choice), not a separate tool. Built-in
  question tools that pause the run are avoided: a plain chat message keeps the agent working.
  Open questions are listed in `STATE.md`. An optional ntfy ping tells the owner to look.
- **Replies are guarded twice:** in the method, and in `mail.py`, where `reply` needs `--approved`
  with the owner's words and `send` refuses `Re:` subjects.
- **Size caps:** the method is under 1,400 words, the goal block under 2,000 characters, and each
  script is one stdlib file.

## Known limits
- Sites with heavy bot defences (Cloudflare Turnstile loops, Workday's per-employer accounts, LinkedIn)
  will sometimes need the owner. LinkedIn's terms forbid automation, so the guides keep it to
  occasional, human-paced use.
- Cloudflare quick tunnels have no uptime guarantee. If a link dies, run `share` again.
- Throughput depends on how many matching vacancies exist. The agent never relaxes `TARGET.md` to
  raise its count.
