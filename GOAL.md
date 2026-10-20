# Campaign prompt

1. Make an empty folder for the job search and open your agent harness in it (Claude Code, Codex,
   Cursor, OpenCode, T3 Code, anything that can run shell commands).
2. Edit the `MY SEARCH` lines (any job, any field), then paste everything inside the block.

The first time on a machine, the agent asks you to connect the mailbox once: `setup.sh mail` with a
Google App password, or `setup.sh google` (Gmail API) if your account has no App passwords (see the README).
Everything else it does itself.

```text
/goal Run my job search from this folder until I get a job offer. Find and apply to as many matching jobs as you can every day, create the site accounts you need, track every application, and manage my mailbox fully. Done only when an email offers me a job: then stop applying, tell me, and end with the STATUS line from STATE.md showing OFFER.
No /goal command? Treat the line above as your standing objective and keep working without waiting for me.

Setup: if AGENTS.md is missing here, run `git clone --depth 1 https://github.com/Hero988/job-autopilot .autopilot`, then `bash .autopilot/scripts/setup.sh`. AGENTS.md in this folder is the method: follow it, and re-read it whenever you are unsure what you were doing.

Ask me only for what only I can do or know (a fact not in my CV or profile, a CAPTCHA, an SMS code, approving a reply). Keep working while you wait for my answer. Never reply to an email without my approval.

MY SEARCH
- Job I want: 100% remote, fully work-from-home customer service job in the UK.
- Similar to: the remote customer service job at <company> I applied for (find it in my email, or: <link>); I want jobs very similar to it.
- My CV: attached to this message (or: a file path / a link / "the latest CV in my email").
- Mailbox: you@gmail.com
```
