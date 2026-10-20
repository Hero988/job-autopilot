# Applying: one job, start to proof

Scripts: `python3 .autopilot/scripts/X.py`. One job per worker at a time, in its own tab.

## The routine
1. **Read the whole current advert**, on the employer's own site if it exists. Check every
   `TARGET.md` must-have against the advert text: remote terms (office days, training or induction
   on site, travel, "must live within X miles"), eligibility, pay, start date, required experience
   or languages. One fails: `track.py set ID SKIPPED --note "<reason>"`, stop.
2. **Scam check.** Skip on: any fee (training, DBS, equipment, "starter kit"); chat-app-only
   "interviews"; pay far above the work; "task" or reshipping jobs; cheques sent to you; a free-mail
   recruiter for a "company" job; bank or ID details asked before an offer.
3. `track.py claim ID --by <name>`. If it refuses, take another job.
4. Open your own tab with `browser.new_tab(context, url)`. Never touch other workers' tabs.
5. **Account or guest.** Guest if offered. Else `track.py acct get SITE`, or `track.py acct new SITE
   --url <login url>`; read the password from `secrets/accounts.json` in code, never print it.
6. **Fill truthfully from `PROFILE.md`** and the CV. Copy dates, titles and grades exactly.
7. **Cover letter and free text:** 120–200 words for this employer and role, mapping real CV facts
   to the advert's duties; add availability and home set-up from `PROFILE.md`. Save to
   `letters/<id>.txt`. No clichés, invented numbers or skills the CV lacks.
8. **Upload the CV** from `cv/` (the original file).
9. **Review page:** name, exact email, phone, country (UK, not a US default), CV file, every
   answer. Tick required data consent; untick marketing.
10. **Submit, then prove it:** success page -> `page.screenshot(path="evidence/<id>.png",
    full_page=True)`, or the acknowledgement via `mail.py search 'newer_than:1h "application"'`.
    No proof, not SUBMITTED: recheck mail in 30 minutes before deciding to retry or mark FAILED.
11. `track.py set ID SUBMITTED --method <ats|board|email> --email <address used> --confirmation
    "<page text or mail id>"`. Close your tab.

## Questions you can't answer
- **Unknown required field:** ask the owner once in chat (as in `AGENTS.md`, Owner), with
  the exact question and options. Add it under "Waiting on owner" in `STATE.md`
  (check there first; one question can serve many jobs), `track.py set ID WAITING --note "<question>"`,
  close the tab, move on. Never guess or pick a "close enough" option.
- **Knockout questions** (years of experience, a tool, a licence): the true answer, even if it
  ends the application; then SKIPPED with the reason.
- **Equality and diversity questions:** "Prefer not to say" unless `PROFILE.md` gives an answer.
- **Salary:** the expectation in `PROFILE.md`. If the owner accepts advertised pay, use the advertised
  figure or the bottom of the range; else ask. Match the unit (annual, hourly, digits only).
- **Dates:** UK forms DD/MM/YYYY; Workday MM/YYYY spinners. Read the value back after typing.
- **Phone:** with a separate country dropdown pick United Kingdom (+44) and enter `7xxx xxxxxx`
  (no leading 0); a single field takes `+44 7xxx xxxxxx` or `07xxx xxxxxx`.
- **Address autocomplete:** type the postcode slowly, wait for suggestions, pick one (ArrowDown,
  Enter), then check town and postcode filled. Plain typing leaves hidden fields empty. County
  blank unless `PROFILE.md` has one.
- **Multi-page forms:** after each "Next", read the error summary and fix every red field.

## Playwright tips
- File upload: `page.locator("input[type=file]").first.set_input_files("cv/CV.pdf")` works on
  hidden inputs too. For upload buttons that open a dialog, use
  `with page.expect_file_chooser() as fc: page.click(...)`, then `fc.value.set_files(...)`.
- Custom dropdowns (React-select, Workday): click the control, type, wait for the option, then click
  it. Read the shown value back. For forms inside an iframe (iCIMS) use `page.frame_locator(...)`.
- Prefer `get_by_label`/`get_by_role` over brittle CSS.
- Pace like a person: small typing delays, a few seconds between actions, a pause between
  applications on the same board. Hammering a site gets accounts blocked.

## Two methods, then move on
If a form breaks, try two different methods (reload and refill; employer site instead of board;
keyboard instead of clicks; the published email route). Both fail: `track.py set ID FAILED --note
"<what broke>"`, next job. One form never eats the run.

## Email applications
Only to a recruitment address published in the advert, never a guessed one: `mail.py send --to <addr> --subject "Application: <role> (<ref>)" --body-file
letters/<id>.txt --attach cv/<file>`. Run `mail.py bounces --hours 2` an hour later and on the next
sweep; a bounce means FAILED. (A previous agent lost 3 of 10 "sent" applications to bounces.)

## Accounts and checks
- Default login is the plus alias that `acct new` creates (`local+site@gmail.com`). If a site
  rejects "+", rerun with `--email <plain address>`.
- Email verification: `mail.py codes --minutes 15 --to <alias>`; open links in your own tab.
- **SMS codes, CAPTCHAs, "verify you are human" loops, identity checks (ID photo, selfie) and
  personal assessments go to the owner.** Prepare everything else, put the tab in front with
  `browser.handoff_tab`, run `browser.py share`, and send the link to the owner in chat ("Please
  complete the check on <site>, reply done"). Never write the link to a file. Note it in `STATE.md`,
  set WAITING, keep working in background tabs; `browser.py unshare` when done. Never use
  CAPTCHA solvers or tricks. A check that passes by itself is fine; repeated blocks: move on.

## ATS field guide
- **Workday** (`*.myworkdayjobs.com`): every employer is a separate tenant, so create one account
  each (`acct new workday-<employer>`). Password rules vary: use a long mixed one. Five bad
  logins lock you out for 30 minutes. Choose "Autofill with Resume", then fix the parse on My
  Experience (duplicate or garbled jobs, wrong dates). Pages run My Information, My Experience,
  Application Questions, Voluntary Disclosures, Self Identify, Review. "How did you hear" is a
  two-level menu. Dropdowns need a click, then an option click. Use `data-automation-id`
  selectors. You can't edit after submitting.
- **Greenhouse** (`job-boards.greenhouse.io`, `boards.greenhouse.io`, `?gh_jid=` embeds): guest
  form. Invisible reCAPTCHA may email a code before submit: `mail.py codes`.
  MyGreenhouse (`my.greenhouse.io`) is one passwordless account for all employers.
- **Lever** (`jobs.lever.co/<co>/<id>/apply`): one page, guest; occasional hCaptcha.
- **Ashby** (`jobs.ashbyhq.com`): guest, autofills from the CV, so check every field. Strict spam
  settings can drop rushed applications silently, so go at human pace.
- **SmartRecruiters** (`jobs.smartrecruiters.com`): "I'm interested" opens a one-page flow, guest.
  Consent step is mandatory. CAPTCHA appears on some employers.
- **Workable** (`apply.workable.com/<co>/j/<code>/`): guest; Cloudflare may challenge.
- **Teamtailor** (`*.teamtailor.com`): guest. "Connect" is a talent pool, not an application.
- **BambooHR** (`*.bamboohr.com/careers`), **Pinpoint** (`*.pinpointhq.com`), **Breezy**
  (`*.breezy.hr`), **JazzHR** (`*.applytojob.com`), **Recruitee** (`*.recruitee.com`), **Personio**
  (`*.jobs.personio.com`/`.de`): one-page guest forms; read all custom questions first.
- **iCIMS** (`careers-<co>.icims.com`): account per employer, form in an iframe, many pages.
- **Oracle Recruiting Cloud / Taleo** (`*.oraclecloud.com/hcmUI/CandidateExperience`): sign in
  by email, then a 6-digit one-time code from `mail.py codes`. Returning users may be asked for
  date of birth or surname. Old Taleo (`*.taleo.net`) needs an account per employer.
- **SAP SuccessFactors** (`career*.successfactors.eu`, branded sites): account per employer,
  long profile. Upload the CV and fill the profile before the application.
- **Tribepad, Talos360, Eploy** (UK, on the employer's domain): account per employer, "save and
  continue later", often screening questions first.
- **SurveyMonkey/Google/Microsoft Forms:** only if the employer's own page links them.

## UK job boards
- **Indeed** (`uk.indeed.com`): one account. "Apply now" on Indeed is Indeed Apply; "Apply on
  company site" means go to the employer. Login by email code, sometimes phone
  verification (owner). Strong bot defences; terms forbid automation: human pace, moderate
  volume, employer site when one exists.
- **Reed** (`reed.co.uk`): account with a stored CV. Easy apply sends the stored CV, so first make
  sure the stored CV is the current one. Many listings redirect to the employer's ATS.
- **Totaljobs, Jobsite** (both StepStone) and **CV-Library**: account, quick apply with the
  stored CV (keep it current). Many adverts are agencies'; the agency is the contact.
- **LinkedIn Easy Apply:** the User Agreement bans bots, and accounts get restricted. Use it
  rarely, at human pace, and only where the employer site isn't available. Never bulk-apply.
- **Guardian Jobs:** account; most adverts go to the employer's site.
- **DWP Find a job:** moving to `jobs.service.gov.uk` in 2026 (re-register; CVs don't transfer).
  Most adverts link out to the employer.
