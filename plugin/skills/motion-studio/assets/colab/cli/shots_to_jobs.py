"""shots.json -> an in_dir for the Z-Image stills runner: one job per take, so retries resend only missing takes.
    python3 shots_to_jobs.py shots.json <in_dir> [--seeds 7 19]
shots.json: {"style": "batch style", "shots": [{"id", "prompt", "style"?, "w"?, "h"?}]}. Takes are named
<id>_s<seed>.png, the two-take convention the stills route picks from (seeds 7 and 19)."""
import argparse, json, os

ap = argparse.ArgumentParser(); ap.add_argument('shots'); ap.add_argument('in_dir')
ap.add_argument('--seeds', type=int, nargs='+', default=[7, 19])
a = ap.parse_args()
cfg = json.load(open(a.shots)); os.makedirs(a.in_dir, exist_ok=True)
jobs = [dict({k: v for k, v in s.items() if k != 'id'}, id=f"{s['id']}_s{seed}", seed=seed) for s in cfg['shots'] for seed in a.seeds]
json.dump(jobs, open(os.path.join(a.in_dir, 'jobs.json'), 'w'), indent=1)
open(os.path.join(a.in_dir, 'style.txt'), 'w').write(cfg.get('style', ''))
print(f"{len(cfg['shots'])} shots x {len(a.seeds)} takes = {len(jobs)} jobs -> {a.in_dir}")
