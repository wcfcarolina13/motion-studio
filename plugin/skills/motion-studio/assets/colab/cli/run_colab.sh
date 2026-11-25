#!/bin/bash
# Full-quality Wan 2.2 14B clips on Colab via the official CLI. Always stops the session at the end.
#   run_colab.sh <in_dir> [out_dir]    in_dir holds the images and jobs.json; out_dir defaults to <in_dir>/../out
#   SETUP=x.py RENDER=y.py swap in other remote scripts. A render script must move each finished file into /content/out
#   whole (write elsewhere, then os.replace), print "OUTFILE <name>" for it, and write /content/out/RENDER_DONE last.
#   pull_live.sh downloads each file as it appears, so a session that drops mid-render keeps everything finished.
#   Settings (env): COLAB_SESSION name (default ms-<out dir>-<pid>, unique per run; a name already live is refused), COLAB_GPUS order, STALL_ALERT / STALL_STOP seconds, COLAB_ALERT notifier
#   command, UNIT_BUDGET units per run, COLAB_DRIVE_DIR for an attended run's second copy on Drive, HF_TOKEN_FILE for a read-only Hugging Face token. The rules behind
#   them are in references/colab.md.
#   Exit 1 means no GPU, a dead runtime or a setup that failed: retry_colab.sh retries those every 20 min. Exit 3 means every card asked
#   for is already in use on the account (another lane or project); retry_colab.sh waits on it without using a try.
#   Exit 4 means the runtime came up but setup never started on it; retry_colab.sh retries that at once. Exit 2 means the render started
#   but didn't finish, or an output didn't come down whole (what came down is kept); that is not retried, since a retry
#   could burn units on the same fault. Run retry_colab.sh again by hand to send only the jobs still missing.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
# The CLI's own request timeout is 10 s, but a cold Colab kernel takes 12 to 39 s to answer, so the first calls after
# "Session READY" died with "Connection was lost" (google-colab-cli issues #285 and #264; the CLI reads this variable in
# client.py). That explains most dropped smoke tests and setups that never started (2026-11-25), and the 120 s allocation
# timeout behind orphan "[?]" runtimes (#263) is eased too. Our own `timeout` wrappers stay the outer limit.
export COLAB_CLI_REQUEST_TIMEOUT=${COLAB_CLI_REQUEST_TIMEOUT:-60}
export REQUEST_TIMEOUT=${REQUEST_TIMEOUT:-60}     # kernel calls: jupyter_kernel_client reads this one (default 10 s in the installed CLI)
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: run_colab.sh <in_dir> [out_dir]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../out}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
cd "$OUT"
# A unique name per run. Two runs under one name (2026-11-24): the second `colab new` took the name, the first run's
# runtime became an unnamed "[?]" one the CLI can neither pull from nor stop, and 14 finished stills were stranded.
# A lane's folder is <out>/.lane-<k>: name it after <out> plus the lane, so the name says which batch it is
# (the leading dot used to give "ms--lane-1-<pid>", 2026-11-25).
b=$(basename "$OUT"); lane=""
case $b in .lane-*) lane=-${b#.}; b=$(basename "$(dirname "$OUT")");; esac
b=$(printf %s "$b" | tr -c 'A-Za-z0-9' '-' | sed 's/^-*//' | cut -c1-24)
S=${COLAB_SESSION:-ms-${b:-run}$lane-$$}
SETUP=${SETUP:-$HERE/remote_setup.py}; RENDER=${RENDER:-$HERE/remote_render.py}
[[ $SETUP = /* ]] || SETUP=$HERE/$SETUP; [[ $RENDER = /* ]] || RENDER=$HERE/$RENDER
PULL=""; NEWPID=""
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }   # GNU first: on Linux, stat -f means something else
# Whatever ends the run, the puller gets its last pass while the session is still up, then the session stops.
cleanup() {
  if [ -n "$PULL" ] && kill -0 "$PULL" 2>/dev/null; then
    touch "$OUT/.pull_stop"; echo "PHASE last pull"
    for _ in $(seq 120); do kill -0 "$PULL" 2>/dev/null || break; sleep 5; done
    kill "$PULL" 2>/dev/null
  fi
  echo "PHASE stop"; timeout 300 colab stop -s $S || true
  # Cancelled while `colab new` was in flight (2026-11-25): the stop found no session, the runtime came up a moment
  # later and billed about 35 min of L4 with no run behind it. So when a request was still out, kill it and watch for
  # the name for NEW_GRACE seconds (150), stopping it the moment it appears.
  if [ -n "$NEWPID" ]; then
    kill "$NEWPID" 2>/dev/null
    echo "a runtime request was in flight; watching for $S for ${NEW_GRACE:-150} s"
    t0=$(date +%s)
    while [ $(( $(date +%s) - t0 )) -lt "${NEW_GRACE:-150}" ]; do
      if timeout 90 colab sessions 2>/dev/null | grep -qF "[$S]"; then
        timeout 300 colab stop -s $S; echo "stopped $S, which came up after the run ended" | tee -a usage.log; break
      fi
      sleep 10
    done
  fi
  timeout 120 colab usage | tee -a usage.log
}
balance() { timeout 120 colab usage 2>/dev/null | sed -n 's/^Current balance: \([0-9.]*\).*/\1/p'; }
alert() { echo "ALERT $*" | tee -a usage.log; [ -n "${COLAB_ALERT:-}" ] && $COLAB_ALERT "$*"; }
# Refuse a name that is already live, before the exit trap is set: the trap stops session $S, which would be the other run's.
if timeout 120 colab sessions 2>/dev/null | grep -qF "[$S]"; then
  echo "FAILED: a Colab session named $S is already running; set COLAB_SESSION to another name or leave it unset"; exit 2
fi
trap cleanup EXIT
trap 'exit 143' TERM INT HUP   # a cancel runs the cleanup above (bash alone skips the EXIT trap on some signals)
date '+start %F %T' | tee -a usage.log; timeout 60 colab version | tee -a usage.log; timeout 120 colab usage | tee -a usage.log
B0=$(balance)
echo "PHASE new"
GPU=""
# COLAB_GPUS sets the order. The default is the cheapest GPU that fits: an A100 burns about 11 units an hour against
# about 2.3 for an L4 (2026-11-24), so it is only used when asked for (COLAB_GPUS="A100 L4"). T4 (16 GB, no bf16) can't
# hold Wan 14B, so the default Wan scripts get "L4" and swapped-in jobs (stills, voice) get "L4 T4". G4 is not offered
# to the CLI. Since about 2026-11-23 the CLI often answers 503 for L4 and A100.
# A setup script can declare the card memory it needs with a "MIN_VRAM_GB: <n>" comment; over 16 keeps it off the T4
# (Z-Image went out of memory on a T4's 14.6 GB, 2026-11-25).
# An in_dir's min_vram_gb file (shots_to_jobs.py writes it per model) raises it; over 24 needs an A100 (Qwen-Image).
need=$(grep -o 'MIN_VRAM_GB: *[0-9]*' "$SETUP" 2>/dev/null | grep -o '[0-9]*$' | head -1)
inneed=$(cat "$IN/min_vram_gb" 2>/dev/null | tr -dc 0-9); [ "${inneed:-0}" -gt "${need:-0}" ] && need=$inneed
if [ "${need:-0}" -gt 24 ]; then DEF_GPUS="A100"
elif [ "${need:-0}" -gt 16 ] || { [ "$SETUP" = "$HERE/remote_setup.py" ] && [ "$RENDER" = "$HERE/remote_render.py" ]; }; then DEF_GPUS="L4"; else DEF_GPUS="L4 T4"; fi
# "precondition failed" means the account already holds its one runtime of that type (the plan allows about one
# runtime per GPU type, 2026-11-24): that is a wait, not a fault, so when every card answers it the run exits 3.
# The request runs in the background and is waited on, so a cancel reaches the cleanup at once and knows it was out.
request() {
  timeout 1800 colab new -s $S --gpu $1 --high-mem > .new.out 2>&1 & NEWPID=$!
  wait $NEWPID; local rc=$?; NEWPID=""; newout=$(cat .new.out); rm -f .new.out; echo "$newout"; return $rc
}
# An orphan is a runtime named by this script (ms-...-<pid>) whose run is gone from this Mac: no live process with that
# pid running run_colab.sh. It holds its card and bills with nothing pulling from it, so it is stopped (the owner's
# standing permission covers our own runtimes, colab.md). Returns 0 when it stopped one.
reap_orphans() {
  local n pid got=1
  while IFS= read -r n; do
    pid=${n##*-}; [[ $pid =~ ^[0-9]+$ ]] && [ "$n" != "$S" ] || continue
    ps -o command= -p "$pid" 2>/dev/null | grep -q run_colab && continue
    timeout 300 colab stop -s "$n" && got=0
    alert "Colab: stopped $n, a runtime of ours whose run (pid $pid) is no longer running on this machine"
  done < <(timeout 120 colab sessions 2>/dev/null | sed -n 's/^\[\(ms-[^]]*\)\].*/\1/p')
  return $got
}
BUSY=1; reaped=0
for g in ${COLAB_GPUS:-$DEF_GPUS}; do
  request $g; rc=$?
  # A busy card may be held by one of our own orphans: stop those once per run and ask for the card again.
  if [ $rc -ne 0 ] && grep -qi "precondition" <<< "$newout" && [ $reaped = 0 ]; then
    reaped=1; reap_orphans && { echo "asking for $g again"; request $g; rc=$?; }
  fi
  if [ $rc -eq 0 ]; then GPU=$g; BUSY=0; break; fi
  grep -qi "precondition" <<< "$newout" || BUSY=0
  echo "no $g, trying next"; timeout 300 colab stop -s $S 2>/dev/null || true
  # A failed `new` can still leave a runtime assigned and billing, listed as [?] (no local name, so not stoppable here)
  hidden=$(timeout 120 colab sessions 2>/dev/null | grep '^\[?\]')
  [ -n "$hidden" ] && alert "Colab: a runtime with no session name is assigned and may be billing; stop it at colab.research.google.com (Runtime > Manage sessions): $hidden"
done
[ -z "$GPU" ] && [ $BUSY = 1 ] && { echo "BUSY: every card asked for is already in use on this account"; exit 3; }
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
# Optional read-only Hugging Face token (owner-approved 2026-11-24): authenticated downloads with Xet on. The file
# never enters the repo, logs or OUT; on the runtime it lives in /content/.hf_token (mode 600) until the runtime stops.
HF_TOKEN_FILE=${HF_TOKEN_FILE:-$HOME/.config/motion-studio/hf_read_token}
if [ -s "$HF_TOKEN_FILE" ]; then
  up=$(timeout 300 colab upload -s $S "$HF_TOKEN_FILE" /content/.hf_token 2>&1); rc=$?
  if [ $rc -ne 0 ] || grep -q "Upload failed" <<< "$up"; then echo "token upload failed; downloads stay anonymous"
  else timeout 120 colab exec -s $S --timeout 60 <<< "import os; os.chmod('/content/.hf_token', 0o600)" > /dev/null; echo "HF token uploaded (read-only)"; fi
fi
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
# Setup runs detached on the runtime, not inside a long `colab exec`. Run inside the exec, a dropped connection right
# after "PHASE setup" left no setup at all (no setup_alive, three times on 2026-11-25 with custom setups doing a pip
# upgrade and a 20-40 GB download) or an exec that hung while the card billed (19 min, 2026-11-25). Now:
#   1. the script goes up with `colab upload` and is checked by size on the runtime;
#   2. a short exec starts it as its own process (nohup-style, output to /content/setup.log, exit code to
#      /content/setup_rc) and returns at once. It is idempotent: it won't start a second copy if one is running or
#      finished, so a start exec that dropped after starting is simply sent again (up to SETUP_SEEN s, 180; then exit 4);
#   3. short probes every 15 s read the log, the exit code and whether the process is alive. A probe that drops is
#      just the next probe. SETUP_PROBE_FAILS (8) misses in a row means the runtime stopped answering: exit 4.
# A setup that exits non-zero fails the run at once with its log (exit 1, retried after a wait, since a pip or download
# blip can pass) instead of the render waiting 45 min for it. No log growth and no setup_alive touch for SETUP_WAIT
# (2700 s) is a stuck setup: exit 1. Setup scripts need no change, and the render still finds its files as before.
: > setup.out
SZ=$(wc -c < "$SETUP" | tr -d ' ')
start_py() { cat <<PY
import os, subprocess, sys
s = '/content/setup.py'; p = '/content/setup_pid'
if not os.path.exists(s) or os.path.getsize(s) != $SZ:
    print('SETUP_BADUPLOAD', os.path.getsize(s) if os.path.exists(s) else -1)
else:
    pid = open(p).read().strip() if os.path.exists(p) else ''
    if not (pid and os.path.exists('/proc/' + pid)) and not os.path.exists('/content/setup_rc'):
        sh = sys.executable + ' -u ' + s + '; echo \$? > /content/setup_rc.part && mv /content/setup_rc.part /content/setup_rc'
        pr = subprocess.Popen(['/bin/bash', '-c', sh], cwd='/content', stdout=open('/content/setup.log', 'ab'),
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        open(p + '.part', 'w').write(str(pr.pid)); os.replace(p + '.part', p)
    print('SETUP_STARTED', open(p).read().strip())
PY
}
probe_py() { cat <<PY
import os, time
L = '/content/setup.log'; n = os.path.getsize(L) if os.path.exists(L) else 0
rc = open('/content/setup_rc').read().strip() if os.path.exists('/content/setup_rc') else '-'
pid = open('/content/setup_pid').read().strip() if os.path.exists('/content/setup_pid') else ''
last = max([os.path.getmtime(f) for f in (L, '/content/setup_alive') if os.path.exists(f)] or [time.time()])
print('SETUP_STATE', n, rc or '?', int(bool(pid) and os.path.exists('/proc/' + pid)), int(time.time() - last))
if n > $1:
    f = open(L, 'rb'); f.seek(max($1, n - 4096)); print(f.read().decode('utf-8', 'replace').replace('\r', '\n'))
PY
}
started=0; uploaded=0
while [ $(( $(date +%s) - T0 )) -lt "${SETUP_SEEN:-180}" ]; do
  if [ $uploaded = 0 ]; then
    up=$(timeout 600 colab upload -s $S "$SETUP" /content/setup.py 2>&1); rc=$?
    { [ $rc -ne 0 ] || grep -q "Upload failed" <<< "$up"; } && { echo "setup upload failed; retrying"; sleep 10; continue; }
    uploaded=1
  fi
  r=$(start_py | timeout 120 colab exec -s $S --timeout 90 2>&1)
  if grep -q '^SETUP_STARTED ' <<< "$r"; then started=1; echo "setup started on the runtime as process $(sed -n 's/^SETUP_STARTED //p' <<< "$r" | head -1)"; break; fi
  grep -q '^SETUP_BADUPLOAD' <<< "$r" && { echo "setup script arrived damaged ($(grep '^SETUP_BADUPLOAD' <<< "$r")); uploading again"; uploaded=0; }
  echo "setup start not confirmed (${r:0:160}); sending it again"; sleep 10
done
[ $started = 1 ] || { echo "FAILED: setup never started on the runtime (no confirmed start in ${SETUP_SEEN:-180} s)"; exit 4; }
off=0; miss=0
while :; do
  sleep "${SETUP_POLL:-15}"
  r=$(probe_py $off | timeout 90 colab exec -s $S --timeout 60 2>&1)
  st=$(grep -m1 '^SETUP_STATE ' <<< "$r")
  if [ -z "$st" ]; then
    miss=$((miss + 1))
    [ $miss -ge "${SETUP_PROBE_FAILS:-8}" ] && { echo "FAILED: the runtime stopped answering during setup ($miss probes in a row)"; exit 4; }
    continue
  fi
  miss=0; read -r _ n rc up idle <<< "$st"
  awk 'f; /^SETUP_STATE /{f=1}' <<< "$r" | tee -a setup.out
  off=$n
  if [ "$rc" != - ]; then
    [ "$rc" = 0 ] && break
    echo "FAILED: setup exited with code $rc; the end of its log:"; tail -20 setup.out; exit 1
  fi
  [ "$up" = 0 ] && { echo "FAILED: the setup process is gone without an exit code (killed, likely out of memory); the end of its log:"; tail -20 setup.out; exit 1; }
  [ "$idle" -ge "${SETUP_WAIT:-2700}" ] && { echo "FAILED: setup silent for $idle s (no log output, no setup_alive)"; exit 1; }
done
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
# A crash in the kernel prints IPython's traceback ("OutOfMemoryError   Traceback (most recent call last)"), which
# doesn't start a line with "Traceback": match the word anywhere (a T4 OOM sat 10 min as "detached", 2026-11-25).
# A detached render is also probed: a kernel that answers a one-line exec at once is idle, so the render is dead.
kernel_idle() { timeout 90 colab exec -s $S --timeout 45 <<< "print('KERNEL' + '_IDLE')" 2>/dev/null | grep -q KERNEL_IDLE; }
if [ ! -e .stalled ] && ! grep -q "RENDER DONE" render.out && ! grep -qE "Traceback|FAILED" render.out; then
  echo "PHASE render detached: the exec returned early; waiting on the puller for RENDER_DONE"
  n=0
  while kill -0 "$PULL" 2>/dev/null; do
    stalled && break
    if [ $((n % 30)) = 3 ] && ! timeout 120 colab ls -s $S /content/out 2>/dev/null | grep -qx RENDER_DONE && kernel_idle; then
      echo "FAILED: the render is no longer running on the runtime (kernel idle, no RENDER_DONE)" | tee -a render.out usage.log
      break
    fi
    n=$((n + 1)); sleep 10
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
