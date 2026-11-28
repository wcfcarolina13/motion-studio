# MIN_VRAM_GB: 40 (LTX-2.5, 22B transformer: an A100; at 24 GB it would not fit even stored in FP8 beside its activations).
# LTX-2.5 image-to-video setup on Colab: the test lane that runs beside Wan 2.2 (run_ab.sh; owner's pick, 2026-11-28).
# Licence: LTX-2.x Community License (Lightricks, amended 2026-11-11): free for commercial use under $10M yearly revenue,
# no territory exclusion, no rights claimed in outputs; outputs must be disclosed as machine-generated, the model's
# watermark kept, and outputs not used to train other models. The weights are gated: the Hugging Face account behind
# the read-only token (HF_TOKEN_FILE) must have accepted the terms, which run_ab.sh checks before asking for a GPU.
import json, os, subprocess, sys, time
open('/content/setup_alive', 'w').write(str(time.time()))     # first thing: run_colab.sh checks it to tell a live setup from a dropped one
alive = lambda: open('/content/setup_alive', 'w').write(str(time.time()))
REPO = os.environ.get('LTX_REPO') or (open('/content/in/ltx_repo').read().strip() if os.path.exists('/content/in/ltx_repo') else
                                      'Lightricks/LTX-2.5-Diffusers')
TOKEN = '/content/.hf_token'
if not (os.path.exists(TOKEN) and open(TOKEN).read().strip()):
    raise SystemExit(f'FAILED: {REPO} is gated and no Hugging Face token was uploaded (HF_TOKEN_FILE on the Mac)')
os.environ['HF_TOKEN'] = open(TOKEN).read().strip(); os.environ['HF_XET_HIGH_PERFORMANCE'] = '1'
# LTX-2.5 is not in a diffusers release yet (model card, 2026-11): install from source, then check the classes load,
# so a missing class fails here and not after a 66 GB download.
pip = lambda *a: subprocess.run([sys.executable, '-m', 'pip', '-q', 'install', '-U', *a], check=True)
pip('git+https://github.com/huggingface/diffusers', 'transformers', 'accelerate', 'sentencepiece', 'imageio', 'imageio-ffmpeg')
alive()
import huggingface_hub as hub
try:
    idx = json.load(open(hub.hf_hub_download(REPO, 'model_index.json')))
except Exception as e:
    raise SystemExit(f'FAILED: cannot read {REPO} ({type(e).__name__}: {str(e)[:200]}). If it says gated or 403, accept the '
                     f'terms at https://huggingface.co/{REPO} with the account behind the token')
cls = idx.get('_class_name', '')
want = os.environ.get('LTX_PIPELINE', 'LTX2ImageToVideoPipeline')
probe = subprocess.run([sys.executable, '-c', f'import diffusers; getattr(diffusers, {want!r})'], capture_output=True)
if probe.returncode:
    raise SystemExit(f'FAILED: diffusers from source has no {want} (the repo names {cls}); check the model card\'s loading code')
print('pipeline', want, '| repo class', cls, flush=True)
# Whole repo but the duplicate formats: the Diffusers repo holds one pipeline's folders. Its size is printed first,
# so a repo that grew (a second transformer) shows in the log before the download.
info = hub.model_info(REPO, files_metadata=True)
skip = ('.md', '.gitattributes', '.png', '.jpg', '.mp4', '.gif')
files = [s for s in info.siblings if not s.rfilename.endswith(skip)]
print(f'downloading {len(files)} files, {sum((s.size or 0) for s in files) / 2**30:.1f} GiB', flush=True)
for n in range(6):
    alive()
    try:
        P = hub.snapshot_download(REPO, ignore_patterns=[f'*{x}' for x in skip]); break
    except Exception as e:
        if n == 5: raise
        print('download retry', n + 1, repr(e)[:150], flush=True)
        if n >= 1 and os.environ.get('HF_HUB_DISABLE_XET') != '1':      # Xet failed twice even with a token: plain HTTP
            os.environ['HF_HUB_DISABLE_XET'] = '1'; hub.constants.HF_HUB_DISABLE_XET = True
        for _ in range(min(10, 2 ** n)): time.sleep(30); alive()
open('/content/tmp_ltx_path.txt', 'w').write(P); os.replace('/content/tmp_ltx_path.txt', '/content/ltx_path.txt')
print('SETUP DONE', flush=True)
