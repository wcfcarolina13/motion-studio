#!/bin/bash
# Colab often answers "Service Unavailable" for every GPU, or hands over a runtime that drops at once.
# Retries run_colab.sh every 20 min, up to 9 tries (about 3 h). Same arguments and env as run_colab.sh.
# A job runner may snapshot scripts under other names, so this resolves its sibling by absolute path.
# Each try sends only the jobs in jobs.json with no output yet: a job counts as done when out_dir holds a non-empty
# file named after its id (<id>.mp4, <id>.png, ...), which is what pull_live.sh leaves for every finished file.
HERE=$(cd "$(dirname "$0")" && pwd)
R=${RUNNER:-$HERE/run_colab.sh}
[ $# -ge 1 ] || { echo "usage: retry_colab.sh <in_dir> [out_dir]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../out}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)

# Prints the folder to send: in_dir itself, or a copy (symlinks) whose jobs.json lists only the jobs still to do.
# Prints nothing when every job is done.
todo_dir() {
  [ -f "$IN/jobs.json" ] || { echo "$IN"; return; }
  local d; d=$(mktemp -d "${TMPDIR:-/tmp}/colab-in.XXXXXX")
  for f in "$IN"/*; do [ "$(basename "$f")" = jobs.json ] || ln -s "$f" "$d/"; done
  python3 - "$IN/jobs.json" "$OUT" "$d/jobs.json" <<'EOF'; local rc=$?
import json, os, sys
src, out, dest = sys.argv[1:]
jobs = json.load(open(src))
have = {n.rsplit('.', 1)[0] for n in os.listdir(out) if not n.startswith('.') and '.' in n and os.path.getsize(os.path.join(out, n)) > 0}
left = [j for j in jobs if str(j['id']) not in have]
print(f"jobs: {len(jobs) - len(left)} of {len(jobs)} already have output", file=sys.stderr)
json.dump(left, open(dest, 'w'), indent=1)
sys.exit(0 if left else 3)
EOF
  [ $rc -eq 0 ] && { echo "$d"; return 0; }
  rm -rf "$d"; [ $rc -eq 3 ]      # 3: nothing left to do (prints nothing); anything else is an error
}

# Exit 3 (the cards are busy on this account) waits 5 min and does not count as a try, up to BUSY_MAX seconds (6 h).
BUSY_MAX=${BUSY_MAX:-21600}; BUSY_WAIT=${BUSY_WAIT:-300}; busy=0; i=1
while [ $i -le 9 ]; do
  echo "PHASE try $i"
  d=$(todo_dir); rc=$?
  [ $rc -ne 0 ] && { echo "FAILED: could not read $IN/jobs.json"; exit 2; }
  [ -z "$d" ] && { echo "every job has output"; exit 0; }
  /bin/bash "$R" "$d" "$OUT"; rc=$?
  [ "$d" != "$IN" ] && rm -rf "$d"
  if [ $rc -eq 3 ]; then
    [ $busy -ge "$BUSY_MAX" ] && { echo "FAILED: cards still busy after $busy s"; exit 1; }
    echo "cards busy; waiting $BUSY_WAIT s (not a try)"; sleep "$BUSY_WAIT"; busy=$((busy + BUSY_WAIT)); continue
  fi
  [ $rc -eq 4 ] && { echo "setup never started; retrying at once"; i=$((i + 1)); continue; }
  [ $rc -ne 1 ] && exit $rc        # 0 done, 2 render didn't finish: only 1 (no GPU, dead runtime) is worth retrying
  [ $i -lt 9 ] && sleep 1200
  i=$((i + 1))
done
exit 1
