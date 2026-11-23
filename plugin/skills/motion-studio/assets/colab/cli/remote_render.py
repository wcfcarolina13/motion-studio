import os
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
import json, time, torch
from diffusers import WanImageToVideoPipeline, WanTransformer3DModel, GGUFQuantizationConfig
from diffusers.utils import export_to_video, load_image
# A dropped `colab exec` connection does not stop the remote setup: wait for its paths file instead of failing.
while not (os.path.exists('/content/paths.txt') and len(open('/content/paths.txt').read().split()) == 4):
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
# Background strangers are fine; a second copy of the hero is not.
NEG = ('text, letters, watermark, logo, subtitles, static, frozen frame, blurry, low quality, deformed hands, extra fingers, extra limbs, '
       'morphing face, flicker, 3d render, photorealistic, duplicate of the main character')
STYLE = '2D anime, cel-shaded, clean ink linework, consistent character, cinematic. '
os.makedirs('/content/out', exist_ok=True)
for j in jobs:
    out = f"/content/out/{j['id']}.mp4"
    if os.path.exists(out): print(f"OUTFILE {j['id']}.mp4", flush=True); continue
    fast = j.get('lightning', False); steps = j.get('steps', 4 if fast else 30); g = j.get('guidance', 1.0 if fast else 3.5)
    t0 = time.time()
    def cb(p, i, t, kw): print(f"{j['id']} step {i+1}/{steps} {int(time.time()-t0)}s", flush=True); return kw
    w, h = j.get('w', 1280), j.get('h', 720)
    img = load_image(f"/content/in/{j['image']}").resize((w, h))
    fr = pipe(image=img, prompt=STYLE + j['motion'], negative_prompt=NEG, height=h, width=w, num_frames=j.get('frames', 81),
              guidance_scale=g, guidance_scale_2=g, num_inference_steps=steps,
              generator=torch.Generator('cpu').manual_seed(j.get('seed', 1)), callback_on_step_end=cb).frames[0]
    export_to_video(fr, out, fps=16)
    print(f"CLIP DONE {j['id']} {int(time.time()-t0)}s\nOUTFILE {j['id']}.mp4", flush=True)
print('RENDER DONE', flush=True)
