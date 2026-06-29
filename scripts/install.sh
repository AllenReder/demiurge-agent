#!/usr/bin/env sh
set -eu

REPO_URL="${DEMIURGE_REPO_URL:-}"
REPO_URL_SSH="${DEMIURGE_REPO_URL_SSH:-git@github.com:AllenReder/demiurge-agent.git}"
REPO_URL_HTTPS="${DEMIURGE_REPO_URL_HTTPS:-https://github.com/AllenReder/demiurge-agent.git}"
DEMIURGE_HOME="${DEMIURGE_HOME:-$HOME/.demiurge}"
INSTALL_DIR="${DEMIURGE_INSTALL_DIR:-$DEMIURGE_HOME/demiurge-agent}"
REF="${DEMIURGE_REF:-}"
SSH_CLONE_TIMEOUT_SECONDS="${DEMIURGE_SSH_CLONE_TIMEOUT_SECONDS:-15}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required" >&2
  exit 1
fi

mkdir -p "$DEMIURGE_HOME"

if [ -d "$INSTALL_DIR/.git" ] && ! git -C "$INSTALL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
  BACKUP_DIR="$INSTALL_DIR.broken-$(date -u +%Y%m%d-%H%M%S)"
  echo "existing checkout has no commits; moving it aside: $BACKUP_DIR" >&2
  mv "$INSTALL_DIR" "$BACKUP_DIR"
fi

clone_checkout() {
  if [ -n "$REPO_URL" ]; then
    echo "cloning managed checkout: $REPO_URL"
    GIT_TERMINAL_PROMPT=0 git clone "$REPO_URL" "$INSTALL_DIR"
    return
  fi

  echo "trying SSH clone: $REPO_URL_SSH"
  ssh_command="ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=accept-new"
  if command -v timeout >/dev/null 2>&1; then
    ssh_clone() {
      GIT_SSH_COMMAND="$ssh_command" timeout "$SSH_CLONE_TIMEOUT_SECONDS" git clone "$REPO_URL_SSH" "$INSTALL_DIR"
    }
  else
    ssh_clone() {
      GIT_SSH_COMMAND="$ssh_command" git clone "$REPO_URL_SSH" "$INSTALL_DIR"
    }
  fi
  if ssh_clone 2>/dev/null; then
    echo "cloned via SSH"
    return
  fi

  if [ -e "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
  fi

  echo "SSH clone failed, trying HTTPS clone: $REPO_URL_HTTPS"
  if GIT_TERMINAL_PROMPT=0 git clone "$REPO_URL_HTTPS" "$INSTALL_DIR"; then
    echo "cloned via HTTPS"
    return
  fi

  echo "failed to clone demiurge repository" >&2
  echo "set DEMIURGE_REPO_URL, DEMIURGE_REPO_URL_SSH, or DEMIURGE_REPO_URL_HTTPS to override the source" >&2
  exit 1
}

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "using existing managed checkout: $INSTALL_DIR"
else
  if [ -e "$INSTALL_DIR" ]; then
    echo "install dir exists but is not a git checkout: $INSTALL_DIR" >&2
    exit 1
  fi
  clone_checkout
fi

cd "$INSTALL_DIR"

if [ -n "$REF" ]; then
  git fetch --all --prune
  git checkout "$REF"
fi

uv sync
uv run demiurge init --home "$DEMIURGE_HOME"

cat <<EOF
demiurge installed

home:        $DEMIURGE_HOME
checkout:    $INSTALL_DIR
command:     $INSTALL_DIR/.venv/bin/demiurge

Update later with:
  $INSTALL_DIR/.venv/bin/demiurge --home "$DEMIURGE_HOME" update
EOF
