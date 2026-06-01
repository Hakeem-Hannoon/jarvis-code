#!/usr/bin/env bash
#
# One-command install for jarvis-code.
#
#   git clone <repo> && cd jarvis-code && ./install.sh
#
# Sets up a venv with ALL features (voice output + speech-to-text), downloads the
# JARVIS-style neural voice, puts `jarvis` on your PATH, and creates a per-user
# config — so `jarvis` then runs from any directory. Re-runnable and idempotent.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
VENV="$REPO/.venv"
VENV_PY="$VENV/bin/python"
CFG_DIR="$HOME/.config/jarvis"
VOICES="$CFG_DIR/voices"
MODEL="$VOICES/en_GB-alan-medium.onnx"

echo "▸ Installing jarvis from: $REPO"

# 1. Virtualenv with every optional feature (TTS + STT + dev tools).
echo "▸ Creating venv and installing dependencies (this can take a minute)…"
"$PY" -m venv "$VENV"
"$VENV_PY" -m pip install -q --upgrade pip
"$VENV_PY" -m pip install -q -e "$REPO[voice,stt,dev]"

# 2. Put `jarvis` on PATH so it works from any directory.
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/jarvis" "$HOME/.local/bin/jarvis"
echo "▸ Linked jarvis → ~/.local/bin/jarvis"

# 3. Download the realistic British-male neural voice (Piper "alan", ~60 MB).
mkdir -p "$VOICES"
if [ ! -f "$MODEL" ]; then
  echo "▸ Downloading the JARVIS voice model…"
  BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
  curl -fsSL -o "$MODEL" "$BASE/en_GB-alan-medium.onnx"
  curl -fsSL -o "$MODEL.json" "$BASE/en_GB-alan-medium.onnx.json"
fi

# 4. Create a per-user config (only if absent — never clobber yours), and point
#    it at the neural voice so it sounds good out of the box.
CFG="$CFG_DIR/config.toml"
if [ ! -f "$CFG" ]; then
  echo "▸ Creating global config: $CFG"
  "$HOME/.local/bin/jarvis" init --global >/dev/null || true
  "$VENV_PY" - "$CFG" "$MODEL" <<'PY'
import pathlib, re, sys
cfg, model = pathlib.Path(sys.argv[1]), sys.argv[2]
def setv(line, key, val):
    m = re.match(r'^(\s*' + re.escape(key) + r'\s*=\s*)([^#\n]*?)(\s*)(#[^\n]*)?(\r?\n?)$', line)
    if not m: return line
    pre, _o, _s, c, nl = m.groups()
    return f"{pre}{val}{('  ' + c) if c else ''}{nl or chr(10)}"
sec, out = None, []
for ln in cfg.read_text().splitlines(keepends=True):
    s = ln.strip()
    if s.startswith("[") and s.endswith("]"): sec = s[1:-1]; out.append(ln); continue
    if sec == "voice":
        if re.match(r'^\s*engine\s*=', ln):        ln = setv(ln, "engine", '"piper"')
        elif re.match(r'^\s*model_path\s*=', ln):  ln = setv(ln, "model_path", f'"{model}"')
        elif re.match(r'^\s*config_path\s*=', ln): ln = setv(ln, "config_path", f'"{model}.json"')
    out.append(ln)
cfg.write_text("".join(out))
PY
  chmod 600 "$CFG" 2>/dev/null || true
fi

# 5. PATH check.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) : ;;
  *) echo "▸ NOTE: add ~/.local/bin to your PATH, then restart your shell:"
     echo "       echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
esac

echo
echo "✓ Done. Next:"
echo "    jarvis doctor            # verify everything"
echo "    # add an API key to $CFG  (or export e.g. AZURE_API_KEY=...)"
echo "    jarvis                   # start chatting (voice + /talk ready)"
