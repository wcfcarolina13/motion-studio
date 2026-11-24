#!/bin/bash
# Full-quality Wan 2.2 14B clips on Colab via the official CLI. Always stops the session at the end.
#   run_colab.sh <in_dir> [out_dir]    in_dir holds the images and jobs.json; out_dir defaults to <in_dir>/../out
#   SETUP=x.py RENDER=y.py swap in other remote scripts. A render script must move each finished file into /content/out
#   whole (write elsewhere, then os.replace), print "OUTFILE <name>" for it, and write /content/out/RENDER_DONE last.
#   pull_live.sh downloads each file as it appears, so a session that drops mid-render keeps everything finished.
#   Settings (env): COLAB_SESSION name, COLAB_GPUS order, STALL_ALERT / STALL_STOP seconds, COLAB_ALERT notifier
#   command, UNIT_BUDGET units per run, COLAB_DRIVE_DIR for an attended run's second copy on Drive. The rules behind
#   them are in references/colab.md.
#   Exit 1 means no GPU or a dead runtime: retry_colab.sh retries those every 20 min. Exit 2 means the render started
#   but didn't finish, or an output didn't come down whole (what came down is kept); that is not retried, since a retry
#   could burn units on the same fault. Run retry_colab.sh again by hand to send only the jobs still missing.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: run_colab.sh <in_dir> [out_dir]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../out}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
cd "$OUT"
S=${COLAB_SESSION:-motion-studio-wan}      # one name per concurrent run; `colab stop -s` only ever stops this one
SETUP=${SETUP:-$HERE/remote_setup.py}; RENDER=${RENDER:-$HERE/remote_render.py}
[[ $SETUP = /* ]] || SETUP=$HERE/$SETUP; [[ $RENDER = /* ]] || RENDER=$HERE/$RENDER
PULL=""
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }   # GNU first: on Linux, stat -f means something else
# Whatever ends the run, the puller gets its last pass while the session is still up, then the session stops.
cleanup() {
  if [ -n "$PULL" ] && kill -0 "$PULL" 2>/dev/null; then
    touch "$OUT/.pull_stop"; echo "PHASE last pull"
    for _ in $(seq 120); do kill -0 "$PULL" 2>/dev/null || break; sleep 5; done
    kill "$PULL" 2>/dev/null
  fi
  echo "PHASE stop"; timeout 300 colab stop -s $S || true; timeout 120 colab usage | tee -a usage.log
}
balance() { timeout 120 colab usage 2>/dev/null | sed -n 's/^Current balance: \([0-9.]*\).*/\1/p'; }
alert() { echo "ALERT $*" | tee -a usage.log; [ -n "${COLAB_ALERT:-}" ] && $COLAB_ALERT "$*"; }
trap cleanup EXIT
date '+start %F %T' | tee -a usage.log; timeout 60 colab version | tee -a usage.log; timeout 120 colab usage | tee -a usage.log
B0=$(balance)
echo "PHASE new"
GPU=""
# COLAB_GPUS sets the order. G4 is not offered to the CLI; T4 (16 GB, no bf16) can't hold Wan 14B, so it is only for
# jobs that fit it (COLAB_GPUS="L4 T4"). Since about 2026-11-23 the CLI often answers 503 for L4 and A100.
for g in ${COLAB_GPUS:-A100 L4}; do
  if timeout 1800 colab new -s $S --gpu $g --high-mem; then GPU=$g; break; fi
  echo "no $g, trying next"; timeout 300 colab stop -s $S 2>/dev/null || true
  # A failed `new` can still leave a runtime assigned and billing, listed as [?] (no local name, so not stoppable here)
  hidden=$(timeout 120 colab sessions 2>/dev/null | grep '^\[?\]')
  [ -n "$hidden" ] && alert "Colab: a runtime with no session name is assigned and may be billing; stop it at colab.research.google.com (Runtime > Manage sessions): $hidden"
done
[ -z "$GPU" ] && { echo "FAILED: no GPU"; exit 1; }
echo "GPU $GPU" | tee -a usage.log
# Smoke test before anything is uploaded: a runtime can come up with no working GPU or a full disk.
smoke=$(timeout 300 colab exec -s $S --timeout 240 <<< "import subprocess, shutil, torch
print(subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'], capture_output=True, text=True).stdout.strip())
print('cuda ok', float((torch.ones(64, device='cuda') * 2).sum()))
print('disk free GB', shutil.disk_usage('/content').free // 10**9)" 2>&1); echo "$smoke" | tee -a usage.log
grep -q "cuda ok 128.0" <<< "$smoke" || { echo "FAILED: smoke test (no working GPU)"; exit 1; }
# A dropped connection right after allocation once left this exec hanging 15 min while units burned: cap it.
# /content/out exists from the start, so the puller's listing works while setup runs.
timeout 300 colab exec -s $S --timeout 120 <<< "import os; [os.makedirs(d, exist_ok=True) for d in ('/content/in', '/content/out')]" || { echo "FAILED: runtime not responding"; exit 1; }
# The CLI can print "Upload failed" (HTTP 500 from a broken runtime) and still exit 0.
for f in "$IN"/*; do
  up=$(timeout 600 colab upload -s $S "$f" "/content/in/$(basename "$f")" 2>&1); rc=$?; echo "$up"
  { [ $rc -ne 0 ] || grep -q "Upload failed" <<< "$up"; } && { echo "FAILED: upload $f"; exit 1; }
done
rm -f "$OUT/.pull_stop" "$OUT/.manifest.tsv"
# Opt-in Drive copy (COLAB_DRIVE_DIR=folder under MyDrive), for attended runs: it may wait on a browser consent click.
if [ -n "${COLAB_DRIVE_DIR:-}" ]; then
  echo "PHASE drive"; printf '%s' "$COLAB_DRIVE_DIR" > "$OUT/.drive_dir"
  timeout 300 colab upload -s $S "$OUT/.drive_dir" /content/drive_dir > /dev/null &&
    dr=$(timeout 900 colab exec -s $S -f "$HERE/remote_drive.py" --timeout 840 2>&1); echo "${dr:-}" | tee -a usage.log
  DRIVE=0; grep -q "DRIVE mirroring" <<< "${dr:-}" && DRIVE=1
  [ $DRIVE = 1 ] || alert "Colab $S: the Drive copy is not running; outputs come only through the Mac puller"
fi
bash "$HERE/pull_live.sh" $S "$OUT" > >(tee -a pull.log) 2>&1 & PULL=$!
echo "PHASE setup"; T0=$(date +%s)
# "Connection was lost" here does not stop the remote setup; the render script waits for it, so carry on.
colab exec -s $S -f "$SETUP" --timeout 0 || echo "setup exec returned an error; render will wait for setup to finish"
echo "setup $(( $(date +%s)-T0 ))s" | tee -a usage.log
echo "PHASE render"; T0=$(date +%s)
: > render.out
# Stall watch. Activity is a line from the render or a file pulled. After $STALL_ALERT s (default 900) without either,
# $COLAB_ALERT is run once with a message (a notifier command; unset, the alert is only logged). After $STALL_STOP s
# (default 1800) the render is given up: the run ends, the puller makes its last pass and the session stops, so a
# hung kernel can't burn units all night. Render scripts should print progress at least every few minutes.
idle_s() {
  local last f t; last=$(mtime render.out)
  while IFS= read -r f; do t=$(mtime "$f"); [ "$t" -gt "$last" ] && last=$t; done < <(find "$OUT" -maxdepth 1 -type f ! -name '.*' -newer render.out)
  echo $(( $(date +%s) - last ))
}
# Unit budget: every 5 min the balance is read; past $UNIT_BUDGET units spent this run (default 20, about $2.40 at
# top-up prices) the render is given up the same way. The balance moves in steps, so this is a backstop, not a meter.
alerted=0; ticks=0
stalled() {
  local idle spent now; idle=$(idle_s)
  if [ $alerted = 0 ] && [ "$idle" -ge ${STALL_ALERT:-900} ]; then
    alerted=1; alert "Colab $S: no render output or new file for $idle s ($(basename "$OUT"))"
  fi
  [ "$idle" -ge ${STALL_STOP:-1800} ] && { echo "STALLED: nothing for $idle s; giving up on the render" | tee -a usage.log; return 0; }
  ticks=$((ticks + 1))
  if [ -n "$B0" ] && [ $((ticks % 30)) = 0 ] && now=$(balance) && [ -n "$now" ]; then
    spent=$(awk -v a="$B0" -v b="$now" 'BEGIN { printf "%.2f", a - b }')
    echo "units spent so far: $spent" >> usage.log
    awk -v s="$spent" -v m="${UNIT_BUDGET:-20}" 'BEGIN { exit !(s >= m) }' && { alert "Colab $S: $spent units spent, over the run's budget of ${UNIT_BUDGET:-20}; stopping"; return 0; }
  fi
  return 1
}
colab exec -s $S -f "$RENDER" --timeout 0 > >(tee -a render.out) 2>&1 & EX=$!
while kill -0 $EX 2>/dev/null; do
  stalled && { kill $EX 2>/dev/null; touch .stalled; break; }
  sleep 10
done
wait $EX 2>/dev/null; sleep 1      # let tee flush
# The exec can return while the kernel still renders ("Connection was lost"). Unless the render visibly crashed or
# stalled, keep the session while the puller still gets new files, under the same stall watch.
if [ ! -e .stalled ] && ! grep -q "RENDER DONE" render.out && ! grep -qE "^Traceback|FAILED:" render.out; then
  echo "PHASE render detached: the exec returned early; waiting on the puller for RENDER_DONE"
  while kill -0 "$PULL" 2>/dev/null; do
    stalled && break
    sleep 10
  done
fi
rm -f .stalled
touch "$OUT/.pull_stop"; wait "$PULL"; PR=$?; PULL=""; rm -f "$OUT/.pull_stop"
# The Drive copy runs in the kernel, which `colab stop` ends: give it up to 5 min to catch up after a finished render.
if [ "${DRIVE:-0}" = 1 ] && grep -q "RENDER DONE" render.out; then
  for _ in $(seq 30); do
    timeout 120 colab ls -s $S "/content/drive/MyDrive/${COLAB_DRIVE_DIR#/}" 2>/dev/null | grep -qx RENDER_DONE && { echo "Drive copy complete"; break; }
    sleep 10
  done
fi
# Belt and braces: anything the render named that the puller missed, while the session is still up.
# Checked against the manifest like the puller's own downloads.
ok_sum() { [ -s .manifest.tsv ] || return 0; python3 - "$1" <<'EOF'
import hashlib, os, sys
n = sys.argv[1]; want = None
for l in open('.manifest.tsv'):
    p = l.rstrip('\n').split('\t')
    if len(p) == 3 and p[0] == n: want = (int(p[1]), p[2])
if want is None: sys.exit(0)
h = hashlib.sha256(open('.' + n + '.part', 'rb').read()).hexdigest()
sys.exit(0 if (os.path.getsize('.' + n + '.part'), h) == want else 1)
EOF
}
missing=""
while IFS= read -r f; do
  { [ -z "$f" ] || [ -s "$OUT/$f" ]; } && continue
  if timeout 900 colab download -s $S "/content/out/$f" ".$f.part" > /dev/null 2>&1 && [ -s ".$f.part" ] && ok_sum "$f"; then
    mv -f ".$f.part" "$f"; echo "got $f (late)"
  else
    rm -f ".$f.part"; missing="$missing $f"
  fi
done < <(grep -o 'OUTFILE [^ ]*' render.out | cut -d' ' -f2 | sort -u)
DONE=0; { [ $PR = 0 ] || grep -q "RENDER DONE" render.out; } && DONE=1
[ -n "$missing" ] && { DONE=0; echo "MISSING: the render made these but they didn't come down whole:$missing"; }
[ $DONE = 1 ] || echo "FAILED: the render did not finish or not every output came down; kept what was pulled ($(find "$OUT" -maxdepth 1 -type f ! -name '.*' ! -name '*.log' ! -name '*.out' | wc -l) files)"
echo "render $(( $(date +%s)-T0 ))s" | tee -a usage.log
now=$(balance); [ -n "$B0" ] && [ -n "$now" ] && awk -v a="$B0" -v b="$now" 'BEGIN { printf "units spent this run: %.2f\n", a - b }' | tee -a usage.log
date '+end %F %T' | tee -a usage.log
# 2: the render ran but didn't finish. retry_colab.sh retries only 1 (no GPU, dead runtime), not this: look first.
[ $DONE = 1 ] || exit 2
