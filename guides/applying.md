# Applying: one job, start to proof

Scripts: `python3 .autopilot/scripts/X.py`. One worker does the whole application in one sitting, in
its own tab. You are your own reviewer: no review, approval or release steps, no preflight packets, no
per-step scripts. The only records are the tracker row and one proof file.

## The routine
1. `track.py claim ID --by <name>`. If it refuses, take another job.
2. **Read the whole advert.** Any full copy with a working apply route is enough; use the employer's
   own route if the advert links one. Check every `TARGET.md` must-have: remote terms (office days,
   on-site training, travel, "must live within X miles"), eligibility, pay, start date, required
   experience or languages. **Scams:** fees; chat-app-only "interviews"; pay far above the work;
   "task" or reshipping jobs; cheques; a free-mail recruiter; bank or ID details before an offer.
   A fail: `track.py set ID SKIPPED --note "<criterion>: <reason>"`. Unreadable after two tries:
   SKIPPED.
3. **Your tab:** `page = browser.new_tab(ctx, url)`; save `browser.target_id(ctx, page)` and get it
   back in later scripts with `browser.find_tab(ctx, id)`. Never touch other tabs.
4. **Read the whole form before asking anything:** every page up to the review page. Collect all
   unknowns and ask them at once.
5. **Account or guest.** Guest if offered. Else `track.py acct get SITE`, or `track.py acct new SITE
   --url <login url>` (a plus alias, `you+site@gmail.com`). If the site rejects "+": `track.py acct
   new SITE --email <plain address>`. Read the password from `secrets/accounts.json` in code and fill
   it inside a `try` that re-raises without the value (timeout errors can echo typed text). Email
   codes and links: `mail.py codes --minutes 15 --to <alias>`.
6. **Answers from `PROFILE.md`** and the CV. Copy dates, titles and grades exactly.
7. **CV retitled for the role** (below), if PROFILE has that standing permission; else the original.
8. **Letter, only if asked** (a letter field, or an email application): 120–200 words for this
   employer and role, mapping real CV facts to the advert's duties. Save as `letters/<ID>.txt`. No
   clichés, invented numbers or skills the CV lacks. Truthful means no false claims, not a list of
   what the owner lacks.
9. **Review page:** name, exact email, phone, country (never trust a default: some ATSs pick the
   server's country, such as Austria), CV file, every answer. Untick marketing.
10. **Submit once, then prove it:** `page.screenshot(path="evidence/<ID>.png", timeout=15000)` of
    the success page; if it times out, save `page.inner_text("body")` to `evidence/<ID>.txt`. Or the
    acknowledgement email's id. One proof; save nothing else.
11. `track.py set ID SUBMITTED --method <ats|board|email> --email <address used> --confirmation
    "<page text or mail id>"`. Close your tab. Report one line.

**No proof:** never click Submit again. A stuck "Submitting…", a CAPTCHA after Submit or an
anti-spam "blocked" message often went through: look for the acknowledgement email (and the site's
"my applications") first. Nothing after 30 minutes: FAILED `outcome unknown: don't resubmit`.

## Questions
- **Unknown required fact:** check PROFILE first; its answers cover every employer. Use what follows
  from the CV or PROFILE (a finished degree: not a student; an employer missing from a complete CV:
  never worked there; county or borough: from the postcode). Still unknown: ask once in general form
  ("For any employer: ...?", with options), add it to `OWNER.md` (check there first), `track.py set
  ID WAITING --note "ASK: <question>"`, close the tab. Never guess. Ask whether the owner has an
  account (Apple ID, etc.), never for its identifier.
- **Owner answers:** one that rules the job out means SKIPPED. Not sure about a skill: No. A vague
  answer is final: use only what it supports.
- **Knockout questions:** the true answer, even if it ends the application; then SKIPPED.
- **Written questions** (motivation, "describe a time", scenarios in the form) are yours: truthful
  from CV and PROFILE facts, stories only from real CV roles, PROFILE's owner-approved wording reused.
- **Owner-only:** anything scored, timed, proctored, recorded (video, voice), personality-based, or
  marked "own words" or "unaided": WAITING `TEST: <what, deadline>`. An assessment or questionnaire
  the employer sends after you apply is a next stage (`email.md`), never yours to take.
- **Terms, privacy consents, accuracy declarations, background-check consents** needed to apply: tick
  them under PROFILE's standing permission; never ask per site. Skim linked policies only for fees,
  money or a contract (then SKIP, or `APPROVE:`). "No automated access" terms don't block one
  application at human pace. Standing permission not answered yet: WAITING `APPROVE: standing
  permission for site terms` (the one first-run question, never per site); the owner said no: SKIPPED
  `terms: owner declined`.
- **Diversity questions:** "Prefer not to say" unless PROFILE answers them.
- **Salary:** PROFILE's floor, or the advertised figure or bottom of the range if PROFILE says so.
  Match the unit and currency, digits only.
- **Dates:** the form's format (DD/MM/YYYY, MM/DD/YYYY, Workday MM/YYYY spinners); read it back.
- **Phone:** with a country dropdown, the national number without its leading 0; else the
  international form.
- **Address and location autocomplete:** type slowly, wait, pick a suggestion (ArrowDown, Enter, or
  click the one naming the right country); plain typing leaves hidden fields empty.
- **Multi-page forms:** after each "Next", fix every field the error summary names.

## WAITING and human checks
WAITING is an owner action only, its note starting `ASK:` (a fact), `CHECK:` (CAPTCHA, code or ID
check), `TEST:` (owner-only assessment) or `APPROVE:` (an approval). Unsure is a decision: apply or
SKIP.

**CAPTCHAs, "verify you are human" loops, SMS or phone codes, ID photos and selfies** are the
owner's. Set WAITING `CHECK: <site>, <what>, <login alias>`, close the tab (tabs die with any Chrome
restart) and move on. The lead offers the batch to the owner as one session; re-fill the form then.
Never use solvers or tricks; a check that passes by itself is fine. Don't create a government
identity account, or anything that texts the owner's phone, without an `APPROVE:` first.

## Retitled CV
Change only the headline or target-role line and the one profile sentence to name the advert's role;
history, dates, education and skills stay exactly as they are. The headline names the role sought,
never a job the owner held ("Target role: <title>"). Make it at upload time. If the owner supplied a
DOCX source, edit a copy and export it (`soffice --headless --convert-to pdf`). For a PDF, write one
reusable script, `work/retitle_cv.py`, with PyMuPDF (in `.venv` from setup.sh):
1. `page.search_for("<old headline>")` gives the rectangle; `page.get_text("dict")` gives that
   span's font, size, colour and origin.
2. `page.add_redact_annot(rect, fill=(1, 1, 1))`; `page.apply_redactions(images=0, graphics=0)`.
3. `page.insert_text(origin, new, fontsize=size, color=...)`, reusing the embedded font
   (`doc.extract_font(xref)`) only if `pymupdf.Font(fontbuffer=...).has_glyph()` holds for every
   character (subset fonts lack some), else `helv`. Shrink to fit the old width; wrap the profile
   sentence to the same width and line count.
4. Save `cv/<ID>-<slug>.pdf`. Check once: `diff <(pdftotext -layout original.pdf -) <(pdftotext
   -layout cv/<ID>-<slug>.pdf -)` shows only those lines, and the page count is unchanged.

No locks, manifests, hashes, renders or reviews. Boards that store one CV for quick apply: replace it
first.

## Two methods, then FAILED
If a form breaks, try two different methods (reload and refill; the employer's site instead of the
board; keyboard instead of clicks; the published email route). Both fail: `track.py set ID FAILED
--note "<what broke>"`, next job.

## Email applications
Only to a recruitment address published in the advert: `mail.py send --to <addr> --subject
"Application: <role> (<ref>)" --body-file letters/<ID>.txt --attach cv/<file>`. Run `mail.py
bounces --hours 2` an hour later; a bounce means FAILED.

## Playwright tips
- List the form once: `page.locator("input:not([type=hidden]), textarea, select").evaluate_all(...)`
  returning each label, required flag and value. Don't print whole page text.
- Upload: `set_input_files(path)` on `input[type=file]` (hidden ones too), or
  `page.expect_file_chooser()` around the button click.
- Custom dropdowns: click, type, wait for the option, click it, read the value back. Iframes:
  `page.frame_locator(...)`. Prefer `get_by_label`/`get_by_role` over CSS.
- Any "autofill from CV" can invent dates or rows: compare each row with the CV.
- Pace like a person; hammering a site gets accounts blocked.

## ATS field guide
- **Workday** (`*.myworkdayjobs.com`): account per employer (`acct new workday-<employer>`); five bad
  logins lock you out for 30 minutes. "Autofill with Resume", then fix My Experience (missing
  employers, wrong dates). Check the country; some tenants require a title. "How did you hear" is a
  two-level menu; use `data-automation-id` selectors. After email activation, open the job URL fresh
  and click Apply, then Sign In. No edits after submitting.
- **Greenhouse**: guest; may email a code before submit.
- **Lever**: one page, guest; "Current location" needs a picked suggestion; occasional hCaptcha.
- **Ashby**: guest; autofills from the CV, so check every field.
- **SmartRecruiters**: one-page guest flow; the country-code button and City combobox need picked
  options; consent is mandatory.
- **Workable, BambooHR, Pinpoint, Breezy, JazzHR, Recruitee, Personio, Teamtailor**: one-page guest
  forms; read all custom questions first; a CAPTCHA on Submit is common. Teamtailor "Connect" is a
  talent pool, not an application.
- **iCIMS**: account per employer, iframe, many pages. **Taleo**, **SuccessFactors**, **Tribepad**,
  **Eploy**: account per employer, profile or screening questions first. **Oracle Recruiting Cloud**:
  email sign-in, then a one-time code.

## Job boards (UK examples; other countries have equivalents)
- **Indeed**: "Apply on company site" means go to the employer. Strong bot defences: human pace.
- **Reed, Totaljobs, CV-Library, Apply4U**: quick apply sends the stored CV. Apply4U submits the
  moment you click Apply, so replace the stored CV before that click.
- **LinkedIn Easy Apply**: accounts get restricted; rarely, at human pace, only without an employer
  route.
