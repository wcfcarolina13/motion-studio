#!/bin/bash
# Downloads a Colab session's outputs while the render runs, so a dropped session loses at most the file in progress.
#   pull_live.sh <session> <out_dir> [remote_dir]      remote_dir defaults to /content/out
# Every $PULL_EVERY seconds (30) it lists the remote folder (`colab ls` goes through the contents API, so it answers
# while the kernel is busy) and downloads each file it doesn't have yet. It stops after one last pass once the remote
# folder holds RENDER_DONE, or once $OUT/.pull_stop exists (run_colab.sh writes it when the run ends).
#
# What it trusts. The contents API gives no size or date, so the remote script must make a file appear only when it is
# whole: write it elsewhere, then os.replace() it in. When the folder holds MANIFEST.tsv (name, size, sha256 per line;
# remote_render.py's publish() writes it), only listed files are taken and each download is checked against its size
# and hash; a mismatch is deleted and fetched again next pass. Without a manifest, the atomic write is the only guard.
# Names ending .part or .tmp and hidden names are always skipped.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
export COLAB_CLI_REQUEST_TIMEOUT=${COLAB_CLI_REQUEST_TIMEOUT:-60}     # see run_colab.sh
[ $# -ge 2 ] || { echo "usage: pull_live.sh <session> <out_dir> [remote_dir]"; exit 2; }
S=$1; OUT=$2; R=${3:-/content/out}; EVERY=${PULL_EVERY:-30}
mkdir -p "$OUT"
log() { echo "[pull $(date +%T)] $*"; }
MAN="$OUT/.manifest.tsv"

# size and sha256 of a local file, as "size<TAB>sha"
local_sum() { python3 -c 'import hashlib,os,sys
p=sys.argv[1]; h=hashlib.sha256()
with open(p,"rb") as f:
    for b in iter(lambda: f.read(1<<20), b""): h.update(b)
print(f"{os.path.getsize(p)}\t{h.hexdigest()}")' "$1"; }

# "size<TAB>sha" the manifest gives for a name; empty when the name isn't listed
want_sum() { [ -s "$MAN" ] && awk -F'\t' -v n="$1" '$1 == n { w = $2 "\t" $3 } END { if (w != "") print w }' "$MAN"; }

# The CLI writes straight to the target, so a dropped download would leave a cut-off file that the "already here"
# check would then trust: download beside it, check it, and rename only when it is right.
fetch() {
  local n=$1 want=$2 tmp="$OUT/.$1.part"
  if ! timeout 900 colab download -s "$S" "$R/$n" "$tmp" > /dev/null 2>&1 || [ ! -s "$tmp" ]; then
    rm -f "$tmp"; log "download of $n failed; next pass tries again"; return 1
  fi
  if [ -n "$want" ] && [ "$(local_sum "$tmp")" != "$want" ]; then
    rm -f "$tmp"; log "download of $n doesn't match its manifest entry; next pass tries again"; return 1
  fi
  mv -f "$tmp" "$OUT/$n"; log "got $n${want:+ (size and sha256 checked)}"
}

# One pass: list, download what's new. Returns 0 if RENDER_DONE was listed, 1 if not, 3 if the listing failed.
pass() {
  local names rc done=1 n want
  names=$(timeout 120 colab ls -s "$S" "$R" 2>&1); rc=$?
  if [ $rc -ne 0 ] || grep -q '^\[colab\]' <<< "$names"; then
    log "list failed: $(head -c 200 <<< "$names" | tr '\n' ' ')"; return 3
  fi
  if grep -qx 'MANIFEST.tsv' <<< "$names"; then
    timeout 300 colab download -s "$S" "$R/MANIFEST.tsv" "$MAN.part" > /dev/null 2>&1 && [ -s "$MAN.part" ] && mv -f "$MAN.part" "$MAN"
  fi
  while IFS= read -r n; do
    [ -z "$n" ] && continue
    case "$n" in
      RENDER_DONE) done=0; continue ;;
      MANIFEST.tsv|*/|*.part|*.tmp|.*) continue ;;
    esac
    [ -s "$OUT/$n" ] && continue                  # already here
    want=$(want_sum "$n")
    [ -s "$MAN" ] && [ -z "$want" ] && continue   # a manifest run lists a file once it is whole; wait for that
    fetch "$n" "$want"
  done <<< "$names"
  return $done
}

fails=0
while :; do
  pass; rc=$?
  if [ $rc -eq 0 ]; then
    pass; log "RENDER_DONE: all outputs pulled"; exit 0   # one more pass retries any download that failed in that one
  fi
  if [ -e "$OUT/.pull_stop" ]; then
    [ $rc -eq 3 ] || pass; log "stopped before RENDER_DONE"; exit 1
  fi
  [ $rc -eq 3 ] && fails=$((fails + 1)) || fails=0
  # A runtime that stays unreachable for 20 passes (10 min at 30 s) is gone; what came down is kept.
  [ $fails -ge ${PULL_FAILS:-20} ] && { log "runtime unreachable for $fails passes; giving up"; exit 1; }
  sleep "$EVERY"
done
