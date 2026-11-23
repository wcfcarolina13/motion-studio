# Picks the model size from the GPU: 40 GB+ (A100) gets 8-bit experts for full-quality 720p renders;
# a 24 GB L4 gets 4-bit (Q4_K_M) plus the Lightning 4-step LoRAs, since 8-bit at 720p ran out of memory there (2026-11-23).
import subprocess, os, sys
subprocess.run([sys.executable,'-m','pip','-q','install','-U','diffusers','transformers','accelerate','ftfy','sentencepiece','imageio','imageio-ffmpeg','gguf','peft'],check=True)
# The Xet CDN answered 429 (rate limit) on Colab and killed an A100 run (2026-11-23): use plain HTTP downloads,
# and retry with backoff. The flag must be set before huggingface_hub is imported. Finished files stay in the cache,
# so a retry fetches only what is missing.
os.environ['HF_HUB_DISABLE_XET'] = '1'
import time
import huggingface_hub as hub

def alive():                        # the render waits for setup while this file keeps getting touched
    open('/content/setup_alive', 'w').write(str(time.time()))

def fetch(fn, *a, tries=6, **kw):
    for n in range(tries):
        alive()
        try:
            return fn(*a, **kw)
        except Exception as e:      # 429s and dropped connections surface as several exception types
            if n == tries - 1: raise
            wait = min(300, 30 * 2 ** n)
            print(f'download failed ({type(e).__name__}: {str(e)[:120]}); retry {n + 1}/{tries - 1} in {wait} s', flush=True)
            for _ in range(wait // 30): time.sleep(30); alive()
snapshot_download = lambda *a, **kw: fetch(hub.snapshot_download, *a, **kw)
hf_hub_download = lambda *a, **kw: fetch(hub.hf_hub_download, *a, **kw)
import torch
vram = torch.cuda.get_device_properties(0).total_memory / 1e9
Q = 'Q8_0' if vram >= 40 else 'Q4_K_M'
D14=snapshot_download('Wan-AI/Wan2.2-I2V-A14B-Diffusers', allow_patterns=['model_index.json','scheduler/*','tokenizer/*','text_encoder/*','transformer/config.json','transformer_2/config.json','vae/*'])
HI=hf_hub_download('QuantStack/Wan2.2-I2V-A14B-GGUF',f'HighNoise/Wan2.2-I2V-A14B-HighNoise-{Q}.gguf')
LO=hf_hub_download('QuantStack/Wan2.2-I2V-A14B-GGUF',f'LowNoise/Wan2.2-I2V-A14B-LowNoise-{Q}.gguf')
LI=snapshot_download('lightx2v/Wan2.2-Lightning', allow_patterns=['Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/*'])+'/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1'
open('/content/paths.txt','w').write(f"{D14}\n{HI}\n{LO}\n{LI}\n")
print('SETUP DONE', Q, round(vram), 'GB', flush=True)
