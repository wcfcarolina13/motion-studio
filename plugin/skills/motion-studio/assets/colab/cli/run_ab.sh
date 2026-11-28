#!/bin/bash
# One clip batch on Wan 2.2 (the free default) and LTX-2.5 (the test model, owner's pick 2026-11-28) at once, each on
# its own Colab runtime, then a side-by-side clip per job for review.
#   run_ab.sh <in_dir> [out_dir]
# in_dir is a Wan batch (images + jobs.json, as for run_colab.sh). Outputs: <out_dir>/wan/<id>.mp4,
# <out_dir>/ltx/<id>.mp4 and <out_dir>/ab/<id>.mp4 (Wan left, LTX right, both at 480 px high, the shorter one holding its
# last frame). out_dir defaults to <in_dir>/../ab-out.
# The two lanes take two different cards, the plan's limit of two runtimes: Wan on an L4, LTX on an A100 (it needs about
# 40 GB; an A100 bills about five times an L4, colab.md). COLAB_GPUS_WAN / COLAB_GPUS_LTX override them.
# Before any GPU is asked for, it checks on this machine that the Hugging Face token can read the gated LTX weights, so
# a missing licence click fails here, not after a runtime has started billing.
# Exit 0 when both lanes finished; otherwise the worse lane's code (2 over 1), as run_lanes.sh.
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 1 ] || { echo "usage: run_ab.sh <in_dir> [out_dir]"; exit 2; }
IN=$(cd "$1" && pwd); OUT=${2:-$IN/../ab-out}; mkdir -p "$OUT"/{wan,ltx,ab}; OUT=$(cd "$OUT" && pwd)
[ -f "$IN/jobs.json" ] || { echo "run_ab.sh needs $IN/jobs.json"; exit 2; }
REPO=${LTX_REPO:-Lightricks/LTX-2.5-Diffusers}
TOK=${HF_TOKEN_FILE:-$HOME/.config/motion-studio/hf_read_token}
[ -s "$TOK" ] || { echo "run_ab: no Hugging Face token at $TOK; LTX-2.5 is gated, so it needs one (colab.md)"; exit 2; }
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(cat "$TOK")" "https://huggingface.co/$REPO/resolve/main/model_index.json")
case $code in
  200|302|307) echo "LTX weights readable with the token ($REPO)";;
  401|403) echo "run_ab: the token can't read $REPO (HTTP $code): accept the terms at https://huggingface.co/$REPO with the account the token belongs to"; exit 2;;
  *) echo "run_ab: Hugging Face answered HTTP $code for $REPO; not starting"; exit 2;;
esac
# the Wan lint once, for both lanes: the same stills go to both, and its rules (picture-truth rule 13) were measured on Wan
if [ "${WAN_LINT:-on}" != off ]; then
  python3 "$HERE/wan_lint.py" "$IN/jobs.json" || { echo "run_ab: jobs refused (above); fix them or WAN_LINT=off"; exit 2; }
fi
export WAN_LINT=off
( export COLAB_GPUS=${COLAB_GPUS_WAN:-L4}; unset COLAB_SESSION SETUP RENDER
  /bin/bash "$HERE/retry_colab.sh" "$IN" "$OUT/wan" > "$OUT/wan/lane.log" 2>&1 ) & pw=$!
( export COLAB_GPUS=${COLAB_GPUS_LTX:-A100} SETUP=remote_ltx_setup.py RENDER=remote_ltx_render.py LTX_REPO="$REPO"; unset COLAB_SESSION
  /bin/bash "$HERE/retry_colab.sh" "$IN" "$OUT/ltx" > "$OUT/ltx/lane.log" 2>&1 ) & pl=$!
echo "Wan lane (log $OUT/wan/lane.log) and LTX lane (log $OUT/ltx/lane.log) started"
wait $pw; rw=$?; echo "Wan lane ended: exit $rw"
wait $pl; rl=$?; echo "LTX lane ended: exit $rl"
# side by side for every job both lanes finished
python3 - "$IN/jobs.json" "$OUT" <<'PY'
import json, os, subprocess, sys
jobs, out = json.load(open(sys.argv[1])), sys.argv[2]
made = 0
for j in jobs:
    w, l, ab = (os.path.join(out, d, f"{j['id']}.mp4") for d in ("wan", "ltx", "ab"))
    if not (os.path.getsize(w) if os.path.exists(w) else 0) or not (os.path.getsize(l) if os.path.exists(l) else 0) or os.path.exists(ab):
        continue
    # both at 480 px high and 24 fps; the shorter clip holds its last frame so the two end together
    vf = ("[0:v]scale=-2:480,fps=24,tpad=stop=-1:stop_mode=clone[a];[1:v]scale=-2:480,fps=24,tpad=stop=-1:stop_mode=clone[b];"
          "[a][b]hstack=inputs=2:shortest=0[v]")
    dur = max(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                   capture_output=True, text=True).stdout.strip() or 0) for p in (w, l))
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", w, "-i", l, "-filter_complex", vf, "-map", "[v]", "-t", f"{dur:.2f}",
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", ab])
    made += r.returncode == 0
print(f"{made} side-by-side clip(s) in {os.path.join(out, 'ab')}")
PY
[ $rw -eq 2 ] || [ $rl -eq 2 ] && exit 2
[ $rw -ne 0 ] || [ $rl -ne 0 ] && exit 1
exit 0
