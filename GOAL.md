# Campaign prompt

1. Make an empty folder for the job search and open your agent harness in it (Claude Code, Codex,
   Cursor, OpenCode, T3 Code, anything that can run shell commands).
2. Edit the three `MY SEARCH` lines, then paste everything inside the block.

The first time on a machine, the agent asks you to run `bash .autopilot/scripts/setup.sh mail` once
to enter the Gmail app password privately. Everything else it does itself.

```text
/goal Run my job search from this folder until I get a job offer. Find and apply to as many matching jobs as you can every day, create the site accounts you need, track every application, and manage my mailbox fully. Done only when an email offers me a job: then stop applying, tell me, and end with the STATUS line from STATE.md showing OFFER.
No /goal command? Treat the line above as your standing objective and keep working without waiting for me.

Setup: if AGENTS.md is missing here, run `gh repo clone Hero988/job-autopilot .autopilot -- --depth 1` (or `git clone https://github.com/Hero988/job-autopilot .autopilot`), then `bash .autopilot/scripts/setup.sh`. AGENTS.md in this folder is the method: follow it, and re-read it whenever you are unsure what you were doing.

Ask me only for what only I can do or know (a fact not in my CV or profile, a CAPTCHA, an SMS code, approving a reply). Keep working while you wait for my answer. Never reply to an email without my approval.

MY SEARCH
- Job I want: 100% remote, fully work-from-home customer service job in the UK.
- Similar to: the White Company remote customer service job I applied for (find it in my email); I want jobs very similar to it.
- My CV: the latest CV attached in my email (or a file path / link).
- Mailbox: ameer1111888@gmail.com
```
