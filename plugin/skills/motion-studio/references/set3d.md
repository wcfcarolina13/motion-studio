# set3d: paper cutouts in toon-lit 3D sets (2.5D)

A `set3d` segment stands flat, code-drawn paper characters inside a small 3D set built from boxes, cylinders, cones and spheres, lit with a toon ramp and outlined in ink, and shoots it with a camera that cuts and moves on the narrator's words. It is the house version of the "2.5D stickman" genre: fast explainers where cheap characters act inside a model room. The method was reverse-engineered from a public description of how such videos are made (an X article on 2.5D stickman channels, caesarscales, 2026-11-24). No code, character or look was taken from it, and its kit shipped with no licence, so none of it is used. The cast, palette and sets here are motion-studio's own.

Files: `scripts/set3d.js` (the scene), `scripts/three.min.js` (three.js r159, MIT, licence in `scripts/three.LICENSE`; its one deprecation `console.warn` is removed and nothing else changed). `longform.py init` copies both into the film.

## The method, step by step

1. **Design sheet first.** Write the cast into the segments, then `longform.py cast-sheet <film>`. It draws `review/cast-sheet.png`: every cast member facing left, from the back, and in eight stock poses. Agree the designs on this sheet before anything is animated. `render` refuses a film whose cast changed since its sheet (the sheet's stamp is in `review/cast-sheet.json`).
2. **Build the set from primitives.** Start from a preset (`room`, `street`, `stage`, or `none`) and add `pieces`. Everything is in metres with the floor at y = 0, so sizes come straight from real objects (a desk is 0.72 m high).
3. **Block the action on the words.** Cast keys and camera keys take `"at": "<phrase>"` like every other cue, so a pose, a walk or a cut lands on the word that names it.
4. **Plan the camera path.** A camera key is a named shot on a cast member (`wide`, `med`, `close`, `low`, `high`, `over`) or an explicit `pos`/`look`. Keys ease over 0.8 s, or cut with `"cut": true`. On every hold the camera drifts (a slow dolly in and a sideways truck), so the picture never freezes.
5. **Sound on the frame.** Foley goes on the segment's `sfx` with an `at` cue (longform.md, the `panel` scene's `sfx`), so a hit lands on the same word as the action it marks. `mix` writes `out/sfx_events.json` and `check`'s pace profile counts them.
6. **Contact sheet before render.** `longform.py stills <film> --every 1` and look at `review/stills-every.png`. `render` refuses a film with set3d scenes and no contact sheet newer than `timeline.js`, because a 3D camera can frame anything, including a wall.

## Scene data

```json
{"id": "s4", "type": "set3d", "text": "Ada opens the shop at dawn, and nobody comes in.",
 "data": {"set": "room",
          "pieces": [{"shape": "box", "at": [2.2, 0, -1.5], "size": [1.4, 0.9, 0.6], "color": "wood"}],
          "cast": [{"id": "ada", "look": "ada", "at": [-1.0, 0], "face": "right", "talks": true,
                    "keys": [{"at": "opens", "pose": "present"}, {"at": "dawn", "to": [0.6, 0.2]}, {"at": "nobody", "pose": "shrug"}]},
                   {"id": "eli", "look": "eli", "back": true, "at": [1.8, 1.2], "face": "left"}],
          "camera": [{"shot": "wide"}, {"at": "dawn", "shot": "med", "on": "ada"}, {"at": "nobody", "shot": "close", "on": "ada", "cut": true}]}}
```

- **cast**: `id`; `look` is a pack character (`ada`, `bo`, `cy`, `dee`, `eli`) or an object of colours (`skin`, `hair`, `hairStyle`: bun, cap, curls, bob or crop, `cap`, `shirt`, `pants`, `shoes`, `tie`); `at` is `[x, z]` on the floor; `face` is `right` or `left`; `back: true` turns the figure away (a faceless hero); `talks: true` flaps the mouth on the segment's words; `pose` is the starting pose. A key takes `pose` (any figure.js pose: stand, relaxed, phone, read, worried, shocked, shrug, point, present, think, angry, happy, cheer, type, sit, sitphone, slump, realise, or a literal object of joint angles), `to: [x, z]` (walk there, at 1.4 m/s unless `walk` gives the seconds) and `face`. Poses blend with figure.js's anticipation and overshoot, an idle sway runs under every hold, and legs stride while walking. A pose name figure.js lacks is a LINT line on `stills`, `check` and `render`.
- **pieces**: `{shape: box|cyl|cone|sphere|plane|disc, at: [x, y, z], size, color, rot, outline, glow, shadow, sway, spin, drift, wrap}`. `at` is the base centre; `size` is `[w, h, d]` (cyl and cone `[diameter, h]`, sphere `[diameter]`, plane `[w, h]`, disc `[diameter]`). `color` is a palette key or a hex. `glow: true` is unlit (lamps, screens, windows with light behind them). `sway` rocks a piece about its base in degrees, `spin` turns it in degrees a second, and `drift: [vx, vz]` with `wrap: [min, max]` moves it along x on a loop (clouds, cars).
- **camera**: shots frame the centre of `on` (one id, a list, or the whole cast when left out); `side: -1` mirrors the offset; `fov` overrides the angle (35° in 16:9, 58° in 9:16). `drift` on the data sets the hold drift in m/s (0.06; 0 holds still).
- **light**: `{"az": 35, "el": 50, "warm": "#fff1dc"}`, the sun's direction in degrees and its colour.
- **palette**: script.json `"set3d": {"palette": {...}}` overrides the pack's colours for the whole film; a segment's `data.palette` overrides it again.

set3d is an opaque scene: it wipes on and off like `stage`. The cast's screen boxes and the set's anchor are mirrored as invisible rects (class = pose and facing), so `check`'s beat, hold and pace measures see the 3D picture move. Each camera key counts as a new shot in the pace profile.

## The pack's look

- **Cast**: chunky big-headed paper dolls with a cream paper border round the whole figure and an ink line round every part, side-on or three-quarter, the far limbs a shade darker. Five characters: ada (bun, teal shirt), bo (yellow cap), cy (curls, red shirt), dee (bob, lilac shirt), eli (crop, white shirt and red tie).
- **Palette**: ink `#232733`, paper `#f6eedd`, floor `#d8c4a0`, wall `#8fb5ad`, warm wall `#e7b54f`, wood `#a2683f`, leaf `#4f8a5b`, accent red `#d9573b`, accent blue `#3f7f9a`, lilac `#9c8cc7`. The full list is `S3.PALETTE` in set3d.js.
- **Light**: a hemisphere fill and one warm sun with soft shadow maps, through a three-step toon ramp, so every face of a box reads as one flat colour; ink outlines are inverted hulls 2.2 cm thick. The cutouts cast shadows that match their silhouette, plus a soft contact shadow under each.

## Pace for this style

The pace profile in `check` (critic.md) reports these numbers against the fast-cut benchmark. The benchmark is one author's count from one channel and is unverified, so these targets are a starting point to adjust from real retention, not rules.

| | benchmark (fast 2.5D Shorts) | set3d in a Short | set3d in long-form | our illustrated long-form |
|---|---|---|---|---|
| something moving | 96.6% | 95%+ | 90%+ | motion floor only |
| a new shot every | 2.1 s | 2 to 3 s | 3 to 5 s | 5 to 8 s (picture-truth.md) |
| something new every | 1.0 s | 1 to 1.5 s | 2 to 3 s | one beat per thought |
| words a second | 3.67 | 3.3 to 3.7 | 2.6 to 3.0 | the voice's natural pace |
| sound effects per 10 s | 8 | 5 to 8 | 2 to 4 | UI sounds only |

A new shot is cheap here (one camera key), which is why set3d can run near the benchmark when a still-based film cannot. The probe counts screen motion over 20 px/s, so the default hold drift (0.06 m/s) and the idle sway are too slow to count as moving there, though `final`'s pixel motion check sees them; walks, pose changes and camera moves are what raise the share.

## What it needs to run

- Nothing paid and no model: three.js runs in the same Chromium page as every other scene. $0.
- **Speed**: about 0.7 s a 1080p frame in the cloud container, where headless Chromium has no GPU and draws WebGL in software (measured 2026-11-25), so a 1-minute Short is about 20 minutes on one worker and a 10-minute film about an hour on four. On the Mac, Chrome uses the GPU for WebGL, which should be much faster; this is not yet measured.
- **Same machine for a whole render.** Software and GPU WebGL can differ by a few pixel values, so don't splice `render --only` parts made on one machine into a render made on another.

## Trade-offs

- three.js toon lighting is simpler than Blender's EEVEE: no ambient occlusion and harder shadow edges. In return every engine check (pace, cues, motion, page errors, contact sheet) works on set3d exactly as on other scenes, with no second renderer, no GPU and no install.
- Cutouts are flat cards that turn to face the camera. A camera that circles a character shows the same side view, as in the genre; there is no true front view.
- Sets are primitives only, with no imported models, so objects stay simple and every size is explicit.
- Hands hold nothing yet; a prop is a set piece placed by the hand.
