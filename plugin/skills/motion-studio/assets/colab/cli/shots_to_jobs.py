"""shots.json -> an in_dir for the stills runner (remote_stills_*.py): one job per take, so retries resend only missing takes.
    python3 shots_to_jobs.py shots.json <in_dir> [--model NAME] [--seeds 7 19]
shots.json: {"style": "batch style", "model"?: NAME, "settings"?: {pipeline call overrides, e.g. "num_inference_steps": 50},
             "negative"?: "...", "shots": [{"id", "prompt", "style"?, "w"?, "h"?}]}.
Models are in stills_models.json; without --model or "model" the file's default is used. A draft model (Z-Image Turbo)
needs --draft, since its stills are for layout only and render refuses them (owner's rule, 2026-11-25).
Takes are named <id>_s<seed>.png, the two-take convention the stills route picks from (seeds 7 and 19). The in_dir gets
jobs.json, style.txt, model.json (the resolved model, read by the remote scripts) and min_vram_gb (read by run_colab.sh
and run_lanes.sh to pick a card that holds the model)."""
import argparse, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(); ap.add_argument('shots'); ap.add_argument('in_dir')
ap.add_argument('--model'); ap.add_argument('--draft', action='store_true', help='allow a draft (turbo) model')
ap.add_argument('--seeds', type=int, nargs='+', default=[7, 19])
a = ap.parse_args()
reg = json.load(open(os.path.join(HERE, 'stills_models.json')))
cfg = json.load(open(a.shots))
name = a.model or cfg.get('model') or reg['default']
if name not in reg['models']:
    raise SystemExit(f"unknown stills model {name}; known: {', '.join(reg['models'])}")
m = dict(reg['models'][name], name=name)
if m.get('route') == 'codex':
    raise SystemExit(f"{name} runs on the Mac, not Colab: python3 scripts/codex_stills.py {a.shots} <out_dir> --allow-codex "
                     "(spends the owner's Codex allowance; only with his yes)")
if m.get('draft') and not a.draft:
    raise SystemExit(f"{name} is a draft model (layout only; render refuses its stills): pass --draft to use it for drafts")
m['call'] = dict(m['call'], **cfg.get('settings', {}))
if 'negative' in cfg:
    m['negative_prompt'] = cfg['negative']
os.makedirs(a.in_dir, exist_ok=True)
jobs = [dict({k: v for k, v in s.items() if k != 'id'}, id=f"{s['id']}_s{seed}", seed=seed) for s in cfg['shots'] for seed in a.seeds]
json.dump(jobs, open(os.path.join(a.in_dir, 'jobs.json'), 'w'), indent=1)
open(os.path.join(a.in_dir, 'style.txt'), 'w').write(cfg.get('style', ''))
json.dump(m, open(os.path.join(a.in_dir, 'model.json'), 'w'), indent=1)
open(os.path.join(a.in_dir, 'min_vram_gb'), 'w').write(str(m['min_vram_gb']))
print(f"{len(cfg['shots'])} shots x {len(a.seeds)} takes = {len(jobs)} jobs, model {name}"
      f"{' (DRAFT: layout only)' if m.get('draft') else ''} -> {a.in_dir}")
