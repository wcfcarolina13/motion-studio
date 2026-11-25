# Review and verification

## Order (cheap to expensive)

1. **Stills per scene** (`stills <film> --bars`): does each bar read on its own? Is the chain visible in order?
2. **Handoffs at 0.05 s** (`stills <film> 12.35 12.4 12.45 …`) around every transition, flood, iris, drag and caption change. Contact sheets every 0.5 s miss what happens between beats: chips crossing captions, rows merging into one bar, a flood cutting across text.
3. **Preview** at full speed with sound (`serve`). Nothing replaces watching it move. The user's notes land in `notes.json`.
4. **Draft** (`draft`) and its per-beat contact sheet: every tile must differ from the last.
5. **Final** (`final`): verify must pass.
6. **Blind read** (`blindpack`): the gate. See `panel.md`.

## Checklist, every round

- The chain reads in order; each scene is made from the previous one.
- Something moves on every beat; no hold over ~1 s except the final card.
- Text is never covered, crossed or clipped (descenders included); no caption lint on the render.
- Text reads against what is actually behind it: 3:1 per glyph at least (`check` text contrast), and no more than 3 flashes a second (`check` flash rate). Both are flags while they are tuned.
- **Shown and pull**: when a line says something happens or works, it is on screen, doing it, at that moment; each scene ends on something unresolved that the next one answers.
- No fades; no translucent layers that look like render glitches in a still.
- **No garbled or stray text in generated images** (owner's rule, 2026-11-22). Image and video models draw fake words, signs, labels and logos (a checklist read "OBDUE" in the Shoestring scene test). Zoom every generated still and a mid frame of every generated clip, and look at signs, papers, screens, packaging, clothing and devices. Fix by redrawing, editing the region out, or covering it with an engine card. Avoid it upfront: prompt for blank or wordless surfaces, and put every word the viewer must read in the engine, never in the model's picture.
- **Generated clips play forward once; never ping-pong or loop them in reverse** (owner's rule, 2026-11-22: "awkward, poor animation"). When a clip ends, cut away or hold its last frame while the camera keeps moving. A line that needs longer motion gets a longer clip, or a second clip chained from the first one's last frame.
- Pictures agree with captions (a forest of branched trees under "fewer branches" contradicts it).
- **Picture truth** (owner's rules, 2026-11-24; picture-truth.md): people and props at their real relative size, checked against the film's `"sizes"` sheet with `longform.py scale` (render stops until it passes); real objects drawn from sourced reference, undersides and parts included; light matches the time of day (a night scene has a dark sky); the work shown in every place it really happens; warm cream grounds, never paper white. Ask of every still: is anything too big, floating, lit from the wrong side, or built wrong?
- Screen/time budget matches the contract.
- Sound: cues land on their events; the drop is on the turn.
- **Motion from frame 0** (2026-11-17): a loop frame is still only AT the seam; motion ramps up from 0 there (a scene-clock strength that is 0 at the seam), never a hold of seconds. The picture changes every 2-3 s. Gate: `motioncheck` (no 0.5 s window in the first 3 s, and no 2 s stretch, under the floor).
- **The motion floor has a ceiling** (2026-11-25). The floor is a quarter of the film's median motion, kept between 0.02 and 0.2 (`motioncheck.floor_of`; `--max-floor` changes the cap). Without the cap, one film's floor went 0.197, 0.202, 0.425, 0.429 and 0.433 over five cuts: each round of stronger camera moves raised the median and the floor with it, so 38 panels that passed one cut were flagged the next, and adding motion could never settle the check. 0.2 is the level those cuts passed at, and a stretch moving that much visibly moves. Trade-off: in a busy film, a stretch far quieter than the rest but above 0.2 now passes; the dead-air and pacing checks still see it.
- **People and props read with no context**: a hand shows its owner (head, mask, suit) in the pack's own style; a vehicle has its defining parts. Blind-read questions: "whose hand is this?", "what is this object?".
- **No idle placeholders**: a prop appears only on the beat that uses it; a blank card, an empty calendar or a sign with no text fails. Blind-read question: "is anything on screen not doing anything?"
- **Nothing covers reading text**: check draw order, not just positions (`layoutcheck` OCCLUDED compares logged paints drawn after a text box).
- **Review link before publishing**: the studio preview runs with the cut loaded, and its URL goes in the review message.

## Timings the tools assume

- `stills --bars` and the blind packet take each bar's still at 0.72 of the bar: land captions and key poses before that.
- Captions rise in 0.26 s after `tIn` and leave in 0.22 s after `tOut`: end a caption ~0.26 s before the next one starts.
- `K.label(text, t0, t1, where)`: `where` is a short location ("title card"); the blind packet shows only "caption" or "text in the scene", never your description of what happens.

## verify (what it checks)

Even dimensions · yuv420p · fast start (moov before mdat) · clean decode · frame-0 background decodes to the film's ground colour (the limited-range grey lift shows up here: it moves every level by up to about 20). For a long-form film, the reference is the film's own frame 0 as the browser draws it, and the two are compared by the per-channel median of the central 60%, within 4 levels, so a textured paper ground is judged against itself rather than the palette's flat `BG` (toll-text's paper decodes about 9 levels under it, 2026-10-24). `studio.py` films keep the flat `K.C.BG` test · loudness −14 ±1 LUFS and true peak ≤ −1 dBTP · no single-frame pops (a frame whose difference from its neighbours spikes 3×). A Short cut to loop (its end jumps back to the opening) sets `"loop": true` in script.json: the jump in the last 0.5 s is then not a pop, as long as the last frame matches frame 0 within 3 levels on average; if it doesn't, the loop check fails and the pop stays. A slow push-in can trip the pop scan faintly (text antialiasing shifting): check the flagged frame before "fixing" it.

## Traps already paid for

| Trap | Fix |
|---|---|
| zsh doesn't word-split `$VAR`; `$N:layout` is a zsh modifier | use the scripts, or `${=VAR}` / `${N}` |
| zsh: a word starting with `=` (`echo ====`) is expanded; an unmatched glob aborts the command | quote it; use `find` or Python instead of globs that may not match |
| The Write tool stores typographic characters (an ellipsis, a curly apostrophe) literally, even when you typed a backslash-u escape | match the literal character when editing a file, not the escape sequence |
| `window.seek` calling itself (recursion, nothing renders) | the kit's `K.run` wires it; don't redefine `seek` |
| `const` used before its declaration in film.html | page error on render; declare shared constants first |
| Whisper word times run early through a take (0.74 s by the loop seam on golf-ball v8), so a scene cut opens mid-word | map each line's words onto the take's measured pauses (silencedetect) before cutting; assert every mid-take clip opens in silence |
| A loop bed jumps at the seam because the cut length lands on a downbeat | try whole-bar lengths either side (golf v6: 15 bars failed, 16 passed) before changing the bed |
| A mask clipping descenders ("g", "p") | mask rect extends ~0.25 em below the baseline |
| A strip turning into text reads as a strikethrough | the strip becomes the line the text rises from, then retracts below it |
| Panels or items summing wider than their container | compute widths from the space; check the last frame before the transition |
| Duplicate objects at a scene handoff | hide the source copy at the exact frame the next scene's copy appears |
| Captions wider than the frame | the kit prints `LINT:`; shorten the words |
| notes.json edits triggering a film reload | fixed in preview.py; keep notes out of the watched set |
| `preview_start` refuses to launch servers in a scheduled/unattended session | run `studio.py serve` from the shell, then `preview_start` with `url: http://localhost:4173` |
| Odd frame sizes break libx264 | the renderer rounds to even; keep formats even |
| A downbeat two beats off | `measure` corrects it from section changes; check by ear on loose grids |
| Session scratchpads are wiped between sessions | keep packets, prompts and reviews inside the film folder |
