import subprocess, os, sys
subprocess.run([sys.executable,'-m','pip','-q','install','-U','diffusers','transformers','accelerate','ftfy','sentencepiece','imageio','imageio-ffmpeg','gguf','peft','huggingface_hub[hf_transfer]'],check=True)
os.environ['HF_HUB_ENABLE_HF_TRANSFER']='1'
from huggingface_hub import snapshot_download, hf_hub_download
D14=snapshot_download('Wan-AI/Wan2.2-I2V-A14B-Diffusers', allow_patterns=['model_index.json','scheduler/*','tokenizer/*','text_encoder/*','transformer/config.json','transformer_2/config.json','vae/*'])
HI=hf_hub_download('QuantStack/Wan2.2-I2V-A14B-GGUF','HighNoise/Wan2.2-I2V-A14B-HighNoise-Q8_0.gguf')
LO=hf_hub_download('QuantStack/Wan2.2-I2V-A14B-GGUF','LowNoise/Wan2.2-I2V-A14B-LowNoise-Q8_0.gguf')
open('/content/paths.txt','w').write(f"{D14}\n{HI}\n{LO}\n")
print(subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv'],capture_output=True,text=True).stdout)
print('SETUP DONE', flush=True)
