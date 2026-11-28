"""LTX-2.5 image-to-video on Colab, from the same jobs.json as Wan (remote_render.py), so one batch runs on both
(run_ab.sh). Each Wan job becomes the same shot for LTX: same still, same prompt (style line + motion), same seed, the
same length in seconds (Wan renders at 16 fps, LTX at 24, and LTX wants 8k+1 frames), the size snapped to a multiple of
32. LTX's own audio is dropped, since the film's sound is built separately. Steps and guidance stay at the pipeline's
defaults unless a job sets "ltx_steps" or "ltx_guidance". "hold_end" is not passed: last-frame conditioning was not
checked for this pipeline. Outputs are <id>.mp4 in their own out_dir, published whole like every render script."""
import os

WAN_FPS, FPS = 16, 24
STYLE = '2D anime, cel-shaded, clean ink linework, consistent character, cinematic. '      # remote_render.py's, word for word
NEG = ('text, letters, watermark, logo, subtitles, static, frozen frame, blurry, low quality, deformed hands, extra fingers, extra limbs, '
       'morphing face, flicker, 3d render, photorealistic, duplicate of the main character')


def ltx_job(j):
    """The LTX call settings for one Wan job: {w, h, frames, seconds, seed, prompt, steps?, guidance?}."""
    fast = j.get('lightning', False)
    secs = j.get('frames', 49 if fast else 81) / WAN_FPS
    frames = max(9, round((secs * FPS - 1) / 8) * 8 + 1)
    snap = lambda v: max(32, round(v / 32) * 32)
    out = {'w': snap(j.get('w', 1280)), 'h': snap(j.get('h', 720)), 'frames': frames, 'seconds': round(frames / FPS, 2),
           'seed': j.get('seed', 1), 'prompt': STYLE + j['motion']}
    if 'ltx_steps' in j: out['steps'] = j['ltx_steps']
    if 'ltx_guidance' in j: out['guidance'] = j['ltx_guidance']
    return out


def main():
    os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
    import hashlib, inspect, json, time
    import torch
    import diffusers
    from diffusers.utils import export_to_video, load_image
    start = time.time()
    while not os.path.exists('/content/ltx_path.txt'):          # same wait as remote_render.py (#121)
        last = max(start, os.path.getmtime('/content/setup_alive') if os.path.exists('/content/setup_alive') else 0)
        if time.time() - last >= int(os.environ.get('SETUP_WAIT', 2700)):
            raise SystemExit('FAILED: setup did not finish (no sign of life for %d s)' % (time.time() - last))
        print('waiting for setup', flush=True); time.sleep(30)
    P = open('/content/ltx_path.txt').read().strip()
    Pipe = getattr(diffusers, os.environ.get('LTX_PIPELINE', 'LTX2ImageToVideoPipeline'))
    pipe = Pipe.from_pretrained(P, torch_dtype=torch.bfloat16)
    vram = torch.cuda.get_device_properties(0).total_memory / 1e9
    if vram < 70:
        # 22B in bf16 is 44 GB: store the transformer's weights in FP8 and compute in bf16 (half the memory, a small
        # quality cost), and keep each model on the GPU only while it runs
        pipe.transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
        pipe.enable_model_cpu_offload()
        print('transformer stored in FP8, models offloaded between steps', flush=True)
    else:
        pipe.to('cuda')
    print('pipeline on', torch.cuda.get_device_name(0), round(vram), 'GB', flush=True)
    takes = set(inspect.signature(pipe.__call__).parameters)
    jobs = json.load(open('/content/in/jobs.json'))
    os.makedirs('/content/out', exist_ok=True); os.makedirs('/content/tmp', exist_ok=True)

    def publish(tmp, name):         # as remote_render.py: whole files, listed with size and sha256 for pull_live.sh
        h = hashlib.sha256()
        with open(tmp, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
        os.replace(tmp, f'/content/out/{name}')
        m = '/content/out/MANIFEST.tsv'
        old = open(m).read() if os.path.exists(m) else ''
        open('/content/tmp/MANIFEST.tsv', 'w').write(old + f"{name}\t{os.path.getsize(f'/content/out/{name}')}\t{h.hexdigest()}\n")
        os.replace('/content/tmp/MANIFEST.tsv', m)

    log = open('/content/tmp/ltx-timings.tsv', 'a')
    for j in jobs:
        if os.path.exists(f"/content/out/{j['id']}.mp4"):
            print(f"OUTFILE {j['id']}.mp4", flush=True); continue
        s = ltx_job(j); t0 = time.time()
        img = load_image(f"/content/in/{j['image']}").resize((s['w'], s['h']))
        kw = {'image': img, 'prompt': s['prompt'], 'width': s['w'], 'height': s['h'], 'num_frames': s['frames'],
              'generator': torch.Generator('cpu').manual_seed(s['seed'])}
        opt = {'negative_prompt': NEG, 'frame_rate': FPS, 'num_inference_steps': s.get('steps'), 'guidance_scale': s.get('guidance')}
        kw.update({k: v for k, v in opt.items() if v is not None and k in takes})
        res = pipe(**kw)
        frames = res.frames[0] if hasattr(res, 'frames') else res[0][0]
        export_to_video(frames, f"/content/tmp/{j['id']}.mp4", fps=FPS); publish(f"/content/tmp/{j['id']}.mp4", f"{j['id']}.mp4")
        dt = int(time.time() - t0)
        log.write(f"{j['id']}\t{s['w']}x{s['h']}\t{s['frames']}\t{dt}\n"); log.flush()
        print(f"CLIP DONE {j['id']} {dt}s\nOUTFILE {j['id']}.mp4", flush=True)
    log.close(); publish('/content/tmp/ltx-timings.tsv', 'ltx-timings.tsv')     # seconds per clip, for the A/B
    open('/content/tmp/RENDER_DONE', 'w').write('done\n'); os.replace('/content/tmp/RENDER_DONE', '/content/out/RENDER_DONE')
    print('RENDER DONE', flush=True)


if __name__ == '__main__':
    main()
