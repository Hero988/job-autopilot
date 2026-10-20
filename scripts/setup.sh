#!/usr/bin/env bash
# setup.sh - prepare this machine and the current folder for a job-search campaign. Safe to re-run.
#
#   bash .autopilot/scripts/setup.sh          set up the campaign folder (the current directory) and check the machine
#   bash .autopilot/scripts/setup.sh mail     enter the mailbox address and Gmail app password (asked privately)
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
  echo "App passwords), not your normal password. IMAP is on by default for Gmail."
  cur_user="$(grep -s '^MAIL_USER=' "$MAILENV" | cut -d= -f2-)"
  read -r -p "Gmail address [${cur_user:-ameer1111888@gmail.com}]: " user
  user="${user:-${cur_user:-ameer1111888@gmail.com}}"
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
  echo "  installing:$need_apt"; $SUDO apt-get install -y -q $need_apt >/dev/null 2>&1 && MISSING=0
fi
[ -n "$need_apt" ] && [ -z "$SUDO" ] && echo "  -> ask the owner to run: sudo apt-get install -y$need_apt"
if command -v websockify >/dev/null || [ -x .venv/bin/websockify ]; then ok "websockify"; else miss "websockify"; fi
if [ -d /usr/share/novnc ] || [ -d "$HOME/.local/share/job-autopilot/novnc" ]; then ok "noVNC"; else
  mkdir -p "$HOME/.local/share/job-autopilot" && curl -fsSL https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz \
    | tar -xz -C "$HOME/.local/share/job-autopilot" && mv "$HOME/.local/share/job-autopilot/noVNC-1.5.0" \
    "$HOME/.local/share/job-autopilot/novnc" && ok "noVNC downloaded" || miss "noVNC"; fi
if command -v cloudflared >/dev/null || [ -x "$HOME/.local/bin/cloudflared" ]; then ok "cloudflared"; else
  arch="$(uname -m)"; case "$arch" in x86_64) a=amd64;; aarch64|arm64) a=arm64;; *) a="$arch";; esac
  mkdir -p "$HOME/.local/bin" && curl -fsSL -o "$HOME/.local/bin/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$a" \
    && chmod +x "$HOME/.local/bin/cloudflared" && ok "cloudflared downloaded to ~/.local/bin" || miss "cloudflared"; fi

echo "== Mailbox"
if [ ! -s "$MAILENV" ]; then
  umask 077; printf '# job-autopilot mailbox. Fill in with: bash .autopilot/scripts/setup.sh mail\nMAIL_USER=\nMAIL_APP_PASSWORD=\n' > "$MAILENV"
fi
if grep -q '^MAIL_APP_PASSWORD=..*' "$MAILENV"; then
  python3 "$REPO/scripts/mail.py" check >/dev/null 2>&1 && ok "mailbox login works ($(grep '^MAIL_USER=' "$MAILENV" | cut -d= -f2-))" \
    || miss "mailbox login FAILED: check the app password with: bash .autopilot/scripts/setup.sh mail"
else
  miss "mail password: the owner runs 'bash .autopilot/scripts/setup.sh mail' once (or gives it in chat)"
fi
[ -s "$CONF/PROFILE.md" ] || echo "  note: no $CONF/PROFILE.md yet (it is created as the campaign learns answers)"

echo "== Done. $( [ "$MISSING" = 0 ] && echo 'Everything is ready.' || echo 'Fix the MISSING items above (others can proceed).')"
echo "   Repo commit: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
