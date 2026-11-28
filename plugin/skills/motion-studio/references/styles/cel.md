# Cel style: an old hand-inked cartoon

The cel look is a vertical Short (or a long-form film) drawn like a 1940s–50s hand-inked cartoon cel and printed on film. It was set up in the cel style test (2026-11-27 to 2026-11-28) from frames of a reference video, described here in our own words. It is a style only: every film brings its own cast, palette and sets, and nothing is taken from the reference's characters.

## Turn it on

| Where | Setting | What it does |
|---|---|---|
| shots.json (codex_stills) | `"style"`: the prompt block below, with the film's palette | Every still is drawn in the look. |
| shots.json | `"aspect": "portrait"` | 1024x1536 from the model, cropped to 9:16 (references/colab.md). |
| shots.json | `"cast"` and `"props"` (file level) | The lead, the antagonist and the recurring things look the same in every still and every edit. |
| shots.json, per shot | `"write_on": ["the price tag"]` | The prompt asks for a clean, empty writing area on that prop (see Blank props). |
| script.json | `"finish": "cel"` | The print look on every composed frame: softened ink with a pale rim, chroma off register, a slow mottle, bloom, warm lifted blacks, a vignette, grain on twos (references/longform.md, "Finish passes"). |
| script.json | `"captions": true, "caption_style": "chunk"` | 1 to 3 words at a time in Nunito Black, yellow with a dark outline, above the YouTube overlay zone. They go on after the finish, so they stay crisp. |

## Prompt block

This goes in the job's `"style"`. Swap the palette for the film's own five colours, and keep the rest.

> Old hand-inked cartoon cel look, vertical 9:16. Thin dip-pen ink lines in warm sepia-black that swell and taper to points, with a slight hand wobble; never thick, even sticker outlines. Flat colour fills, with no gradients on people. Locked palette for this film: [shadow colour] for shadows and night, one skin tone for every person, [cream] for paper and pale surfaces, [accent] as the accent, one red used sparingly. Skin gets one darker shade where something throws a shadow and one lighter shade where bone catches light. Texture is short ticks and dots, never long hatching: tick-mark stubble and pores, rows of dashes on knit and fur, grain ticks on wood, broken course lines on brick. Light is two flat tones split by hard diagonal edges. By day: pale blue sky, warm sunlit surfaces, blue-violet shadows. Indoors: one warm shaft from a window across a cool room. At night: lit surfaces go pink and shadows go to the shadow colour. One small sparkle glint on any metal or glass. Backgrounds behind a close-up are softly out of focus. Caricature, not anatomy: big heads, small features set close together in the middle. Each person has two hands with padded fingers and short nails that stop at the fingertip.

The test Short's palette was cobalt #2F4DB0 (shadows, night, the units), salmon #EE8A72 (every person's skin), cream #F2E4C4 (paper), ochre #B07A1E (accent) and red #C8402F (sparingly).

codex_stills already adds its own line against writing, letters, numbers and logos, so the block doesn't repeat it.

## Cast

Two people carry most films in this look. They go in the job's `"cast"`, written as what each one has, so the lead and the antagonist are never mixed up.

- **The lead** (the viewer's stand-in, "you"): sympathetic. Slight caricature only: a rounder head and slightly small features. Give them one signature item that reads at thumbnail size (a coloured cap, a jacket). The camera is always on their side, and they are never mocked.
- **The antagonist** (a caricature): every grotesque feature goes to them and never to the lead. That means a huge lumpy head with tiny features crowded in the middle, jowls, a smug grin and a signature item of their own (a hard hat tipped back, a gold ring).

## Acting

- Faces change from shot to shot. An open mouth is fully drawn: lip rim, dark inside, teeth one by one.
- Shock is big pale discs with pinprick pupils and the head thrown back. Smug is chin up and head cocked. Worry is brows raised in the middle.
- Every noun in the line gets a drawn prop: the truck, the pump, the envelope, the pile of invoices, the calendar, the cash.
- Fill the 9:16 frame and make subjects big. Nothing essential goes in the bottom 20%, where the captions and YouTube's overlay sit.

## Blank props: the model draws the paper, the engine writes the number

Image models garble writing, and the engine never uses theirs (picture-truth.md rule 9; "Generated text" in longform.md). In this look, though, a number reads best written on a thing in the picture: a price on a hang tag, a total on a receipt, a date on a calendar page, an amount on an envelope. A card pasted over the picture looks out of place. So:

1. **The still asks for the writing area.** List the props in the shot's `"write_on"`. The prompt then says each one "has a clean, empty writing area in plain paper or screen colour, turned enough toward the camera to read". It names what the prop has, never what it lacks (rule 14: "blank doors" lost their handles). Don't write "blank tag" or "no text on the receipt" in the prompt yourself.
2. **The panel letters it.** Give the panel a `"screen"` for each prop: a list when there are several. Each has the prop's writing area as a `quad` (four corners, 0..1 of the still, clockwise from top left) and the words as `lines` cued to their word with `"at"`. For paper, set `"bg": false` so the prop's own paper shows, `"ink"` to the film's ink colour, and `"font": "marker"` for a hand-written price (or `"elite"`, `"caslon"`, `"anton"` or `"nunito"`, the engine's OFL faces). The words sit in the prop's perspective, ride the camera, and land on their word.

```json
"data": {"image": "panels/s12.png",
         "screen": [{"quad": [[0.58, 0.31], [0.74, 0.29], [0.75, 0.40], [0.59, 0.42]], "bg": false, "ink": "#2a1710",
                     "font": "marker", "align": "center", "lines": [{"text": "$1,200", "at": "twelve"}]},
                    {"quad": [[0.12, 0.55], [0.30, 0.55], [0.30, 0.70], [0.12, 0.70]], "bg": false, "ink": "#2a1710",
                     "font": "elite", "lines": ["RENT", {"text": "$950/mo", "at": "nine"}]}]}
```

3. **A blank prop only appears on the beat that writes on it.** A blank tag or empty calendar with nothing written on it fails review ("No idle placeholders", review.md). If the number isn't said in that beat, draw the prop without a writing area, or leave it out.

`pace` and `check` treat each screen line like any other reveal: a line with no `"at"` is reported, and `pace --apply` cues it to the word that says its number. `check` compares a number on a prop with the narration, the same as a callout.

## What's enforced, and what isn't

- Enforced: codex_stills refuses a `"write_on"` that isn't a list of names. The cue checks cover screen lines. The finish and the chunk captions are validated by `timeline`.
- Not enforced: nothing checks that a still made with `"write_on"` gets a `screen` on its panel. That is a review item for now. Nothing checks either that the quad matches the drawn writing area: look at the `finish` stills (`longform.py finish <film> --at <s>`) at full size.
