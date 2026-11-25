#!/bin/bash
# Splits a batch across parallel Colab runtimes ("lanes"), each its own retry_colab.sh run with its own session.
#   run_lanes.sh <in_dir> [out_dir] [lanes]     lanes defaults to COLAB_LANES or 2, the owner's plan limit (2026-11-24:
#   L4+T4 and A100+L4 ran together; a third runtime got "precondition failed").
# Jobs in jobs.json are dealt round-robin, so each lane gets a similar mix. Each lane writes to <out_dir>/.lane-<k>
# (its own logs, manifest and stop file), and its finished files are moved into out_dir as soon as the lane ends.
# The account holds about one runtime per GPU type (2026-11-24: a second L4 was refused while one ran), so each lane
# asks for a different card: lane k gets the k-th card of the order (COLAB_GPUS, else run_colab.sh's default: "L4"
# for Wan, "L4 T4" for other jobs). COLAB_GPUS_<k> overrides one lane. With fewer cards than lanes, the extra lanes
# are dropped, so a Wan batch on the default runs as one lane unless COLAB_GPUS_2 names another card (A100 costs about
# five times an L4). A lane whose card is busy waits (retry_colab.sh, exit 3). Exit 0 when every lane finished;
# otherwise the worst lane's code (2 over 1).
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: run_lanes.sh <in_dir> [out_dir] [lanes]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../out}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
N=${3:-${COLAB_LANES:-2}}
[ -f "$IN/jobs.json" ] || { echo "run_lanes.sh needs $IN/jobs.json to split"; exit 2; }
MAX=${COLAB_MAX_LANES:-2}
[ "$N" -gt "$MAX" ] && { echo "$N lanes asked, plan allows $MAX at once (COLAB_MAX_LANES); using $MAX"; N=$MAX; }
SETUP=${SETUP:-$HERE/remote_setup.py}; RENDER=${RENDER:-$HERE/remote_render.py}
[[ $SETUP = /* ]] || SETUP=$HERE/$SETUP; [[ $RENDER = /* ]] || RENDER=$HERE/$RENDER
need=$(grep -o 'MIN_VRAM_GB: *[0-9]*' "$SETUP" 2>/dev/null | grep -o '[0-9]*$' | head -1)     # same rule as run_colab.sh
if [ "${need:-0}" -gt 16 ] || { [ "$SETUP" = "$HERE/remote_setup.py" ] && [ "$RENDER" = "$HERE/remote_render.py" ]; }; then CARDS=(${COLAB_GPUS:-L4}); else CARDS=(${COLAB_GPUS:-L4 T4}); fi
lane_gpus() { local g="COLAB_GPUS_$1"; [ -n "${!g:-}" ] && { echo "${!g}"; return; }; echo "${CARDS[$(($1 - 1))]:-}"; }
for ((k = N; k > 1; k--)); do [ -n "$(lane_gpus $k)" ] && break; echo "lane $k has no card of its own (cards: ${CARDS[*]}); dropping it"; N=$((k - 1)); done
TMP=$(mktemp -d "${TMPDIR:-/tmp}/colab-lanes.XXXXXX")
python3 - "$IN" "$OUT" "$TMP" "$N" <<'PY' || { rm -rf "$TMP"; exit 2; }
import json, os, sys
src, out, tmp, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
jobs = json.load(open(os.path.join(src, "jobs.json")))
have = {x.rsplit('.', 1)[0] for x in os.listdir(out) if not x.startswith('.') and '.' in x and os.path.getsize(os.path.join(out, x)) > 0}
left = [j for j in jobs if str(j["id"]) not in have]
print(f"jobs: {len(jobs) - len(left)} of {len(jobs)} already have output; {len(left)} over {min(n, len(left))} lane(s)")
for k in range(min(n, len(left))):
    d = os.path.join(tmp, f"in-{k + 1}")
    os.makedirs(d)
    for f in os.listdir(src):
        if f != "jobs.json":
            os.symlink(os.path.join(src, f), os.path.join(d, f))
    json.dump(left[k::n], open(os.path.join(d, "jobs.json"), "w"), indent=1)
PY
pids=(); lanes=()
for d in "$TMP"/in-*; do
  [ -d "$d" ] || continue
  k=${d##*-}; LO="$OUT/.lane-$k"; mkdir -p "$LO"
  gpus=$(lane_gpus $k)
  [ "$N" -eq 1 ] && [ -z "${COLAB_GPUS_1:-}" ] && gpus="${CARDS[*]}"     # one lane keeps the whole fallback order
  ( export COLAB_GPUS="$gpus" SETUP RENDER; unset COLAB_SESSION
    /bin/bash "$HERE/retry_colab.sh" "$d" "$LO" > "$LO/lane.log" 2>&1; rc=$?
    # move finished files up; logs and dotfiles stay in the lane folder
    for f in "$LO"/*; do [ -f "$f" ] && [ -s "$f" ] && case $(basename "$f") in lane.log|usage.log|render.out|pull.log) ;; *) mv -f "$f" "$OUT/";; esac; done
    exit $rc ) &
  pids+=($!); lanes+=($k); echo "lane $k started on $gpus (log $LO/lane.log)"
done
[ ${#pids[@]} -eq 0 ] && { rm -rf "$TMP"; echo "every job has output"; exit 0; }
worst=0
for i in "${!pids[@]}"; do
  wait "${pids[$i]}"; rc=$?; echo "lane ${lanes[$i]} ended: exit $rc"
  [ $rc -eq 2 ] && worst=2; [ $rc -ne 0 ] && [ $rc -ne 2 ] && [ $worst -eq 0 ] && worst=1
done
rm -rf "$TMP"
exit $worst
