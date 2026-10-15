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
| music | `python3 $L music <film>` then `python3 $L mix <film> --plan [--whoosh w.wav]` | `out/music_plan.json`, `out/mix.wav`, with UI sounds from the timeline (below; `--no-sfx` leaves them out) |
| render | `python3 $L render <film> --workers 4` | `out/silent.mp4` |
| ship | `python3 $L final <film>` | `out/<film>.mp4`, verify report |
| critic | `python3 $L check <film>` | `review/check.txt` (see `critic.md`) |
| upload | `python3 ${CLAUDE_SKILL_DIR}/scripts/youtube.py upload … --privacy private` | private video; `captions` adds the VTT |
| share a draft | `python3 $L share <film>` | `out/share/`: a page that streams the cut, to publish as a private artifact (below) |

`align` needs `pip install pocketsphinx` (its English model ships in the wheel, no download). Without it, words are spread across each clip by spoken weight between measured pauses, good to about 0.15 s. Numbers, money and years are expanded the way a narrator says them ("$4.12" → four dollars twelve cents) before aligning; words the dictionary doesn't know take the gap between their neighbours. A year is tried both as "twenty twenty-five" and "two thousand twenty-five" (voices differ), keeping the reading whose word starts land on the measured pauses, and word edges are then snapped onto measured silence (the aligner otherwise hands leading silence to the first word).

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

- **`stage`**: rigged figures on a set (`room`, `street`, `desk`, `blank`). Cast entries take figure options (below) plus `x` (0–1), `h` (px, default 540), `pose`, `enter: "left"|"right"` (walks in). Beats: `pose`, `face`, `emote` (`? ! $ … x`), `say` / `think` (bubble), `notify` (a message card beside the phone; `from` changes its "MESSAGES · now" header, e.g. for a browser or an app), `glow` (the phone lights and buzzes), `move` (walk to a new x). The camera leans toward whoever acted last. `kicker` is a fixed label top left.
- **`board`**: an evidence board. `cards` (`id, x, y, title, text, icon: phone|bank|card|person|money|web|doc, kind: "note"`) pin up on their words; `strings` (`from, to`) draw red string; `stamp` (`on, text`) slams onto a card. The camera drifts toward the newest card.
- **`crowd`**: rows of seated people on phones; the camera starts on one person and pulls back to the room (`from`/`to` are 0–1 of the segment), then `label` and an optional `count` (`value, prefix, suffix, label, at`).

Write `"source"` on every segment that states a fact; it shows bottom left.

## Captions

Burned-in, word-lit captions are on by default (the channel owner wants them; `"captions": false` turns them off). `timeline` also writes `out/captions.vtt` from the word times, and `youtube.py captions <id> out/captions.vtt` attaches it as a track. The burned-in captions use pages of about four words (short pages keep the eye from running ahead of the voice), spoken words turn from grey to ink, the current word is accent.

## Music by mood

`music` measures every library track once (loudness, onset density, pulse strength, brightness, major/minor lean; stored in `catalog.json` as `mood`) and ranks tracks against each chapter's mood: `tension`, `mystery`, `serious`, `calm`, `warm`, `drive`, `uplift` (targets in `audio.MOODS`). Consecutive chapters with one mood share a track; a mood change brings a different track on a 3 s crossfade at the chapter line; the last track is back-timed so its own ending lands on the film's end. A run whose best fit is under 0.6 prints NO GOOD FIT with search words for the library source: fetch better tracks rather than shipping a club track under a tense chapter. Edit `track` in `out/music_plan.json` to override. Music sits at -25.5 dB under the voice (the channel owner found -24 a little loud) and ducks further while it speaks. Never put music with vocals under narration.

## UI sounds

`mix` adds one sound per on-screen event, read from the timeline: a buzz when a phone lights (`glow`), a ping when a message lands (`notify`, `sms`; 0.12 s after the buzz when both happen), a paper tick when a board card pins, a thud when a stamp hits. They sit at -12 dB (`--sfx-db`) under the voice. Same-kind sounds closer than the gaps in `SFX_GAP` are dropped so a burst of cards doesn't rattle. Each kind uses recorded library clips that carry `"role": "buzz" | "ping" | "pin" | "thud"` in `catalog.json` (cycled, level varied ±15%, transient on the cue); a kind with none falls back to a sound drawn in code, and `mix` prints which kinds did. The channel owner approved the drawn set on the toll-text film; sound.md's rule still prefers recordings, so give each kind recorded clips when the library can be reached (see sound.md).

## Sharing a draft

`share` turns `out/<film>.mp4` into `out/share/index.html` (poster, chapter buttons, the script's palette accent) plus `out/share/v/`. Publish the page with the Artifact tool and every file in `v/` under its own path; the result is private until the owner shares it from the page's Share menu, and a viewer just presses play. Why it is built this way: the artifact host serves neither `.m3u8` nor `.ts`, caps each file at 15 MB and each publish at 64 MB, and loads scripts only from approved CDNs, so the cut goes out as fMP4 HLS segments named `.mp4` with the playlist renamed `index.txt`, played through hls.js from cdnjs. A 10-minute 1080p cut is about 51 MB in 11 one-minute files. It needs Media Source Extensions: desktop browsers and iOS 17.1 or later; older iPhones get an error line. Optional `"share": {"headline", "lede", "eyebrow", "foot", "poster_at"}` in script.json sets the page text. The container's Playwright Chromium has no H.264 decoder, so a local test can only confirm that the page loads the playlist and segments, not that it plays; have the owner open it once before it goes to anyone.

## Lessons from the toll-text build

Each of these came from the channel owner watching a cut; the fix lives in the engine, not in one film.

- **Captions must follow the voice, not the page.** Long caption pages let the eye run ahead and word timing drifted up to 0.9 s after pauses. Fixed by short pages (about 4 words), word edges snapped to measured silence, and trying both readings of a year. Check any new voice on its first film: median word-start error should stay near 0.
- **Music under narration is quieter than it sounds while building.** -24 dB was too loud on a phone speaker; -25.5 dB is the default, with every track level-matched first so a quiet track doesn't jump at a chapter change.
- **Small sounds make the scenes feel real.** One sound per visible event, never per animation.
- **A mood measure can't tell relative keys apart.** Composed minor beds read as major; trust chapter intent and override `out/music_plan.json` by hand.
- **Music sources may be blocked from the build machine.** Check the library can be reached before planning a film around new tracks (sound.md lists the routes).
- **The owner reviews on a link, not a file.** Run `share` with every draft so review doesn't wait on a download or a YouTube upload.

## Traps hit building this

- Two camera keys at the same time divide by zero and the camera jumps a whole frame. Build keys with `camKeys()`, which keeps them 0.8 s apart.
- An opaque scene that doesn't paint every pixel shows the previous scene through its gaps until that one leaves, which reads as a pop. Opaque scenes get a full-frame backdrop automatically; keep it if you write a new one.
- Fixed labels on a scene with a moving camera drift off frame: put them in `s.ov`, the per-segment overlay that the camera doesn't move.
- `verify` treats a wipe crossing a bright figure, or a stamp, as a pop. `final` passes those windows as allowed; anything outside them is a real glitch.
