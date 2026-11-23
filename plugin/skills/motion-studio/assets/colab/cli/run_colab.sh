#!/bin/bash
# Full-quality Wan 2.2 14B clips on Colab via the official CLI. Always stops the session at the end.
#   run_colab.sh <in_dir> [out_dir]    in_dir holds the images and jobs.json; out_dir defaults to <in_dir>/../out
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: run_colab.sh <in_dir> [out_dir]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../out}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
cd "$OUT"
S=motion-studio-wan
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
colab exec -s $S --timeout 0 <<< "import os; os.makedirs('/content/in', exist_ok=True)"
for f in "$IN"/*; do colab upload -s $S "$f" "/content/in/$(basename "$f")" || { echo "FAILED: upload $f"; exit 1; }; done
echo "PHASE setup"; T0=$(date +%s)
colab exec -s $S -f "$HERE/remote_setup.py" --timeout 0 || { echo "FAILED: setup"; exit 1; }
echo "setup $(( $(date +%s)-T0 ))s" | tee -a usage.log
echo "PHASE render"; T0=$(date +%s)
colab exec -s $S -f "$HERE/remote_render.py" --timeout 0 || { echo "FAILED: render"; exit 1; }
echo "render $(( $(date +%s)-T0 ))s" | tee -a usage.log
for id in $(python3 -c "import json;print(' '.join(j['id'] for j in json.load(open('$IN/jobs.json'))))"); do
  colab download -s $S /content/out/$id.mp4 "$OUT/$id.mp4" && echo "got $id"
done
date '+end %F %T' | tee -a usage.log
