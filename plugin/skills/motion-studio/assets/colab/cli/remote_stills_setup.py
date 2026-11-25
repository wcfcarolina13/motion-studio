# Stills setup on Colab for the model in /content/in/model.json (written by shots_to_jobs.py from stills_models.json):
# installs diffusers and downloads the weights. MIN_VRAM_GB: 20 (the smallest stills model; the in_dir's min_vram_gb file
# raises it per model, so Qwen-Image goes to an A100). Run it through run_colab.sh or run_lanes.sh with
# SETUP=remote_stills_setup.py RENDER=remote_stills_render.py.
import json, os, subprocess, sys, time
open('/content/setup_alive', 'w').write(str(time.time()))     # first thing: run_colab.sh checks it (#109)
alive = lambda: open('/content/setup_alive', 'w').write(str(time.time()))
M = json.load(open('/content/in/model.json')) if os.path.exists('/content/in/model.json') else \
    {'repo': 'Tongyi-MAI/Z-Image', 'pipeline': 'ZImagePipeline', 'diffusers': '0.36'}
print('stills model', M.get('name', M['repo']), '(DRAFT: layout only)' if M.get('draft') else '', flush=True)
# Same download rules as remote_setup.py: authenticated with Xet on when the owner's read-only token was uploaded,
# else anonymous over plain HTTP (Xet answered 429 on anonymous runs).
TOKEN = '/content/.hf_token'
if os.path.exists(TOKEN) and open(TOKEN).read().strip():
    os.environ['HF_TOKEN'] = open(TOKEN).read().strip(); os.environ['HF_XET_HIGH_PERFORMANCE'] = '1'
    print('downloads: authenticated, Xet on', flush=True)
else:
    os.environ['HF_HUB_DISABLE_XET'] = '1'; print('downloads: anonymous, Xet off', flush=True)
# A released diffusers is pinned when it has the model's pipeline; the source build is only the fallback, because an
# unpinned source install can change under a run.
pip = lambda *a: subprocess.run([sys.executable, '-m', 'pip', '-q', 'install', *a], check=True)
pip(f"diffusers>={M.get('diffusers', '0.36')}", 'transformers', 'accelerate')
probe = subprocess.run([sys.executable, '-c', f"from diffusers import {M.get('pipeline', 'DiffusionPipeline')}"], capture_output=True)
if probe.returncode:
    print('released diffusers lacks', M.get('pipeline'), '; installing from source', flush=True)
    pip('git+https://github.com/huggingface/diffusers')
alive()
import huggingface_hub as hub
for n in range(6):
    alive()
    try:
        P = hub.snapshot_download(M['repo'], allow_patterns=['model_index.json', 'scheduler/*', 'text_encoder/*', 'tokenizer/*',
                                                             'processor/*', 'transformer/*', 'vae/*'])
        break
    except Exception as e:
        if n == 5: raise
        print('download retry', n + 1, repr(e)[:150], flush=True)
        if n >= 1 and os.environ.get('HF_HUB_DISABLE_XET') != '1':     # Xet failed twice even with a token: plain HTTP
            os.environ['HF_HUB_DISABLE_XET'] = '1'; hub.constants.HF_HUB_DISABLE_XET = True
        for _ in range(n + 1): time.sleep(30); alive()
open('/content/tmp_model_path.txt', 'w').write(P); os.replace('/content/tmp_model_path.txt', '/content/model_path.txt')
print('SETUP DONE', flush=True)
