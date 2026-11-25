# Z-Image Turbo stills on Colab (Tongyi-MAI/Z-Image-Turbo, weights Apache-2.0, licence read from the model card
# 2026-11-24). MIN_VRAM_GB: 20 (a T4's 14.6 GB ran out of memory on the first still, 2026-11-25). Ported from the Shoestring stills route that rendered about 31 s a still on an L4. Run it through
# run_colab.sh with SETUP=remote_zimage_setup.py RENDER=remote_zimage_render.py; inputs are described there.
import os, subprocess, sys, time
open('/content/setup_alive', 'w').write(str(time.time()))     # first thing: run_colab.sh checks it (#109)
alive = lambda: open('/content/setup_alive', 'w').write(str(time.time()))
# Same download rules as remote_setup.py: authenticated with Xet on when the owner's read-only token was uploaded,
# else anonymous over plain HTTP (Xet answered 429 on anonymous runs).
TOKEN = '/content/.hf_token'
if os.path.exists(TOKEN) and open(TOKEN).read().strip():
    os.environ['HF_TOKEN'] = open(TOKEN).read().strip(); os.environ['HF_XET_HIGH_PERFORMANCE'] = '1'
    print('downloads: authenticated, Xet on', flush=True)
else:
    os.environ['HF_HUB_DISABLE_XET'] = '1'; print('downloads: anonymous, Xet off', flush=True)
# ZImagePipeline first shipped in diffusers 0.36; a released version is pinned when it has the pipeline, and the
# source build is only the fallback, because an unpinned source install can change under a run.
pip = lambda *a: subprocess.run([sys.executable, '-m', 'pip', '-q', 'install', *a], check=True)
pip('diffusers>=0.36', 'transformers', 'accelerate')
probe = subprocess.run([sys.executable, '-c', 'from diffusers import ZImagePipeline'], capture_output=True)
if probe.returncode:
    print('released diffusers lacks ZImagePipeline; installing from source', flush=True)
    pip('git+https://github.com/huggingface/diffusers')
alive()
import huggingface_hub as hub
for n in range(6):
    alive()
    try:
        P = hub.snapshot_download('Tongyi-MAI/Z-Image-Turbo', allow_patterns=['model_index.json', 'scheduler/*', 'text_encoder/*',
                                                                         'tokenizer/*', 'transformer/*', 'vae/*'])
        break
    except Exception as e:
        if n == 5: raise
        print('download retry', n + 1, repr(e)[:150], flush=True)
        if n >= 1 and os.environ.get('HF_HUB_DISABLE_XET') != '1':     # Xet failed twice even with a token: plain HTTP
            os.environ['HF_HUB_DISABLE_XET'] = '1'; hub.constants.HF_HUB_DISABLE_XET = True
        for _ in range(n + 1): time.sleep(30); alive()
open('/content/tmp_zimage_path.txt', 'w').write(P); os.replace('/content/tmp_zimage_path.txt', '/content/zimage_path.txt')
print('SETUP DONE', flush=True)
