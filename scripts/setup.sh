#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
section() { echo; bold "== $* =="; echo; }

env_value() {
  local key="$1"
  if [[ ! -f .env ]]; then
    return 0
  fi
  local line
  line="$(grep -E "^${key}=" .env | tail -n 1 || true)"
  if [[ -z "$line" ]]; then
    return 0
  fi
  printf '%s' "${line#*=}"
}

write_env_values() {
  local json="$1"
  JSON_PAYLOAD="$json" uv run python - <<'PY'
import json
import os
from pathlib import Path

from pr_comment_codex_bot.env_file import update_env_values

updates = json.loads(os.environ["JSON_PAYLOAD"])
update_env_values(Path(".env"), updates)
PY
}

print_holder_guide() {
  cat <<'EOF'
Holder account = your repo admin identity.

It needs permission to:
  - create or update repository webhooks
  - invite the replier account as a collaborator

How to get holder credentials:
  1. Use the GitHub user that owns or admins your target repositories.
  2. Note the login name (example: acme-admin).

Token option (recommended when replier is a separate account):
  1. Open https://github.com/settings/tokens
  2. Create a classic token with "repo" scope
     or a fine-grained token with:
       - Repository administration (read/write)
       - Webhooks (read/write)
  3. Copy the token (starts with ghp_).

gh CLI option (easy if holder is your current `gh auth` user):
  1. Install GitHub CLI: https://cli.github.com/
  2. Run: gh auth login
  3. Choose the holder account when prompted
EOF
}

print_replier_guide() {
  cat <<'EOF'
Replier account = the bot identity that posts PR comments.

It needs permission to:
  - read pull requests and comments
  - post issue comments on PRs

How to get replier credentials:
  1. Create or pick a dedicated GitHub account for the bot.
  2. Note the login name (example: acme-codex-bot).

Token option (recommended):
  1. Log into the replier account in the browser.
  2. Open https://github.com/settings/tokens
  3. Create a classic token with "repo" scope
     or a fine-grained token with:
       - Pull requests (read/write)
       - Issues (read/write)
  4. Copy the token (starts with ghp_).

gh CLI option:
  Only works if `gh auth login` is currently authenticated as the replier.
  If your shell is logged in as the holder, use a token for the replier instead.
EOF
}

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local answer
  if [[ "$default" == "y" ]]; then
    read -r -p "$prompt [Y/n]: " answer
    answer="${answer:-y}"
  else
    read -r -p "$prompt [y/N]: " answer
    answer="${answer:-n}"
  fi
  [[ "$answer" =~ ^[Yy] ]]
}

prompt_login() {
  local label="$1"
  local current="$2"
  local value=""
  while [[ -z "$value" ]]; do
    if [[ -n "$current" ]]; then
      read -r -p "$label [$current]: " value
      value="${value:-$current}"
    else
      read -r -p "$label: " value
    fi
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    if [[ -z "$value" ]]; then
      echo "Login cannot be empty."
    fi
  done
  printf '%s' "$value"
}

prompt_auth_mode() {
  local account="$1"
  local choice=""
  while true; do
    echo
    echo "Choose $account authentication:"
    echo "  1) Personal access token"
    echo "  2) gh CLI (use current gh auth token)"
    read -r -p "Enter 1 or 2: " choice
    case "$choice" in
      1) printf 'token'; return 0 ;;
      2)
        if command -v gh >/dev/null 2>&1; then
          if gh auth status >/dev/null 2>&1; then
            printf 'gh_cli'
            return 0
          fi
          echo "gh is installed but not authenticated. Run: gh auth login"
        else
          echo "gh CLI not found. Install it from https://cli.github.com/ or choose token auth."
        fi
        ;;
      *) echo "Please enter 1 or 2." ;;
    esac
  done
}

prompt_token() {
  local label="$1"
  local token=""
  while [[ -z "$token" ]]; do
    read -r -s -p "$label: " token
    echo
    token="$(printf '%s' "$token" | tr -d '[:space:]')"
    if [[ -z "$token" ]]; then
      echo "Token cannot be empty."
    fi
  done
  printf '%s' "$token"
}

prompt_activation_phrase() {
  local current phrase updates
  current="$(env_value GITHUB_TRIGGER_PHRASE)"
  if [[ -z "$current" ]]; then
    current="codex"
  fi

  section "Activation phrase"
  cat <<'EOF'
The activation phrase decides which PR comments the bot responds to.

Press Enter to keep the default: codex
Type your own phrase to change it, for example: @codex-bot
Type NONE to trigger on every PR comment on watched repos.
EOF

  read -r -p "Activation phrase [$current] (type NONE for every PR comment): " phrase
  phrase="${phrase:-$current}"
  if [[ "$phrase" == "NONE" || "$phrase" == "none" ]]; then
    phrase=""
  fi

  updates="$(ACTIVATION_PHRASE="$phrase" uv run python - <<'PY'
import json
import os

print(json.dumps({"GITHUB_TRIGGER_PHRASE": os.environ["ACTIVATION_PHRASE"]}))
PY
)"
  write_env_values "$updates"
  if [[ -z "$phrase" ]]; then
    bold "Saved activation phrase: every PR comment on watched repos"
  else
    bold "Saved activation phrase: $phrase"
  fi
}

configure_accounts() {
  local holder_login holder_auth holder_token replier_login replier_auth replier_token
  local current_holder_login current_replier_login
  local updates

  current_holder_login="$(env_value GITHUB_HOLDER_LOGIN)"
  current_replier_login="$(env_value GITHUB_REPLIER_LOGIN)"
  if [[ -z "$current_replier_login" ]]; then
    current_replier_login="$(env_value GITHUB_BOT_LOGIN)"
  fi

  if [[ -n "$current_holder_login" && -n "$current_replier_login" ]]; then
    if ! prompt_yes_no "Holder and replier logins are already set in .env. Reconfigure them?"; then
      echo "Keeping existing account settings."
      return 0
    fi
  fi

  section "GitHub account setup"
  cat <<'EOF'
This bot uses two GitHub identities:

  Holder  - repo admin that invites the bot and manages webhooks
  Replier - bot account that posts PR comments

You can change these later in the dashboard or .env.
EOF

  section "Holder account"
  print_holder_guide
  holder_login="$(prompt_login "Holder GitHub login" "$current_holder_login")"
  holder_auth="$(prompt_auth_mode "holder")"
  if [[ "$holder_auth" == "token" ]]; then
    holder_token="$(prompt_token "Holder personal access token")"
  else
    holder_token=""
    bold "Using gh CLI token for holder account."
  fi

  section "Replier account"
  print_replier_guide
  replier_login="$(prompt_login "Replier GitHub login" "$current_replier_login")"
  replier_auth="$(prompt_auth_mode "replier")"
  if [[ "$replier_auth" == "token" ]]; then
    replier_token="$(prompt_token "Replier personal access token")"
  else
    replier_token=""
    bold "Using gh CLI token for replier account."
  fi

  if [[ "$holder_login" == "$replier_login" && "$holder_auth" == "gh_cli" && "$replier_auth" == "gh_cli" ]]; then
    echo
    echo "Note: holder and replier are the same login with gh CLI auth."
    echo "That is fine for solo development on repos you admin."
  fi

  updates="$(HOLDER_LOGIN="$holder_login" \
    REPLIER_LOGIN="$replier_login" \
    HOLDER_AUTH="$holder_auth" \
    REPLIER_AUTH="$replier_auth" \
    HOLDER_TOKEN="$holder_token" \
    REPLIER_TOKEN="$replier_token" \
    uv run python - <<'PY'
import json
import os

holder_auth = os.environ["HOLDER_AUTH"]
replier_auth = os.environ["REPLIER_AUTH"]
updates = {
    "GITHUB_HOLDER_LOGIN": os.environ["HOLDER_LOGIN"],
    "GITHUB_REPLIER_LOGIN": os.environ["REPLIER_LOGIN"],
    "GITHUB_HOLDER_USE_GH_CLI_TOKEN": "true" if holder_auth == "gh_cli" else "false",
    "GITHUB_REPLIER_USE_GH_CLI_TOKEN": "true" if replier_auth == "gh_cli" else "false",
}
if holder_auth == "token":
    updates["GITHUB_HOLDER_TOKEN"] = os.environ["HOLDER_TOKEN"]
else:
    updates["GITHUB_HOLDER_TOKEN"] = None
if replier_auth == "token":
    updates["GITHUB_REPLIER_TOKEN"] = os.environ["REPLIER_TOKEN"]
else:
    updates["GITHUB_REPLIER_TOKEN"] = None
print(json.dumps(updates))
PY
)"

  write_env_values "$updates"
  echo
  bold "Saved holder and replier settings to .env"
}

section "Installing dependencies"

if command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared already installed: $(command -v cloudflared)"
else
  echo "Installing cloudflared..."
  if command -v brew >/dev/null 2>&1; then
    brew install cloudflared
  else
    echo "Homebrew not found. Install cloudflared manually:"
    echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv sync
else
  echo "uv not found. Install it from https://docs.astral.sh/uv/"
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

if [[ -t 0 ]]; then
  configure_accounts
  prompt_activation_phrase
else
  echo
  echo "Non-interactive shell detected. Skipping account prompts."
  echo "Configure holder/replier and activation phrase in the dashboard or edit .env manually."
fi

section "Setup complete"
echo "Next steps:"
echo "  1. Run: make start"
echo "  2. Open: http://127.0.0.1:8088/"
echo "  3. Add a watched repo under GitHub Accounts / Watched Repositories"
echo
echo "You can also edit account settings and GITHUB_TRIGGER_PHRASE later."
