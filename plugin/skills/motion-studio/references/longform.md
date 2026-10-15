# Long-form narrated films

For 5–20 minute 16:9 explainers (YouTube). The narration is the clock: each segment of the script gets one voice clip, the clip's length sets the segment's length, and anything in a scene can be timed to a spoken word. Run everything with `L=${CLAUDE_SKILL_DIR}/scripts/longform.py`.

## Pipeline

| Step | Command | Produces |
|---|---|---|
| scaffold | `python3 $L init <film>` | film.html (engine), kit.js, figure.js, folders |
| voice | `python3 $L lines <film>` → one mp3 per segment id in `narration/` (any TTS or a human read) | `narration/s01.mp3` … |
| word times | `python3 $L align <film>` | `narration/words.json` (offline forced alignment) |
| timeline | `python3 $L timeline <film>` | `timeline.js`, `out/narration.wav`, `out/chapters.txt`, `out/captions.vtt` |
| look | `python3 $L stills <film> --every 2.5` | stills + contact sheet in `review/` |
| music | `python3 $L music <film>` then `python3 $L mix <film> --plan [--whoosh w.wav]` | `out/music_plan.json`, `out/mix.wav` |
| render | `python3 $L render <film> --workers 4` | `out/silent.mp4` |
| ship | `python3 $L final <film>` | `out/<film>.mp4`, verify report |
| critic | `python3 $L check <film>` | `review/check.txt` (see `critic.md`) |
| upload | `python3 ${CLAUDE_SKILL_DIR}/scripts/youtube.py upload … --privacy private` | private video; `captions` adds the VTT |

`align` needs `pip install pocketsphinx` (its English model ships in the wheel, no download). Without it, words are spread across each clip by spoken weight between measured pauses, good to about 0.15 s. Numbers, money and years are expanded the way a narrator says them ("$4.12" → four dollars twelve cents) before aligning; words the dictionary doesn't know take the gap between their neighbours.

## script.json

```json
{"title": "…", "moods": {"Intro": "mystery", "The trick": "tension"}, "captions": false,
 "segments": [
  {"id": "s01", "chapter": "Intro", "type": "stage", "source": "…", "narration": "You've probably had this text…",
   "data": {"set": "room", "cast": [{"id": "you", "x": 0.36, "skin": 2, "hair": "short", "shirt": "#e8772e"}],
            "beats": [{"who": "you", "at": "text", "pose": "phone", "glow": true, "notify": "TOLL NOTICE: …"},
                      {"who": "you", "at": "late fee", "pose": "read", "emote": "!"}]}}]}
```

**Word cues.** Any object inside `data` with `"at": "<word or phrase>"` gets an absolute time `t` when `timeline` runs (`"nth": 2` for a repeat, `"offset": -0.1` to lead). Things that must be read land 0.05 s before their word. `timeline` prints a PROBLEM line for any cue word that isn't in the narration. List items, flow nodes, hub spokes, compare items and statement lines accept `{"text": …, "at": …}` in place of a plain string; `stat` and `sms` take `"at"` on their data to start the count or the message on a word, and `stat` takes `"notes": [{"text", "at"}]`, follow-up lines under the label, so a long stat scene doesn't sit still after the count.

## Scene types

Text-led (use sparingly; `check` flags a film that is more than 40% these): `title`, `chapter`, `statement`, `quote`, `list`.
Diagrams: `compare`, `stat`, `bars`, `flow`, `hub`, `sms` (a phone with a message).
People and evidence (their own set; they wipe on and off left to right instead of cutting):

- **`stage`**: rigged figures on a set (`room`, `street`, `desk`, `blank`). Cast entries take figure options (below) plus `x` (0–1), `h` (px, default 540), `pose`, `enter: "left"|"right"` (walks in). Beats: `pose`, `face`, `emote` (`? ! $ … x`), `say` / `think` (bubble), `notify` (a message card beside the phone), `glow` (the phone lights and buzzes), `move` (walk to a new x). The camera leans toward whoever acted last. `kicker` is a fixed label top left.
- **`board`**: an evidence board. `cards` (`id, x, y, title, text, icon: phone|bank|card|person|money|web|doc, kind: "note"`) pin up on their words; `strings` (`from, to`) draw red string; `stamp` (`on, text`) slams onto a card. The camera drifts toward the newest card.
- **`crowd`**: rows of seated people on phones; the camera starts on one person and pulls back to the room (`from`/`to` are 0–1 of the segment), then `label` and an optional `count` (`value, prefix, suffix, label, at`).

Write `"source"` on every segment that states a fact; it shows bottom left.

## Captions

16:9 long-form ships captions as a track: `timeline` writes `out/captions.vtt` from the word times, and `youtube.py captions <id> out/captions.vtt` attaches it. Burned-in, word-lit captions (`"captions": true`) are for vertical cut-downs: pages of up to 7 words, spoken words turn from grey to ink, the current word is accent.

## Music by mood

`music` measures every library track once (loudness, onset density, pulse strength, brightness, major/minor lean; stored in `catalog.json` as `mood`) and ranks tracks against each chapter's mood: `tension`, `mystery`, `serious`, `calm`, `warm`, `drive`, `uplift` (targets in `audio.MOODS`). Consecutive chapters with one mood share a track; a mood change brings a different track on a 3 s crossfade at the chapter line; the last track is back-timed so its own ending lands on the film's end. A run whose best fit is under 0.6 prints NO GOOD FIT with search words for the library source: fetch better tracks rather than shipping a club track under a tense chapter. Edit `track` in `out/music_plan.json` to override. Music sits at -24 dB under the voice and ducks further while it speaks. Never put music with vocals under narration.

## Traps hit building this

- Two camera keys at the same time divide by zero and the camera jumps a whole frame. Build keys with `camKeys()`, which keeps them 0.8 s apart.
- An opaque scene that doesn't paint every pixel shows the previous scene through its gaps until that one leaves, which reads as a pop. Opaque scenes get a full-frame backdrop automatically; keep it if you write a new one.
- Fixed labels on a scene with a moving camera drift off frame: put them in `s.ov`, the per-segment overlay that the camera doesn't move.
- `verify` treats a wipe crossing a bright figure, or a stamp, as a pop. `final` passes those windows as allowed; anything outside them is a real glitch.
