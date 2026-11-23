import json, os, time, torch
from diffusers import WanImageToVideoPipeline, WanTransformer3DModel, GGUFQuantizationConfig
from diffusers.utils import export_to_video, load_image
D14, HI, LO = open('/content/paths.txt').read().split()
q = GGUFQuantizationConfig(compute_dtype=torch.bfloat16)
hi = WanTransformer3DModel.from_single_file(HI, quantization_config=q, config=D14, subfolder='transformer', torch_dtype=torch.bfloat16)
lo = WanTransformer3DModel.from_single_file(LO, quantization_config=q, config=D14, subfolder='transformer_2', torch_dtype=torch.bfloat16)
pipe = WanImageToVideoPipeline.from_pretrained(D14, transformer=hi, transformer_2=lo, torch_dtype=torch.bfloat16)
vram = torch.cuda.get_device_properties(0).total_memory / 1e9
pipe.to('cuda') if vram >= 70 else pipe.enable_model_cpu_offload()
print('pipeline on', torch.cuda.get_device_name(0), round(vram), 'GB', flush=True)
NEG = ('text, letters, watermark, logo, subtitles, static, frozen frame, blurry, low quality, deformed hands, extra fingers, extra limbs, '
       'morphing face, flicker, 3d render, photorealistic, new person entering the frame, extra people, pedestrian, new character appearing, crowd')
STYLE = '2D anime, cel-shaded, clean ink linework, consistent character, cinematic. '
os.makedirs('/content/out', exist_ok=True)
for j in json.load(open('/content/in/jobs.json')):
    out = f"/content/out/{j['id']}.mp4"
    if os.path.exists(out): continue
    t0 = time.time()
    def cb(p, i, t, kw): print(f"{j['id']} step {i+1}/{j.get('steps',30)} {int(time.time()-t0)}s", flush=True); return kw
    img = load_image(f"/content/in/{j['image']}").resize((j.get('w',1280), j.get('h',720)))
    fr = pipe(image=img, prompt=STYLE + j['motion'], negative_prompt=NEG, height=j.get('h',720), width=j.get('w',1280),
              num_frames=j.get('frames',81), guidance_scale=3.5, guidance_scale_2=3.5, num_inference_steps=j.get('steps',30),
              generator=torch.Generator('cpu').manual_seed(j.get('seed',1)), callback_on_step_end=cb).frames[0]
    export_to_video(fr, out, fps=16)
    print(f"CLIP DONE {j['id']} {int(time.time()-t0)}s", flush=True)
print('RENDER DONE', flush=True)
