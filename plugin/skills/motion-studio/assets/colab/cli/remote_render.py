import os
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
import json, time, torch
from diffusers import WanImageToVideoPipeline, WanTransformer3DModel, GGUFQuantizationConfig
from diffusers.utils import export_to_video, load_image
# A dropped `colab exec` connection does not stop the remote setup: wait for its paths file instead of failing,
# but not forever: a setup that crashed never writes it, and an endless wait burns units until someone notices.
# The limit counts from setup's last sign of life (it touches /content/setup_alive before each download and while
# waiting to retry), so a slow download or a run of retries doesn't trip it; one stuck download still can.
start = time.time()
while not (os.path.exists('/content/paths.txt') and len(open('/content/paths.txt').read().split()) == 4):
    last = max(start, os.path.getmtime('/content/setup_alive') if os.path.exists('/content/setup_alive') else 0)
    if time.time() - last >= int(os.environ.get('SETUP_WAIT', 2700)):
        raise SystemExit('FAILED: setup did not finish (no sign of life for %d s)' % (time.time() - last))
    print('waiting for setup', flush=True); time.sleep(30)
D14, HI, LO, LI = open('/content/paths.txt').read().split()
q = GGUFQuantizationConfig(compute_dtype=torch.bfloat16)
hi = WanTransformer3DModel.from_single_file(HI, quantization_config=q, config=D14, subfolder='transformer', torch_dtype=torch.bfloat16)
lo = WanTransformer3DModel.from_single_file(LO, quantization_config=q, config=D14, subfolder='transformer_2', torch_dtype=torch.bfloat16)
pipe = WanImageToVideoPipeline.from_pretrained(D14, transformer=hi, transformer_2=lo, torch_dtype=torch.bfloat16)
jobs = json.load(open('/content/in/jobs.json'))
if any(j.get('lightning') for j in jobs):  # 4 steps, guidance 1.0
    pipe.load_lora_weights(LI, weight_name='high_noise_model.safetensors', adapter_name='hi')
    pipe.load_lora_weights(LI, weight_name='low_noise_model.safetensors', adapter_name='lo', load_into_transformer_2=True)
    print('lightning LoRAs loaded', flush=True)
vram = torch.cuda.get_device_properties(0).total_memory / 1e9
pipe.to('cuda') if vram >= 70 else pipe.enable_model_cpu_offload()
print('pipeline on', torch.cuda.get_device_name(0), round(vram), 'GB', flush=True)
SCHED = dict(pipe.scheduler.config)   # ships flow_shift 3.0; official Wan uses 5.0, which invented more people in our test
# Background strangers are fine; a second copy of the hero is not.
NEG = ('text, letters, watermark, logo, subtitles, static, frozen frame, blurry, low quality, deformed hands, extra fingers, extra limbs, '
       'morphing face, flicker, 3d render, photorealistic, duplicate of the main character')
STYLE = '2D anime, cel-shaded, clean ink linework, consistent character, cinematic. '
os.makedirs('/content/out', exist_ok=True); os.makedirs('/content/tmp', exist_ok=True)
import hashlib
def publish(tmp, name):
    """Move a finished file into /content/out whole, then list it with its size and sha256 in MANIFEST.tsv, which
    pull_live.sh checks every download against. Only listed files are taken, so a file is never pulled half-written."""
    h = hashlib.sha256()
    with open(tmp, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    os.replace(tmp, f'/content/out/{name}')
    m = '/content/out/MANIFEST.tsv'
    old = open(m).read() if os.path.exists(m) else ''
    open('/content/tmp/MANIFEST.tsv', 'w').write(old + f"{name}\t{os.path.getsize(f'/content/out/{name}')}\t{h.hexdigest()}\n")
    os.replace('/content/tmp/MANIFEST.tsv', m)
for j in jobs:
    out = f"/content/out/{j['id']}.mp4"
    if os.path.exists(out): print(f"OUTFILE {j['id']}.mp4", flush=True); continue
    fast = j.get('lightning', False)
    if any(x.get('lightning') for x in jobs):     # the LoRAs stay loaded: switch them per job, or a 30-step job runs with them
        for m in (pipe.transformer, pipe.transformer_2):   # per model: the pipeline-level call may not reach transformer_2
            m.enable_lora() if fast else m.disable_lora()
    steps = j.get('steps', 4 if fast else 30); g = j.get('guidance', 1.0 if fast else 3.5)
    t0 = time.time()
    def cb(p, i, t, kw): print(f"{j['id']} step {i+1}/{steps} {int(time.time()-t0)}s", flush=True); return kw
    w, h = j.get('w', 1280), j.get('h', 720)
    img = load_image(f"/content/in/{j['image']}").resize((w, h))
    # flow_shift: per job, for testing; unset keeps the shipped scheduler. hold_end: the clip ends on its own still,
    # which keeps the end clean but freezes subtle motion (A/B 2026-11-23), so only for empty shots with strong
    # ambient motion such as rain.
    pipe.scheduler = pipe.scheduler.__class__.from_config(SCHED, **({'flow_shift': j['flow_shift']} if 'flow_shift' in j else {}))
    extra = {'last_image': img} if j.get('hold_end') else {}
    fr = pipe(image=img, prompt=STYLE + j['motion'], negative_prompt=NEG, height=h, width=w, num_frames=j.get('frames', 49 if fast else 81),
              guidance_scale=g, guidance_scale_2=g, num_inference_steps=steps,
              generator=torch.Generator('cpu').manual_seed(j.get('seed', 1)), callback_on_step_end=cb, **extra).frames[0]
    # whole files only in /content/out, listed in its manifest: pull_live.sh downloads them mid-render
    export_to_video(fr, f"/content/tmp/{j['id']}.mp4", fps=16); publish(f"/content/tmp/{j['id']}.mp4", f"{j['id']}.mp4")
    print(f"CLIP DONE {j['id']} {int(time.time()-t0)}s\nOUTFILE {j['id']}.mp4", flush=True)
open('/content/tmp/RENDER_DONE', 'w').write('done\n'); os.replace('/content/tmp/RENDER_DONE', '/content/out/RENDER_DONE')
print('RENDER DONE', flush=True)
