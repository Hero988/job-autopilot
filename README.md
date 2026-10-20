# job-autopilot

An AI agent that runs your job search until you get an offer. You give it three things: the job you
want, a similar job (a link, or "find it in my email"), and your CV. It then:

- **finds** matching jobs every day (public job APIs, job boards, company career sites, job-alert emails);
- **applies** in one local Chrome, creating site accounts with alias addresses (`you+site@gmail.com`)
  and unique passwords, and filling forms truthfully from your CV and profile;
- **tracks** every application (`applications.csv`) and proves each one (success page or acknowledgement);
- **manages your mailbox** with a Gmail app password, or through the Gmail API if app passwords aren't
  available: labels, archives, trashes junk,
  reads codes, spots interviews, rejections and bounces. It never replies without your approval;
- **asks you** in your agent's chat only for what only you can do (an unknown fact, a CAPTCHA, an SMS
  code, approval of a reply), and keeps working while it waits;
- **lets you into its browser from any computer**: `browser.py share` prints a temporary https link
  (noVNC over a Cloudflare tunnel) for CAPTCHAs and sign-ins;
- **stops only when an email offers you a job**, and tells you at once.

It works in any agent harness that can run shell commands and reads `AGENTS.md` (Codex, Cursor,
OpenCode, Copilot CLI, T3 Code), and in Claude Code through `CLAUDE.md` → `@AGENTS.md`.

## Start
1. **Once per machine, connect the mailbox** (in any folder that has the repo at `.autopilot/`; the
   agent asks you if you skip this). Pick one:
   - **App password:** turn on 2-Step Verification, create a Google App password (Google Account →
     Security → App passwords), then run `bash .autopilot/scripts/setup.sh mail`.
   - **No App passwords on your account** ("not available for your account")? Use the Gmail API
     (free): create a "Desktop app" OAuth client in Google Cloud Console once (about 10 minutes), then
     run `bash .autopilot/scripts/setup.sh google`. Steps: [`guides/gmail-api.md`](guides/gmail-api.md).
   Either way the credentials are stored only in `~/.config/job-autopilot/mail.env` (mode 600).
2. **Each search:** make an empty folder, open your agent there, and paste the block in
   [`GOAL.md`](GOAL.md) with your three lines filled in. The agent clones this repo into `.autopilot/`,
   runs `setup.sh`, and takes it from there.

**Answering the agent:** reply in the chat; your harness may let you message it while it works. For
a CAPTCHA, open the link it sends (from any computer), solve the check, and reply "done". Optional:
put an [ntfy](https://ntfy.sh) topic in `~/.config/ntfy_topic_tt` and subscribe to it on your phone
to get a ping whenever it needs you.

## What's here
| Path | What |
|---|---|
| `templates/campaign/AGENTS.md` | **The method**: boundaries, first run, the loop, owner asks, the offer stop, resume. Copied into each campaign. |
| `GOAL.md` | The prompt to paste. |
| `guides/` | `search.md` (finding jobs), `applying.md` (forms, accounts, ATS tips), `email.md` (mailbox rules), `browser.md` (the shared Chrome and the remote link), `gmail-api.md` (connecting Gmail without an app password). |
| `templates/campaign/` | Campaign files: `STATE.md`, `JOURNAL.md`, `TARGET.md`, `PROFILE.md`, `applications.csv`. |
| `scripts/browser.py` | One local Chrome (Xvfb + CDP) shared by the agent and its helpers; `share` gives the remote link. |
| `scripts/mail.py` | Full mailbox management: search, show, codes, attachments, labels, archive, trash, send (new mail only), guarded reply, drafts, bounces. IMAP/SMTP with an app password, or the Gmail API with OAuth (`mail.py auth`). |
| `scripts/jobs.py` | Searches public job APIs and company ATS boards (Greenhouse, Lever, Ashby…), and hides jobs already tracked. |
| `scripts/track.py` | The application tracker (locked, so helpers can run in parallel) and site accounts with generated passwords. |
| `scripts/setup.sh` | Machine and folder setup (safe to re-run). `setup.sh mail` stores an app password; `setup.sh google` walks you through the Gmail API sign-in. |
| `DESIGN.md` | Why it is built this way, and lessons from the earlier ChatGPT run and the bug-bounty playbook. |

## Requirements
- Linux with `python3` (3.8+), `git`, Google Chrome or Chromium, `Xvfb`, `x11vnc`, `pdftotext`.
- `setup.sh` installs the rest where it can: Playwright (in `.venv`), websockify, noVNC, cloudflared.
- **Machine-local, never in git:**
  - `~/.config/job-autopilot/mail.env`: mailbox address, and the app password or Gmail API sign-in (refresh token);
  - `~/.config/job-autopilot/PROFILE.md`: answers carried between campaigns;
  - `~/.local/state/job-autopilot/`: the Chrome profile, which holds the site logins;
  - each campaign's `secrets/accounts.json`: site passwords.

## How this repo changes
Lessons from the bug-bounty playbook, whose earlier versions collapsed under their own rules:
- **Boundaries are absolute; the rest is judgment.** The campaign `AGENTS.md` is the only copy of
  the boundaries, the loop and the stop condition. Keep it under 1,400 words, and the `GOAL.md`
  block under 2,000 characters.
- **Fix one-offs with a message to the running agent, not a new rule.** Change the method only for
  a problem that recurs across campaigns, and prefer deleting text to adding it.
- **No new mechanisms** (scripts, states, required fields) unless the agent lacks a capability
  entirely, or for safety.
- **No tests, no CI.** The scripts are checked by running them.

## Use it responsibly
You are responsible for what the agent submits in your name. It is told never to invent facts, never
to solve CAPTCHAs or bypass site security, and never to reply to anyone without your approval, but
check its work. Some job boards (LinkedIn, Indeed) forbid automated use in their terms; the guides
keep the agent on employer sites where possible.

## Licence
MIT, see [LICENSE](LICENSE).
