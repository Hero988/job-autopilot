# Design notes

Why job-autopilot is built the way it is. Not read by campaigns.

## Inputs
1. **The ChatGPT run** (shared chat "Resume job search monitoring", 19–20 October 2026). A cloud
   browser, the Gmail connector and two scheduled automations made about 7 real applications in a
   day against a target of 40–50. On the next day it made none.
2. **An earlier agent playbook for bug-bounty research.** Its first two versions died of
   complexity: 32,000 lines of code, 28,000 lines of tests, and 43% of agent effort spent on the tool
   itself. The rebuild that worked is a short method file, two small helper scripts, owner asks that
   never block, and plus-address aliases read over IMAP.

## What went wrong in the ChatGPT run, and the fix here
| Problem | Fix |
|---|---|
| Stopped whenever the current batch was blocked ("no further suitable unblocked vacancy"), and needed "please continue" four times. | The loop has no end but an offer. A blocked item is never a reason to stop. The goal prompt keeps the agent going; no keep-alive script. |
| Asked about the same facts more than once; answers lived only inside a scheduled-task prompt. | `PROFILE.md` general answers, copied to `~/.config/job-autopilot/PROFILE.md` for later campaigns. |
| Counted three email applications that had bounced (550 recipient rejected). | `mail.py bounces` after every email application. A bounce sets FAILED. |
| Could not hand over CAPTCHAs: the cloud browser's takeover was rejected repeatedly. | One local Chrome, and `browser.py share` makes a link that opens it from any computer. The agent keeps working in background tabs. |
| Required proof that training was remote before applying, which emptied the queue. | Judge on the advert: "remote" with no office, training or travel requirement counts as a match. Doubt is not a reason to skip. |
| Rechecked closed or ineligible adverts. | SKIPPED rows with a one-line reason; `track.py add` refuses duplicates; `jobs.py --new` hides tracked jobs. |
| Spent long stretches on one broken form. | Two methods, then FAILED with the reason, then the next job. |
| No safe way to run things in parallel. | `track.py claim` uses a file lock, so helpers can each take a job and their own tabs. |
| Counts drifted between days and sessions. | `applications.csv` with timestamps; `track.py today` uses the owner's dates (`JOBS_TZ`, default Europe/London). |

What it did well is kept: truthful knockout answers, reading the full advert, proof for every
submission, notifying the owner once per interview or assessment, and never replying or booking for
the owner.

## The first full run of this repo (20–21 October 2026), and what changed
Setup: Codex in T3 Code, 34 hours, a lead agent with up to 8 helpers.

Results: 113 confirmed applications (62, then 51), against about 7 in the ChatGPT run, and no
duplicates. Truth held under pressure ("make something up", "solve this CAPTCHA"), and no secret
reached a file. Fourteen analysts read the transcripts and the folder. Most of the cost was
self-imposed ritual, not missing tools.

| Problem | Fix |
|---|---|
| "Never sign anything" made the agent ask the owner 12 times to approve standard site terms; he approved all 12, one after waiting 7 hours. | "Sign" means a contract. Site terms, consents and accuracy declarations are covered by one standing yes in the first-run batch. |
| Generic declarations were asked once per employer: relatives, past interviews, criminal record, CCJs, title, student status. | The PROFILE template lists the common fields, and first run asks every blank in one batch. Answers are saved as general answers. |
| 205 of 265 WAITING rows were the agent's own "source holds", not owner asks. | WAITING needs an `ASK:`/`CHECK:`/`TEST:`/`APPROVE:` note (`track.py` enforces it). Unsure means apply or SKIP, and any full advert copy will do. |
| One CAPTCHA handoff per form. The owner said "save them in a file", but filled forms sat open in tabs and were lost when Chrome restarted. | Human checks are batched. The tab is closed, and the batch is offered as one share session. |
| Helpers sat idle half the time: the lead micro-tasked them, approved every step, ran the browser "one worker at a time", and re-reviewed every CV. | A helper has a standing job, owns an application from claim to proof, and uses the browser in parallel. |
| Chrome quit three times; closing the last tab killed it, and every relaunch was silent. | Keep-alive and restore flags. `connect()` reports relaunches, has a timeout, and offers `target_id`/`find_tab`. |
| Notes averaged about 750 characters (tracker 2.5 MB), the journal reached 1.2 MB, STATE.md 42 KB, PROFILE.md 48 KB, and evidence 623 MB. Compaction came every 40 minutes, with a costly re-read each time. | Size caps: notes one line (≤300 characters, enforced), STATE ≤40 lines, journal lines ≤200 characters, evidence is proof only, scratch goes in `work/`. |
| The narrow target ran dry, and the team slept for an hour instead of saying so. | After an hour with no match, report the top skip reasons and propose ways to widen. |
| The owner's to-dos were spread over 48,000 words of guides; he asked twice for "a very simple message". | `OWNER.md`: one line per item, pasted when asked, in the daily summary and at stop. Next stages get `INTERVIEW` and a `next-steps/` file. |
| The stop order and per-site approvals went into PROFILE.md, which is copied to every future campaign. | PROFILE holds facts true for any job. Search steering goes in TARGET.md's Owner instructions. |
| `mail.py show` printed tracking links, which the agent took as a leak, so it wrote its own mail reader. | Links are shown without tracking or sign-in parameters, there is a Gmail rate-limit retry, `codes --to` for helpers, and `sweep`. Boundary 5 says reading secrets is fine and writing them is not. |
| CV tailoring (the owner asked for it) grew locks, manifests and lead reviews: 118 CVs, 45 of them never used. | Retitle only the headline and one profile line, check once, keep one reusable script. |
| An agent-written, 431-word scheduled-task prompt restated the method. | A wake-up prompt is one line. |

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
- **Gmail API as the alternative** for accounts where Google offers no App passwords. It is free,
  and it reuses the same message building and parsing, with labels per message. The OAuth app must
  be published, not left in Testing, because Testing refresh tokens expire after 7 days. An Apps
  Script bridge was considered and dropped as an extra moving part.
- **Aliases.** Gmail plus-addressing: `you+<site>@gmail.com` reaches the same inbox, keeps
  each site's mail separate, and shows which site leaked an address. If a site rejects `+`, the
  agent uses the plain address.
- **Owner asks use the harness's own chat** (the owner's choice), not a separate tool. Built-in
  question tools that pause the run are avoided: a plain chat message keeps the agent working.
  Open questions are listed in `OWNER.md`. An optional ntfy ping tells the owner to look.
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
