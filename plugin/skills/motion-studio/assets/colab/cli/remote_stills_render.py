# Renders the stills in /content/in/jobs.json with the model in /content/in/model.json (stills_models.json via
# shots_to_jobs.py): its size and pipeline settings, which a job may override ("w", "h", "steps"). Every still carries
# PNG text "motion-studio-model" (and "motion-studio-draft" for a draft model), which render checks before it uses it.
# jobs.json: [{"id": "s01_s7", "prompt": "...", "seed": 7, "style"?: "...", "w"?, "h"?}]; style.txt (optional) is the
# batch style, appended to every prompt unless the job carries its own, so day, night and diagram shots share one
# runtime (setup costs about 15 min against a few minutes of rendering). One job is one take: make takes with
# shots_to_jobs.py, so an id is the output's name and retry_colab.sh resends only the missing takes.
import hashlib, json, os, time
import torch
start = time.time()
M = json.load(open('/content/in/model.json')) if os.path.exists('/content/in/model.json') else \
    {'name': 'z-image', 'w': 1344, 'h': 768, 'call': {'num_inference_steps': 40, 'guidance_scale': 4.0}, 'min_vram_gb': 20}
while not os.path.exists('/content/model_path.txt'):
    last = max(start, os.path.getmtime('/content/setup_alive') if os.path.exists('/content/setup_alive') else 0)
    if time.time() - last >= int(os.environ.get('SETUP_WAIT', 2700)):
        raise SystemExit('FAILED: setup did not finish (no sign of life for %d s)' % (time.time() - last))
    print('waiting for setup', flush=True); time.sleep(30)
from diffusers import DiffusionPipeline
from PIL.PngImagePlugin import PngInfo
# Refused before the weights load when the card is too small: a T4's 14.6 GB ran out of memory on Z-Image's first still
# (2026-11-25). bf16 where the card has it.
vram = torch.cuda.get_device_properties(0).total_memory / 1e9
if vram < M.get('min_vram_gb', 20) * 0.9:
    raise SystemExit('FAILED: %s needs a card with about %d GB or more; this one has %.1f GB' % (M.get('name'), M.get('min_vram_gb', 20), vram))
dt = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
# DiffusionPipeline reads the class from the model's own model_index.json, so a new model needs no code here
pipe = DiffusionPipeline.from_pretrained(open('/content/model_path.txt').read().strip(), torch_dtype=dt, low_cpu_mem_usage=False)
# whole model on the card when it fits with room; otherwise one component at a time (slower, same pictures)
# (model offload moves a whole component at once: Qwen's 40 GB transformer overflowed an L4 that way, 2026-11-25, so a
# model too big for that marks itself "offload": "sequential", one layer at a time)
if vram >= M.get('full_gpu_gb', M.get('min_vram_gb', 20) * 1.5): pipe.to('cuda')
elif M.get('offload') == 'sequential': pipe.enable_sequential_cpu_offload()
else: pipe.enable_model_cpu_offload()
print('pipeline', M.get('name'), type(pipe).__name__, 'on', torch.cuda.get_device_name(0), round(vram), 'GB', dt, flush=True)
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
    kw = dict(M['call'], **({'num_inference_steps': j['steps']} if 'steps' in j else {}))
    if M.get('negative'):
        kw['negative_prompt'] = M.get('negative_prompt', ' ')
    img = pipe(prompt=prompt, height=j.get('h', M['h']), width=j.get('w', M['w']),
               generator=torch.Generator('cuda').manual_seed(int(j.get('seed', 7))), **kw).images[0]
    lo, hi = img.convert('L').getextrema()
    if hi - lo < 8:
        bad += 1; print(f'FAILED {name}: blank image (levels {lo}-{hi})', flush=True); continue
    info = PngInfo(); info.add_text('motion-studio-model', str(M.get('name', '?')))
    if M.get('draft'):
        info.add_text('motion-studio-draft', '1')
    img.save('/content/tmp/' + name, pnginfo=info); publish('/content/tmp/' + name, name)
    print(f'OUTFILE {name}', flush=True); print(f'STILL {name} {time.time() - t0:.1f}s', flush=True)
open('/content/tmp/RENDER_DONE', 'w').write(f'done, {bad} failed'); os.replace('/content/tmp/RENDER_DONE', '/content/out/RENDER_DONE')
print('RENDER DONE', flush=True)
