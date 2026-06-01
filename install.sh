#!/usr/bin/env bash
# Install `jarvis` so it runs from any directory after cloning the repo.
#
#   git clone <repo> && cd jarvis-code && ./install.sh
#
# Prefers pipx (isolated, on PATH). Falls back to a local venv + a symlink into
# ~/.local/bin. Also creates a per-user global config so jarvis works anywhere.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Installing jarvis from: $REPO"

if command -v pipx >/dev/null 2>&1; then
  echo "Using pipx (isolated install, automatically on PATH)…"
  pipx install --force "$REPO"
  BIN="$(command -v jarvis 2>/dev/null || echo "$HOME/.local/bin/jarvis")"
else
  echo "pipx not found; using a local venv + a ~/.local/bin symlink…"
  PY="${PYTHON:-python3}"
  "$PY" -m venv "$REPO/.venv"
  "$REPO/.venv/bin/pip" install -q --upgrade pip
  "$REPO/.venv/bin/pip" install -q -e "$REPO"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$REPO/.venv/bin/jarvis" "$HOME/.local/bin/jarvis"
  BIN="$HOME/.local/bin/jarvis"
  echo "Linked: $BIN -> $REPO/.venv/bin/jarvis"
fi

# A global config lets jarvis run in any directory without a project-local one.
if [ ! -f "$HOME/.config/jarvis/config.toml" ] && [ ! -f "$HOME/.jarvis/config.toml" ]; then
  echo "Creating a per-user global config…"
  "$BIN" init --global || true
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) : ;;
  *) echo
     echo "NOTE: ~/.local/bin is not on your PATH. Add it:"
     echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac

echo
echo "Done. Next:"
echo "  1. jarvis doctor"
echo "  2. Add an API key to ~/.config/jarvis/config.toml (or export e.g. AZURE_API_KEY)."
echo "  3. jarvis              # voice output is on by default (macOS 'say'); --voice false to mute"
echo "  4. Push-to-talk (/talk) also needs: pip install faster-whisper"
