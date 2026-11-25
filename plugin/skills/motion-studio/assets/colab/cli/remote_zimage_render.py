# Renders the stills in /content/in/jobs.json with Z-Image Turbo: 9 steps, guidance 0, 1344x768 unless a job says.
# jobs.json: [{"id": "s01_s7", "prompt": "...", "seed": 7, "style"?: "...", "w"?, "h"?}]; style.txt (optional) is the
# batch style, appended to every prompt unless the job carries its own, so day, night and diagram shots share one
# runtime (setup costs about 15 min against a few minutes of rendering). One job is one take: make takes with
# shots_to_jobs.py, so an id is the output's name and retry_colab.sh resends only the missing takes.
import hashlib, json, os, time
import torch
start = time.time()
while not os.path.exists('/content/zimage_path.txt'):
    last = max(start, os.path.getmtime('/content/setup_alive') if os.path.exists('/content/setup_alive') else 0)
    if time.time() - last >= int(os.environ.get('SETUP_WAIT', 2700)):
        raise SystemExit('FAILED: setup did not finish (no sign of life for %d s)' % (time.time() - last))
    print('waiting for setup', flush=True); time.sleep(30)
from diffusers import ZImagePipeline
# The 6B transformer alone is about 12 GB in 16-bit: a card under 20 GB (a T4) runs out of memory on the first still,
# so it is refused before the weights load. bf16 on L4 and A100.
if torch.cuda.get_device_properties(0).total_memory / 1e9 < 20:
    raise SystemExit('FAILED: Z-Image needs a card with 20 GB or more (L4, A100); this one has %.1f GB'
                     % (torch.cuda.get_device_properties(0).total_memory / 1e9))
dt = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
pipe = ZImagePipeline.from_pretrained(open('/content/zimage_path.txt').read().strip(), torch_dtype=dt, low_cpu_mem_usage=False)
vram = torch.cuda.get_device_properties(0).total_memory / 1e9
pipe.to('cuda') if vram >= 30 else pipe.enable_model_cpu_offload()
print('pipeline on', torch.cuda.get_device_name(0), round(vram), 'GB', dt, flush=True)
jobs = json.load(open('/content/in/jobs.json'))
style = open('/content/in/style.txt').read().strip() if os.path.exists('/content/in/style.txt') else ''
os.makedirs('/content/out', exist_ok=True); os.makedirs('/content/tmp', exist_ok=True)


def publish(tmp, name):
    """Same contract as remote_render.py: whole file into /content/out, then size and sha256 into MANIFEST.tsv."""
    h = hashlib.sha256()
    with open(tmp, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    os.replace(tmp, f'/content/out/{name}')
    m = '/content/out/MANIFEST.tsv'
    old = open(m).read() if os.path.exists(m) else ''
    open('/content/tmp/MANIFEST.tsv', 'w').write(old + f"{name}\t{os.path.getsize(f'/content/out/{name}')}\t{h.hexdigest()}\n")
    os.replace('/content/tmp/MANIFEST.tsv', m)


bad = 0
for j in jobs:
    name = f"{j['id']}.png"
    if os.path.exists('/content/out/' + name): print(f'OUTFILE {name}', flush=True); continue
    t0 = time.time()
    prompt = f"{j['prompt']} {j.get('style', style)}".strip()
    img = pipe(prompt=prompt, height=j.get('h', 768), width=j.get('w', 1344), num_inference_steps=j.get('steps', 9),
               guidance_scale=0.0, generator=torch.Generator('cuda').manual_seed(int(j.get('seed', 7)))).images[0]
    lo, hi = img.convert('L').getextrema()
    if hi - lo < 8:
        bad += 1; print(f'FAILED {name}: blank image (levels {lo}-{hi})', flush=True); continue
    img.save('/content/tmp/' + name); publish('/content/tmp/' + name, name)
    print(f'OUTFILE {name}', flush=True); print(f'STILL {name} {time.time() - t0:.1f}s', flush=True)
open('/content/tmp/RENDER_DONE', 'w').write(f'done, {bad} failed'); os.replace('/content/tmp/RENDER_DONE', '/content/out/RENDER_DONE')
print('RENDER DONE', flush=True)
