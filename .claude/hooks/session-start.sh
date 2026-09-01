#!/bin/bash
# SessionStart hook: provision the KiCad toolchain this repo needs.
#
# tools/ depends on packages that are not declared anywhere, and on a portable
# KiCad tree that is not in the repository, so a fresh container cannot verify
# anything until this has run.  Installs KiCad 10.0.6 (the version the Rev A
# release chain was produced with), the Python packages tools/ imports, and the
# shim that stands in for the portable KiCad layout.
set -euo pipefail

# Web sessions only; a local checkout keeps whatever KiCad the user already has.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HOOKS="$ROOT/.claude/hooks"
PROJECT="HomeKey-Lock-RevA-PN7161"

SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

log() { printf '[session-start] %s\n' "$*"; }

# --- KiCad 10 -------------------------------------------------------------
# 10.x specifically: the board is in the KiCad 10 file format and Ubuntu
# noble's own package is 7.0.11, which cannot open it.
have_kicad10() {
  command -v kicad-cli >/dev/null 2>&1 &&
    [ "$(kicad-cli --version 2>/dev/null | cut -d. -f1)" = "10" ]
}

if have_kicad10; then
  log "KiCad $(kicad-cli --version) already present"
else
  log "installing KiCad 10 from the kicad-10.0-releases PPA"
  # The signing key is embedded in kicad-ppa.sources so this does not depend
  # on a keyserver being reachable.
  $SUDO install -m 644 "$HOOKS/kicad-ppa.sources" /etc/apt/sources.list.d/kicad-10.sources
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    kicad kicad-symbols kicad-footprints
  log "installed KiCad $(kicad-cli --version)"
fi

# --- Python packages ------------------------------------------------------
# tools/ imports these; none of them is declared in the repo.  pillow is here
# because noble's system PIL is too old for the pip matplotlib that
# render_board.py pulls in; python-dateutil because matplotlib.pyplot needs it.
#
# Two subtleties, both learned the hard way:
#  * verify with HOME neutralised.  A package sitting in ~/.local satisfies a
#    plain import check but vanishes for any process run with a different HOME,
#    which is exactly how build_release.py failed while a bare check passed.
#  * install into the interpreter's own system site directory for the same
#    reason -- a --user install is invisible the moment HOME changes.
PY_DEPS="kiutils shapely numpy matplotlib pillow gerber-writer python-dateutil six"
PY_CHECK='import kiutils, shapely, gerber_writer; import matplotlib.pyplot'
if env HOME=/nonexistent python3 -c "$PY_CHECK" 2>/dev/null; then
  log "python dependencies already satisfied"
else
  PY_SITE="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  log "installing python dependencies into $PY_SITE"
  python3 -m pip install -q --break-system-packages --upgrade --target "$PY_SITE" $PY_DEPS
  env HOME=/nonexistent python3 -c "$PY_CHECK"
fi

# --- Shim for the portable-KiCad layout tools/ expects ---------------------
# Four tools (run_official_drc, export_official_fabrication,
# render_official_previews, test_u4_generation_chain) hardcode a portable
# KiCad tree at <workspace>/.tools/kicad10-full-root, and 27 call sites add
# <workspace>/.tools/py to sys.path.  "workspace" is the repository's parent
# directory.  Neither tree exists here, so point that layout at the installed
# KiCad instead of editing 33 files.
TOOLS_ROOT="$(dirname "$ROOT")/.tools"
KICAD_SHIM="$TOOLS_ROOT/kicad10-full-root"
if [ -x "$KICAD_SHIM/usr/bin/kicad-cli" ]; then
  log "portable-KiCad shim already in place"
else
  log "creating portable-KiCad shim at $TOOLS_ROOT/kicad10-full-root"
  mkdir -p "$KICAD_SHIM/usr/bin" "$TOOLS_ROOT/py"
  ln -sfn "$(command -v kicad-cli)" "$KICAD_SHIM/usr/bin/kicad-cli"
  # LD_LIBRARY_PATH targets the tools build from KICAD_ROOT/usr/lib{,/x86_64-linux-gnu}
  ln -sfn /usr/lib "$KICAD_SHIM/usr/lib"
  # generate_schematics.py resolves stock footprints under a second portable
  # tree, .tools/kicad-root; point it at the kicad-footprints package.
  mkdir -p "$TOOLS_ROOT/kicad-root/usr/share"
  ln -sfn /usr/share/kicad "$TOOLS_ROOT/kicad-root/usr/share/kicad"
fi

# --- Session environment --------------------------------------------------
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export HOMEKEY_PROJECT_DIR=\"$ROOT\""
    echo "export HOMEKEY_KICAD_DIR=\"$ROOT/hardware/kicad\""
  } >> "$CLAUDE_ENV_FILE"
fi

log "ready: kicad-cli $(kicad-cli --version), sources in-tree"
