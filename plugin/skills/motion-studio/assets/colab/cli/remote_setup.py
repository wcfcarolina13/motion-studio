# Picks the model size from the GPU: 40 GB+ (A100) gets 8-bit experts for full-quality 720p renders;
# a 24 GB L4 gets 4-bit (Q4_K_M) plus the Lightning 4-step LoRAs, since 8-bit at 720p ran out of memory there (2026-11-23).
import subprocess, os, sys
subprocess.run([sys.executable,'-m','pip','-q','install','-U','diffusers','transformers','accelerate','ftfy','sentencepiece','imageio','imageio-ffmpeg','gguf','peft'],check=True)
from huggingface_hub import snapshot_download, hf_hub_download
import torch
vram = torch.cuda.get_device_properties(0).total_memory / 1e9
Q = 'Q8_0' if vram >= 40 else 'Q4_K_M'
D14=snapshot_download('Wan-AI/Wan2.2-I2V-A14B-Diffusers', allow_patterns=['model_index.json','scheduler/*','tokenizer/*','text_encoder/*','transformer/config.json','transformer_2/config.json','vae/*'])
HI=hf_hub_download('QuantStack/Wan2.2-I2V-A14B-GGUF',f'HighNoise/Wan2.2-I2V-A14B-HighNoise-{Q}.gguf')
LO=hf_hub_download('QuantStack/Wan2.2-I2V-A14B-GGUF',f'LowNoise/Wan2.2-I2V-A14B-LowNoise-{Q}.gguf')
LI=snapshot_download('lightx2v/Wan2.2-Lightning', allow_patterns=['Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/*'])+'/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1'
open('/content/paths.txt','w').write(f"{D14}\n{HI}\n{LO}\n{LI}\n")
print('SETUP DONE', Q, round(vram), 'GB', flush=True)
