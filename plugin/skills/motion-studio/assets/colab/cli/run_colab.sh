#!/bin/bash
# Full-quality Wan 2.2 14B clips on Colab via the official CLI. Always stops the session at the end.
#   run_colab.sh <in_dir> [out_dir]    in_dir holds the images and jobs.json; out_dir defaults to <in_dir>/../out
#   SETUP=x.py RENDER=y.py swap in other remote scripts; each must print "OUTFILE <name>" for every file it writes to /content/out.
#   Exit 1 means no GPU or a dead runtime: retry_colab.sh retries those every 20 min.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: run_colab.sh <in_dir> [out_dir]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../out}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
cd "$OUT"
S=motion-studio-wan
SETUP=${SETUP:-$HERE/remote_setup.py}; RENDER=${RENDER:-$HERE/remote_render.py}
[[ $SETUP = /* ]] || SETUP=$HERE/$SETUP; [[ $RENDER = /* ]] || RENDER=$HERE/$RENDER
cleanup() { echo "PHASE stop"; colab stop -s $S || true; colab usage | tee -a usage.log; }
trap cleanup EXIT
date '+start %F %T' | tee -a usage.log; colab usage | tee -a usage.log
echo "PHASE new"
GPU=""
for g in G4 A100 L4; do
  if timeout 1800 colab new -s $S --gpu $g --high-mem; then GPU=$g; break; fi
  echo "no $g, trying next"; colab stop -s $S 2>/dev/null || true
done
[ -z "$GPU" ] && { echo "FAILED: no GPU"; exit 1; }
echo "GPU $GPU" | tee -a usage.log
# A dropped connection right after allocation once left this exec hanging 15 min while units burned: cap it.
timeout 300 colab exec -s $S --timeout 120 <<< "import os; os.makedirs('/content/in', exist_ok=True)" || { echo "FAILED: runtime not responding"; exit 1; }
# The CLI can print "Upload failed" (HTTP 500 from a broken runtime) and still exit 0.
for f in "$IN"/*; do
  timeout 600 colab upload -s $S "$f" "/content/in/$(basename "$f")" 2>&1 | tee /dev/stderr | grep -q "Upload failed" && { echo "FAILED: upload $f"; exit 1; }
done
echo "PHASE setup"; T0=$(date +%s)
# "Connection was lost" here does not stop the remote setup; the render script waits for it, so carry on.
colab exec -s $S -f "$SETUP" --timeout 0 || echo "setup exec returned an error; render will wait for setup to finish"
echo "setup $(( $(date +%s)-T0 ))s" | tee -a usage.log
echo "PHASE render"; T0=$(date +%s)
colab exec -s $S -f "$RENDER" --timeout 0 2>&1 | tee render.out
grep -q "RENDER DONE" render.out || echo "FAILED: render did not finish; downloading whatever exists"
echo "render $(( $(date +%s)-T0 ))s" | tee -a usage.log
for f in $(grep -o 'OUTFILE [^ ]*' render.out | cut -d' ' -f2 | sort -u); do
  colab download -s $S "/content/out/$f" "$OUT/$f" && echo "got $f"
done
date '+end %F %T' | tee -a usage.log
