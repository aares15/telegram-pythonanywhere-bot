#!/usr/bin/env bash
#
# connect-claude-code.sh — point Claude Code at the workshop AI gateway.
#
# You will be given a personal key in class (it looks like:  sk-xxxxxxxx ).
# Run this once with that key and it will install Claude Code (if needed),
# wire it to the workshop gateway, and launch it.
#
# USAGE
#   ./connect-claude-code.sh sk-your-key            # set up + launch  claude
#   ./connect-claude-code.sh sk-your-key --persist  # also remember it in your shell
#   source connect-claude-code.sh sk-your-key       # only set env in THIS shell
#
# If you don't pass the key, the script will ask for it.
#
# macOS / Linux / WSL / Git-Bash. (Native Windows PowerShell: see setup/CLAUDE-CODE.md)
#
# Instructor: the gateway/model settings live at the top of _wcc_main() below.

# Detect early whether we're being `source`d — BEFORE touching shell options, so a
# sourced run never leaks `set -u`/`pipefail` into the caller's interactive shell.
_wcc_sourced=0
if [ -n "${ZSH_VERSION:-}" ]; then
  case "${ZSH_EVAL_CONTEXT:-}" in *:file*) _wcc_sourced=1;; esac
elif [ -n "${BASH_VERSION:-}" ]; then
  [ "${BASH_SOURCE[0]:-$0}" != "$0" ] && _wcc_sourced=1
fi
# Harden only when executed; when sourced we must not change the caller's shell options.
[ "$_wcc_sourced" -eq 0 ] && set -uo pipefail

# Namespaced helpers — the _wcc_ prefix keeps them from colliding with the caller's own
# functions when this script is sourced (they're unset again at the end of a sourced run).
_wcc_ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
_wcc_warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
_wcc_err()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
_wcc_info() { printf '\033[2m•\033[0m %s\n' "$*"; }
# Wrap a value as a POSIX single-quoted shell literal — safe to write into a profile even
# if it ever contains quotes/specials (escapes embedded ' as '\''). Streams the value
# straight through (no inner command substitution, which would eat trailing newlines).
_wcc_shq()  { printf "'"; printf '%s' "$1" | sed "s/'/'\\\\''/g"; printf "'"; }

# All the real work runs inside this function with `local` vars, so a sourced run leaves
# ONLY the exported ANTHROPIC_* env behind — no stray KEY/code/body/... in the caller.
# Fatal problems `return 1`; the dispatcher at the bottom turns that into exit-vs-return.
_wcc_main() {
  # ---- workshop settings (instructor: change here if the gateway/model moves) ----
  local GATEWAY="https://ai.simonian.online"   # no /v1 — Claude Code adds the path itself
  local MODEL="${WORKSHOP_MODEL:-gemma26}"     # gemma26 = the workshop model (Gemma-4-26B, ~200k
                                               # context during class). It only has the big window
                                               # while the scheduled workshop config is up; running
                                               # it OUTSIDE class hours can 400 with a 20480-token
                                               # "ContextWindowExceededError" — expected. See
                                               # setup/CLAUDE-CODE.md.
  # ------------------------------------------------------------------------------

  # ---- 1. resolve the key (arg, env, or prompt) --------------------------------
  local PERSIST=0 KEY="${WORKSHOP_KEY:-}" a='' _gotkey=0
  for a in "$@"; do
    case "$a" in
      --persist) PERSIST=1 ;;
      --*)       _wcc_warn "Ignoring unknown option '$a' (did you mean --persist?)." ;;
      *)         [ "$_gotkey" -eq 1 ] && _wcc_warn "More than one key given; using the last one."
                 KEY="$a"; _gotkey=1 ;;
    esac
  done

  if [ -z "$KEY" ]; then
    printf 'Paste your workshop key (handed out in class), then press Enter:\n> '
    IFS= read -r KEY
  fi
  KEY="${KEY#"${KEY%%[![:space:]]*}"}"; KEY="${KEY%"${KEY##*[![:space:]]}"}"  # trim spaces

  if [ -z "$KEY" ]; then _wcc_err "No key given. Ask the instructor for your key and run again."; return 1; fi
  case "$KEY" in
    sk-*) : ;;
    *) _wcc_warn "That doesn't look like a workshop key (they start with 'sk-'). Continuing anyway." ;;
  esac

  # ---- 2. preflight: does the key work against the gateway? --------------------
  if ! command -v curl >/dev/null 2>&1; then
    _wcc_err "'curl' isn't installed, but this script needs it. Install curl, then re-run."
    return 1
  fi
  echo; _wcc_info "Checking your key against $GATEWAY ..."
  local resp='' code='' body='' msg=''
  resp="$(curl -sS -m 20 -w $'\n%{http_code}' \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    "$GATEWAY/v1/messages" \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":4,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}" 2>/dev/null)"
  code="$(printf '%s' "$resp" | tail -n1)"
  body="$(printf '%s' "$resp" | sed '$d')"

  case "$code" in
    200) _wcc_ok "Your key works and the gateway answered. You're good to go." ;;
    401|403)
      if printf '%s' "$body" | grep -q outside_workshop_hours; then
        _wcc_ok "Your key is VALID."
        _wcc_warn "The gateway is closed right now — it only opens during your class hours."
        # the human sentence is the innermost 'message' (LiteLLM nests a dict-string in the JSON)
        msg="$(printf '%s' "$body" | grep -oE "'message': *'[^']*'" | tail -1 | sed "s/^'message': *'//; s/'\$//")"
        [ -z "$msg" ] && msg="$(printf '%s' "$body" | sed -n 's/.*"message": *"\([^"]*\)".*/\1/p' | head -1)"
        [ -n "$msg" ] && _wcc_info "$msg"
        _wcc_info "This is expected outside class. Re-run during the session and it will connect."
      else
        _wcc_err "The gateway rejected your key (HTTP $code). It may be mistyped or expired."
        _wcc_info "Double-check the key with your instructor, then run this script again."
        return 1
      fi ;;
    000) _wcc_err "Couldn't reach $GATEWAY. Check your internet connection and try again."; return 1 ;;
    *)   _wcc_warn "Unexpected response (HTTP $code) — setup will continue, but tell the instructor if 'claude' fails."
         printf '%s\n' "$body" | head -c 300; echo ;;
  esac

  # ---- 3. make sure Claude Code is installed -----------------------------------
  echo
  if command -v claude >/dev/null 2>&1; then
    _wcc_ok "Claude Code already installed ($(claude --version 2>/dev/null | head -1))."
  else
    _wcc_info "Installing Claude Code ..."
    if curl -fsSL https://claude.ai/install.sh | bash; then
      _wcc_ok "Installed via claude.ai/install.sh"
    elif command -v npm >/dev/null 2>&1 && npm install -g @anthropic-ai/claude-code; then
      _wcc_ok "Installed via npm"
    else
      _wcc_err "Couldn't install Claude Code automatically."
      _wcc_info "Install it by hand (see setup/CLAUDE-CODE.md), then re-run this script."
      return 1
    fi
    # make sure the freshly-installed binary is on PATH for the rest of this script
    case ":$PATH:" in *":$HOME/.local/bin:"*) : ;; *) PATH="$HOME/.local/bin:$PATH";; esac
  fi

  # ---- 4. point Claude Code at the workshop gateway ----------------------------
  export ANTHROPIC_BASE_URL="$GATEWAY"
  export ANTHROPIC_AUTH_TOKEN="$KEY"         # skips Claude Code's normal login
  export ANTHROPIC_MODEL="$MODEL"            # the model your prompts use
  export ANTHROPIC_SMALL_FAST_MODEL="$MODEL" # background tasks use the same model
  export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-8192}"
  _wcc_ok "Claude Code is now pointed at the workshop ($MODEL via $GATEWAY)."

  # ---- 5. optionally remember it in the shell profile --------------------------
  if [ "$PERSIST" -eq 1 ]; then
    local prof="$HOME/.bashrc"
    [ -n "${ZSH_VERSION:-}" ] && prof="$HOME/.zshrc"
    [ -n "${SHELL:-}" ] && case "$SHELL" in *zsh) prof="$HOME/.zshrc";; *bash) prof="$HOME/.bashrc";; esac
    local marker="# >>> workshop claude code >>>"
    local endmk="# <<< workshop claude code <<<"
    local tmp=''; tmp="$(mktemp 2>/dev/null)"
    if [ -z "$tmp" ]; then
      _wcc_warn "Couldn't create a temp file; env is set for THIS session only."
    else
      # Build the COMPLETE new profile in $tmp first (existing content minus any previous
      # workshop block, then the fresh block), then write it back in ONE pass. `cat >` (not
      # `mv`) follows a symlinked profile (stow/chezmoi/yadm) instead of replacing it, and a
      # single write avoids leaving the profile stripped-but-not-rewritten if a step fails.
      {
        [ -f "$prof" ] && sed "/$marker/,/$endmk/d" "$prof"
        echo "$marker"
        # every value _wcc_shq-quoted so nothing is re-expanded when the profile is sourced
        printf 'export ANTHROPIC_BASE_URL=%s\n'            "$(_wcc_shq "$GATEWAY")"
        printf 'export ANTHROPIC_AUTH_TOKEN=%s\n'          "$(_wcc_shq "$KEY")"
        printf 'export ANTHROPIC_MODEL=%s\n'               "$(_wcc_shq "$MODEL")"
        printf 'export ANTHROPIC_SMALL_FAST_MODEL=%s\n'    "$(_wcc_shq "$MODEL")"
        printf 'export CLAUDE_CODE_MAX_OUTPUT_TOKENS=%s\n' "$(_wcc_shq "$CLAUDE_CODE_MAX_OUTPUT_TOKENS")"
        echo "$endmk"
      } > "$tmp"
      if [ -s "$tmp" ] && cat "$tmp" > "$prof"; then
        _wcc_ok "Saved to $prof — new terminals will already be connected."
      else
        _wcc_warn "Couldn't update $prof — env is set for THIS session only."
      fi
      rm -f "$tmp"
    fi
  fi

  return 0
}

# ---- 6. run, then launch (executed) or hand back to the shell (sourced) --------
if [ "$_wcc_sourced" -eq 1 ]; then
  # Neutralize the caller's `set -e` for the duration of setup (restored below) so a
  # transient non-zero can't abort the user's shell or skip cleanup. Sourcing stays clean.
  case $- in *e*) _wcc_e=1;; *) _wcc_e=0;; esac
  set +e
  _wcc_main "$@"; _wcc_rc=$?
  [ "$_wcc_rc" -eq 0 ] && _wcc_info "Env set in this shell. Type 'claude' to start."
  # Leave only the exported env behind: drop our helpers, then restore the caller's options.
  unset -f _wcc_main _wcc_ok _wcc_warn _wcc_err _wcc_info _wcc_shq 2>/dev/null
  [ "$_wcc_e" -eq 1 ] && set -e
  unset _wcc_sourced _wcc_e 2>/dev/null
  return "$_wcc_rc"   # _wcc_rc is the one deliberate straggler (namespaced, harmless)
fi

_wcc_main "$@" || exit 1
echo
_wcc_info "Starting Claude Code — type your request in plain English, or 'exit' to quit."
echo
if command -v claude >/dev/null 2>&1; then
  exec claude
else
  _wcc_err "Claude Code isn't on your PATH yet. Open a NEW terminal and type 'claude'."
  exit 1
fi
