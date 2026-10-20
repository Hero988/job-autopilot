# Gmail API: mailbox access without an App password

If Google says App passwords are "not available for your account", the agent can use the official
Gmail API instead. It is free and needs no billing account. Setup takes about 10 minutes, once.

In a terminal on the agent's machine, run `bash .autopilot/scripts/setup.sh google`. It walks you
through these steps one at a time. Open each link in your browser. At a step, type `help` for more
detail, or press Enter to go past a step you have already done. To start later in the list, run
`setup.sh google --skip-to N`.

## Steps
1. **Sign in** at <https://console.cloud.google.com/> with the **job mailbox account** (check the
   avatar at the top right).
2. **Create a project:** <https://console.cloud.google.com/projectcreate>. Name it `job-autopilot`
   and click **Create**. Then select it in the project picker at the top.
3. **Enable the Gmail API:** <https://console.cloud.google.com/apis/library/gmail.googleapis.com>.
   Check that the picker says job-autopilot, then click **Enable**.
4. **Branding:** <https://console.cloud.google.com/auth/branding>. Click **Get started** if you see
   it. Set App name to `job-autopilot` and User support email to the mailbox, then click **Next**.
   Choose Audience **External** and click **Next**. Set the contact email to the mailbox and click
   **Next**. Tick the policy box, then click **Continue** and **Create**.
5. **Audience:** <https://console.cloud.google.com/auth/audience>. Under **Test users**, click
   **Add users**, enter the mailbox, and click **Save**. Then click **Publish app** and **Confirm**
   so that the status shows **In production**. *Why:* while the app is in Testing, Google ends its
   sign-ins after 7 days. You don't need verification, and the 100-user limit doesn't matter for
   a single user.
6. **Client:** <https://console.cloud.google.com/auth/clients>. Click **Create client**, choose
   Application type **Desktop app**, enter the name `job-autopilot`, and click **Create**. Click
   **Download JSON** in the window that opens: the secret is shown only there. If your browser is on
   another computer, copy the **Client ID** and **Client secret** instead.
7. **Give it to setup:** enter the JSON file's path, for example `~/Downloads/client_secret_....json`.
   You can also pass the path as an argument: `setup.sh google FILE`. Otherwise, press Enter and
   paste the Client ID and secret. The secret stays hidden as you type.
8. **Sign in:** open the link that setup prints and choose the mailbox. On **"Google hasn't
   verified this app"**, click **Advanced**, then **Go to job-autopilot (unsafe)**. The warning is
   expected, because the app is your own. Tick full Gmail access if a box is shown, then click
   **Continue**. If the browser is on another computer, it ends on a page that **can't be reached**
   (`http://127.0.0.1...`). That is normal: copy the **whole address** from the address bar and
   paste it into the terminal. You can also open the link in the agent's shared browser
   (`browser.py share`).

Setup then saves the sign-in and runs `mail.py check` (`"backend": "api"`). The last line says
**SUCCESS**, or **FAILED** with what to do next.

## Problems
- **"Google sign-in expired or revoked":** make sure step 5 shows In production, then run
  `setup.sh google --skip-to 7`.
- **"Gmail API has not been used in project" or "disabled":** do step 3, with the right project
  selected.
- **"This app is blocked":** create the project with the mailbox account itself.

## Cost, revoking, security
- It is free, and the quota is far above one person's use. Gmail's normal sending limits apply.
- To revoke access, go to <https://myaccount.google.com/permissions>, choose job-autopilot, and
  click **Delete all connections**.
- The saved sign-in (a refresh token) **gives full control of the mailbox**. It is stored only in
  `~/.config/job-autopilot/mail.env` (mode 600) with the client secret. Short-lived access tokens
  are cached in `~/.local/state/job-autopilot/token.json` (mode 600). `mail.py` never prints them.
  Never share these files.
