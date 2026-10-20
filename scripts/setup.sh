#!/usr/bin/env bash
# setup.sh - prepare this machine and the current folder for a job-search campaign. Safe to re-run.
#
#   bash .autopilot/scripts/setup.sh          set up the campaign folder (the current directory) and check the machine
#   bash .autopilot/scripts/setup.sh mail     enter the mailbox address and Gmail app password (asked privately)
#   bash .autopilot/scripts/setup.sh google [CLIENT.json] [--skip-to N]
#                                             no App passwords on the account? sign in to the Gmail API instead (guided)
#
# The campaign folder gets the templates (never overwriting your files), cv/ letters/ evidence/ secrets/, and a
# Python venv (.venv) with Playwright. Machine-wide config lives in ~/.config/job-autopilot/ (never in git).
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$HOME/.config/job-autopilot"
MAILENV="${JOBS_MAIL_ENV:-$CONF/mail.env}"
mkdir -p "$CONF" && chmod 700 "$CONF"

ok()   { printf '  OK      %s\n' "$*"; }
miss() { printf '  MISSING %s\n' "$*"; MISSING=1; }
MISSING=0

if [ "${1:-}" = "mail" ]; then
  echo "Mailbox for the job agent. Use a Google App password (Google Account > Security > 2-Step Verification >"
  echo "App passwords), not your normal password. App passwords need 2-Step Verification turned on. If the page says"
  echo "\"The setting you are looking for is not available for your account\", press Ctrl-C and run instead:"
  echo "  bash $REPO/scripts/setup.sh google"
  cur_user="$(grep -s '^MAIL_USER=' "$MAILENV" | cut -d= -f2-)"
  read -r -p "Gmail address${cur_user:+ [$cur_user]}: " user
  user="${user:-$cur_user}"
  read -r -s -p "App password (16 letters, hidden): " pw; echo
  umask 077
  cat > "$MAILENV" <<EOF
# job-autopilot mailbox. Never commit or print this file.
MAIL_USER=$user
MAIL_APP_PASSWORD=$pw
EOF
  chmod 600 "$MAILENV"
  echo "Saved $MAILENV (mode 600). Checking login..."
  python3 "$REPO/scripts/mail.py" check
  exit $?
fi

if [ "${1:-}" = "google" ]; then
  shift; json=""; from=1
  while [ $# -gt 0 ]; do
    case "$1" in --skip-to) from="${2:-1}"; shift 2 ;; *) json="$1"; from=7; shift ;; esac
  done
  G="https://console.cloud.google.com"
  cur_user="$(grep -s '^MAIL_USER=' "$MAILENV" | cut -d= -f2-)"
  STEP=(""
    "Sign in to Google Cloud with the job mailbox account${cur_user:+ ($cur_user)}: $G/"
    "Create a project named job-autopilot: $G/projectcreate"
    "Enable the Gmail API (the project picker at the top must say job-autopilot): $G/apis/library/gmail.googleapis.com"
    "Google Auth Platform: Get started; App name job-autopilot; support email = the mailbox: $G/auth/branding"
    "Audience: External. Add the mailbox as a test user, then click Publish app (In production): $G/auth/audience"
    "Clients > Create client > Application type Desktop app > Create > Download JSON: $G/auth/clients")
  HELP=(""
    "Open the link in a browser signed in to the job mailbox (avatar at the top right; Switch account if needed).
    First visit: accept the Terms of Service. No billing account or payment is needed: the Gmail API is free."
    "Project name: job-autopilot. Leave Location as it is (No organization) and click Create. Wait for the
    notification, then select the project in the picker at the top of the page."
    "Check the picker at the top shows job-autopilot, then click Enable on the Gmail API page."
    "If it says \"Google Auth Platform not configured yet\", click Get started. App name: job-autopilot. User support
    email: the mailbox. Next. Audience: External. Next. Contact email: the mailbox. Next. Tick the User Data Policy,
    Continue, Create."
    "On Audience: under Test users click Add users, enter the mailbox, Save. Then under Publishing status click
    Publish app and Confirm, so it says In production. In Testing, Google ends the sign-in after 7 days and the agent
    loses the mailbox. Not verifying the app is fine for personal use: you will see an \"unverified app\" warning."
    "Click Create client. Application type: Desktop app. Name: job-autopilot. Create. In the window that opens, click
    Download JSON (the secret is shown only in that window). If this computer is not the one running the agent,
    copy the Client ID and Client secret instead; you can paste them in the next step.")
  echo "Gmail API sign-in for the job mailbox (for accounts without App passwords). About 10 minutes, free."
  echo "Guide: https://github.com/Hero988/job-autopilot/blob/main/guides/gmail-api.md (or $REPO/guides/gmail-api.md)."
  echo "Already done some steps? Press Enter to go past them, or start later with: setup.sh google --skip-to N"
  for n in 1 2 3 4 5 6; do
    [ "$n" -lt "$from" ] && continue
    printf '\n%s/8  %s\n' "$n" "${STEP[$n]}"
    while read -r -p "    Press Enter when done (or type 'help'): " ans; do
      [ "$ans" = "help" ] && { printf '    %s\n' "${HELP[$n]}"; continue; }
      break
    done
  done
  printf '\n7/8  Give setup the OAuth client.\n'
  if [ -z "$json" ]; then
    read -r -e -p "    Path to the downloaded JSON (Enter to paste the Client ID and secret instead): " json
  fi
  json="${json/#\~/$HOME}"
  if [ -z "$json" ]; then
    saved="$(grep -s '^MAIL_GOOGLE_CLIENT_ID=..*' "$MAILENV")"
    read -r -p "    Client ID${saved:+ (Enter keeps the saved one)}: " cid
    if [ -n "$cid" ]; then
      read -r -s -p "    Client secret (hidden): " csec; echo
      [ -n "$csec" ] || { echo "FAILED: no Client secret. Run: bash $REPO/scripts/setup.sh google --skip-to 7"; exit 2; }
      umask 077; touch "$MAILENV"
      { grep -v -E '^(export )?MAIL_GOOGLE_CLIENT_(ID|SECRET)=' "$MAILENV"
        printf 'MAIL_GOOGLE_CLIENT_ID=%s\nMAIL_GOOGLE_CLIENT_SECRET=%s\n' "$cid" "$csec"; } > "$MAILENV.tmp" \
        && mv "$MAILENV.tmp" "$MAILENV" && chmod 600 "$MAILENV"
    elif [ -z "$saved" ]; then
      echo "FAILED: no Client ID. Run: bash $REPO/scripts/setup.sh google --skip-to 7"; exit 2
    fi
  elif [ ! -f "$json" ]; then
    echo "FAILED: no file at $json. Run: bash $REPO/scripts/setup.sh google --skip-to 7"; exit 2
  fi
  printf '\n8/8  Sign in as the job mailbox and allow full Gmail access.\n'
  if python3 "$REPO/scripts/mail.py" auth ${json:+"$json"} && python3 "$REPO/scripts/mail.py" check; then
    echo "SUCCESS: the agent can manage $(grep '^MAIL_USER=' "$MAILENV" | cut -d= -f2-) through the Gmail API. Nothing else to do."
  else
    echo "FAILED: see the error above. Fix that step (type 'help' at it), then run: bash $REPO/scripts/setup.sh google --skip-to 7"
    exit 1
  fi
  exit 0
fi

echo "== Campaign folder: $PWD"
if [ "$PWD" = "$REPO" ]; then echo "Run this from your campaign folder, not from the repo itself."; exit 2; fi
for f in AGENTS.md CLAUDE.md STATE.md JOURNAL.md TARGET.md PROFILE.md applications.csv; do
  [ -e "$f" ] || { cp "$REPO/templates/campaign/$f" "$f" && ok "created $f"; }
done
[ -e .gitignore ] || cp "$REPO/templates/campaign/gitignore" .gitignore
if [ -s "$CONF/PROFILE.md" ] && cmp -s PROFILE.md "$REPO/templates/campaign/PROFILE.md"; then
  cp "$CONF/PROFILE.md" PROFILE.md && ok "PROFILE.md filled from $CONF/PROFILE.md (earlier campaigns)"
fi
mkdir -p cv letters evidence && mkdir -p -m 700 secrets && chmod 700 secrets

echo "== Python and Playwright"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv || miss "python3 venv (apt install python3-venv)"; fi
if [ -x .venv/bin/python ]; then
  .venv/bin/python -c "import playwright" 2>/dev/null || .venv/bin/pip install -q playwright websockify >/dev/null 2>&1
  .venv/bin/python -c "import playwright" 2>/dev/null && ok "playwright in .venv (uses system Chrome; no browser download)" \
    || miss "playwright (.venv/bin/pip install playwright)"
fi

echo "== Machine tools"
SUDO=""; command -v sudo >/dev/null && sudo -n true 2>/dev/null && SUDO="sudo -n"
need_apt=""
if command -v google-chrome >/dev/null || command -v chromium >/dev/null || command -v chromium-browser >/dev/null; then
  ok "Chrome/Chromium"; else miss "Chrome or Chromium"; need_apt="$need_apt chromium"; fi
command -v Xvfb >/dev/null && ok "Xvfb" || { miss "Xvfb"; need_apt="$need_apt xvfb"; }
command -v x11vnc >/dev/null && ok "x11vnc" || { miss "x11vnc"; need_apt="$need_apt x11vnc"; }
command -v pdftotext >/dev/null && ok "pdftotext" || { miss "pdftotext"; need_apt="$need_apt poppler-utils"; }
if [ -n "$need_apt" ] && [ -n "$SUDO" ] && command -v apt-get >/dev/null; then
  echo "  installing:$need_apt"; $SUDO apt-get install -y -q $need_apt >/dev/null 2>&1 && need_apt="" \
    && echo "  installed; re-run setup.sh to re-check"
fi
[ -n "$need_apt" ] && [ -z "$SUDO" ] && echo "  -> ask the owner to run: sudo apt-get install -y$need_apt"
if command -v websockify >/dev/null || [ -x .venv/bin/websockify ]; then ok "websockify"; else miss "websockify"; fi
if [ -d /usr/share/novnc ] || [ -d "$HOME/.local/share/job-autopilot/novnc" ]; then ok "noVNC"; else
  ok "noVNC (browser.py downloads it on the first share)"; fi
if command -v cloudflared >/dev/null || [ -x "$HOME/.local/bin/cloudflared" ]; then ok "cloudflared"; else
  arch="$(uname -m)"; case "$arch" in x86_64) a=amd64;; aarch64|arm64) a=arm64;; *) a="$arch";; esac
  mkdir -p "$HOME/.local/bin" && curl -fsSL -o "$HOME/.local/bin/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$a" \
    && chmod +x "$HOME/.local/bin/cloudflared" && ok "cloudflared downloaded to ~/.local/bin" || miss "cloudflared"; fi

echo "== Mailbox"
if [ ! -s "$MAILENV" ]; then
  umask 077; printf '# job-autopilot mailbox. Fill in with: bash .autopilot/scripts/setup.sh mail (or setup.sh google)\nMAIL_USER=\nMAIL_APP_PASSWORD=\n' > "$MAILENV"
fi
if grep -q '^MAIL_GOOGLE_REFRESH_TOKEN=..*' "$MAILENV"; then
  python3 "$REPO/scripts/mail.py" check >/dev/null 2>&1 && ok "mailbox works via the Gmail API ($(grep '^MAIL_USER=' "$MAILENV" | cut -d= -f2-))" \
    || miss "mailbox Gmail API FAILED: see 'python3 $REPO/scripts/mail.py check'; the owner reruns: bash $REPO/scripts/setup.sh google --skip-to 7"
elif grep -q '^MAIL_APP_PASSWORD=..*' "$MAILENV"; then
  python3 "$REPO/scripts/mail.py" check >/dev/null 2>&1 && ok "mailbox login works ($(grep '^MAIL_USER=' "$MAILENV" | cut -d= -f2-))" \
    || miss "mailbox login FAILED: check the app password with: bash $REPO/scripts/setup.sh mail"
else
  miss "mailbox access: the owner runs 'bash $REPO/scripts/setup.sh mail' once in a terminal (Gmail App password),"
  echo "          or 'bash $REPO/scripts/setup.sh google' (Gmail API) if the account has no App passwords"
fi
[ -s "$CONF/PROFILE.md" ] || echo "  note: no $CONF/PROFILE.md yet (it is created as the campaign learns answers)"

echo "== Done. $( [ "$MISSING" = 0 ] && echo 'Everything is ready.' || echo 'Fix the MISSING items above (others can proceed).')"
echo "   Repo commit: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
