#!/bin/bash
# Full-quality Wan 2.2 14B clips on Colab via the official CLI. Always stops the session at the end.
set -uo pipefail
export PATH=$HOME/.local/bin:$PATH
cd ~/pontus/experiments/shoestring-anime-test/colab/cli
S=shoestring; IN=../in; OUT=out; mkdir -p $OUT
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
for f in $IN/*; do colab upload -s $S "$f" "/content/in/$(basename "$f")"; done
echo "PHASE setup"; T0=$(date +%s)
colab exec -s $S -f remote_setup.py --timeout 0
echo "setup $(( $(date +%s)-T0 ))s" | tee -a usage.log
echo "PHASE render"; T0=$(date +%s)
colab exec -s $S -f remote_render.py --timeout 0
echo "render $(( $(date +%s)-T0 ))s" | tee -a usage.log
for id in $(python3 -c "import json;print(' '.join(j['id'] for j in json.load(open('$IN/jobs.json'))))"); do
  colab download -s $S /content/out/$id.mp4 $OUT/$id.mp4 && echo "got $id"
done
date '+end %F %T' | tee -a usage.log
