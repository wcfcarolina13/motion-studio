# Map scenes: an engraved paper map with collage pieces

A `map` segment puts the story on one engraved paper map. A camera flies across it as the story moves, and collage pieces are pinned to real places: halftone cut-outs, yellow paint, felt-marker ink, paper tags, cut-paper (ransom) letters. Consecutive map segments share one map, one camera and any pieces marked to last, so a run of them reads as one continuous shot. It suits stories that happen in places: a war, a voyage, a trade route, how a border was drawn.

The look and its numbers (line spacings, the 16-frame paint sweep, the 1.1 → 0.98 → 1.0 slap, the boil every 8 frames, the 5 px dot screen) follow a public build prompt for Vox-style map explainers (posted on X by @treyscalez, 2026-11-23). No code came with it and none was copied. This is the engine's own implementation of the idea, in its long-form pipeline, so map films get word cues, `check`, the mix and the render gate like any other film.

## Steps

| Step | Command | Produces |
|---|---|---|
| map | `python3 scripts/mapart.py build <film>` | `maps/map-0..3/` (four sizes, cut into 1024 px tiles), `maps/paint-<id>-0..3/`, `maps/map.json`, and `fonts/` with each font's licence |
| pieces | `python3 scripts/mapart.py cutout <in.png> art/<name>.png [--doc] [--width 600]` | a collage piece with a scissor edge and shadow |
| the rest | `align`, `timeline`, `stills`, `check`, `render`, `final` as in longform.md | |

`build` downloads Natural Earth vectors (public domain) and the four fonts into `~/.motion-studio/` on first use (`MS_CACHE` moves it). Run it again after changing `"map"`, then run `timeline`, which embeds `maps/map.json`.

## script.json

```json
{"palette": {"BG": "#EFEBE3", "INK": "#1D1C20", "GREY": "#4a4740", "ACCENT": "#EDBD2E"}, "grade": {"vignette": 0},
 "map": {"bounds": [-14, 40, 16, 60], "detail": "50m",
         "featured": [{"id": "fr", "country": "FRA"}, {"id": "gb", "country": "GBR"}],
         "labels": [{"text": "France", "lon": 2.2, "lat": 47.2}, {"text": "Atlantic\nOcean", "lon": -8.5, "lat": 49.5, "sea": true}],
         "compass": [-9.5, 47.2]},
 "segments": [
  {"id": "s01", "type": "map", "narration": "Britain and France sit just thirty-three kilometres apart.",
   "data": {"camera": {"lon": 0.6, "lat": 50.6, "zoom": 0.55},
            "paint": [{"region": "gb", "at": "Britain"}, {"region": "fr", "at": "France"}],
            "pieces": [{"kind": "title", "lines": ["Under the", "Channel"], "fig": "FIG. 01", "until": "s03"},
                       {"kind": "tag", "text": "33 km", "lon": 1.6, "lat": 51.05, "dy": -90, "at": "thirty"}],
            "ink": [{"kind": "arc", "id": "strait", "from": [1.31, 51.13], "to": [1.86, 50.95], "at": "apart"}]}}]}
```

The palette and `"vignette": 0` keep the film's labels (the source line) in the map's ink and stop the engine's dark vignette dirtying the paper, which carries its own light vignette and drifting dust.

The paper (cloud tone, fibres, a few creases, grain) is printed into the map itself, so it moves with the map under the camera. The sea lines (lines along the coast, then wave lines) are only in the two finest sizes of the map: a wide shot shows plain sea and a clean coastline, and the lines appear as the camera closes in, as in the reference frames. The switch happens where the camera changes map size, so a long zoom shows the lines arrive in one step.

**`"map"`.** `bounds` is `[west, south, east, north]` in degrees. The map is `width` px wide (default 7200) and as tall as the bounds make it, projected equirectangular with longitude scaled by the cosine of the middle latitude. Draw it at least 25 degrees beyond any shot where you can: the camera is held inside the map, and the page warns when a shot had to be moved to stay inside. `detail` is `50m` (default) or `10m` (finer coasts for close shots, a bigger download). `featured` lists the regions that get a cross-hatch and can be painted, each an `id` you choose and a `country` (a Natural Earth name or ISO code). Other land stays plain paper; `"land_hatch": true` gives it a diagonal hatch, as the source prompt has it, which reads busier on screen. A featured region can also be a state or province: `{"id": "wa", "state": "Washington"}` (a name, an ISO 3166-2 code such as `US-WA`, or a postal code with `"country"`, since `WA` is also Western Australia), from Natural Earth's admin-1 layer, also public domain. At `50m` it covers the US, Canada, Australia, Brazil, China, India, Indonesia, Russia and South Africa; other countries' provinces need `"detail": "10m"`. `"label": true` on any featured region engraves its name at Natural Earth's label point (or `"label": "N. Carolina"` for other words; `label_size` sets its size). State borders are drawn thinner than national ones, with shorter dashes (2.6 px, 14 on and 12 off), for the countries whose states are featured, or for the countries listed in `"admin1_borders": ["USA", "CAN"]` (`[]` for none). Lakes large enough to read at the map's scale (an area of at least 1% of the map's width squared; `lake_min_px` changes it, `"lakes": false` turns them off) are cut out of the land, so the Great Lakes read as water. A whole-country US map projected like this looks wider and flatter than the Albers maps viewers know; it suits regional shots better than a full contiguous-US view. `labels` are engraved place names: countries in letter-spaced capitals, seas in italic (`"sea": true`), `\n` for a second line, `size` in map px (default 60). The engraving clears the hatching behind each name. `compass` places a compass rose; put it in open sea. `coast_lines` and `graticule` change the sea lines (px from the shore, default `[8, 19, 32, 48]`) and the dotted graticule's spacing (default 10 degrees).

**`camera`.** One key or a list: `{lon, lat, zoom, at?, frames?}`. Zoom is screen px per map px: about 0.3 shows a whole region, 0.6 a country, 1.3 a city. Without `at`, a key lands at its segment's start. Each move eases over 16 to 26 frames (longer for a bigger move; `frames` sets it), zoom in log space, stepped on twos. Plan one move every one to three sentences. The first key of a run is where it starts, with no move.

**`paint`.** `{region, at, until?}` paints a featured region in the film's accent colour (palette `ACCENT`, Vox yellow by default; `"accent"` in `"map"` overrides it, and tags, the highlighter, the FIG tag and the yellow cut-paper letters follow the same colour; run `build` again after changing it), multiplied at 90% so the hatching shows through, revealed from south to north over 16 frames on its word. It stays for the rest of the run unless `until` ends it.

**`pieces`.** Each has `at` (its word), a place (`lon`, `lat`) plus a pixel offset (`dx`, `dy`), an optional `rot` in degrees, and an end. Pieces keep a constant size on screen at any zoom. They arrive with a slap (1.1, 0.98, 1.0 on twos), never a fade, and every piece boils: it jitters ±1.5 px and ±0.4 degrees, a new jitter every 8 frames.
- `cutout`: `image` (from `mapart.py cutout`), `w` in px (default 260). `swap: {image, at}` changes it (the losing side's sad version). `along: "<ink id>"` sends it along that ink line over `travel` seconds (default 1.2) with a small wobble. `splash: true` adds ink spatter around it.
- `document`: the same, for a document cut-out made with `--doc` (line art on cream paper, no dot screen).
- `tag`: `text` on a yellow paper strip in Special Elite capitals.
- `note`: `text` in Permanent Marker, lower case, written on at 1.3 letters a frame; `size` 58 to 84 (default 64); `\n` for lines.
- `ransom`: `text` as cut-paper letters, one slapped down every 3 or 4 frames, cycling Caslon Bold, Anton and Special Elite on ink, beige and yellow squares; `size` (default 110).
- `title`: a beige card at the top left, 560 px wide, tilted −1.2 degrees, with a yellow `fig` tag ("FIG. 01"); its `lines` type on at one letter a frame, and its height follows the line count.
- `sheet`: a beige sheet at the bottom centre with a bold Caslon line (`text`) and an optional payoff `word` in ransom letters, underlined with a yellow highlighter once the letters land.
- `splash`: ink spatter on its own.

A piece ends at its segment's end. Text is held at least long enough to read it (2 s, or 1 s plus a second per three words, plus its write-on time), past its segment if it must be. `"keep": true` keeps a piece for the rest of the run, and `"until"` ends it on a word (`{"at": "word"}`, in its own segment) or as a later segment starts (`"until": "s05"`). Give every piece a sensible end: a tag left from the last beat collides with the next beat's.

**`ink`.** Marker marks, drawn in screen space every frame, so they stay 5 to 6 px wide at any zoom. Each is revealed along its own path (`frames`, default 12) and boils between three versions every 8 frames. A felt-marker body with a ±15% width wobble and tapered ends is overlaid with a dry crayon pass a third as wide, with gaps.
- `arrow`: `from`, `to` (`[lon, lat]`), `bend` (default 0.14); a 26 px head at ±0.48 rad.
- `arc`, `trail`, or `"dashed": true`: dashed, 27 on and 19 off (an arc bends 0.22 by default).
- `circle`: around `lon`, `lat`, radius `r` px (default 70), drawn with an overshoot.
- `x`: two 20 px strokes over 6 frames.
- `highlight`: a band of yellow (`width`, default 30 px), multiplied, from `from` to `to`.
Give an ink mark an `id` to send a cut-out along it.

## Collage pieces

`mapart.py cutout` takes a picture with transparency, or with a plain white background, which it keys out. It makes it grey, raises the contrast by 1.25, mixes in a 45-degree dot screen (5 px) at 34%, adds grain, puts a white scissor edge about 7 px wide with a wobble around it, and casts a soft shadow offset (5, 7) px. `--doc` keeps a document as line art on cream paper, with its paper. Pictures come from the image models (panel.md) or from public-domain engravings. Never a real person's likeness: use generic figures (a king, a sailor, a clerk).

## Sound

`mix` adds a paper tick (`pin`) for each piece and each cut-out letter, a marker scratch (`marker`) for each ink mark, typewriter ticks (`typewriter`) under a typed title, and a whoosh on each camera move after the first. They use recordings from the sound library with those roles (studio.py import-sfx), like named foley; a role with no recording stays silent, since the owner found sounds drawn in code amateur. Same-kind hits closer together than the library's gap are dropped.

## Checks

The pieces' text is checked like any other: size (tags and notes are drawn at the reading floor, 56 px, or larger), overlap, edge, contrast and reading time. Check every pinned note at both of its camera positions, because a note that fits a wide shot can sit off-screen when the camera pushes in: `check` reads a piece pinned to the map as still while the camera pans, but a zoom changes it, and it counts only the last stretch a text holds still. A run of map segments is one scene, so the "three of one type in a row" flag leaves it alone. The arrivals are allowed as hits in `final`'s pop check.

## Trade-offs

- The source prompt sets tags at 22 to 26 px. Here they are at the engine's reading floor (56 px at 1920 wide, about 11 pt on a phone), so they are much bigger and a crowded shot has room for fewer of them.
- The prompt draws coastlines from hand-written points. Natural Earth is more accurate and free of invented coasts, but it is a 1:50m (or 1:10m) generalisation: say coastlines are simplified when that matters. The credit is in `maps/map.json`.
- The prompt draws characters in code. Here cut-outs come from pictures, which look better but need a picture per character.
- The prompt synthesises its sound effects. Here they come from the library, so a film is silent where no recording carries the role.
- Lines come from distance fields at 7200 px, which keeps them even, but `build` takes 65 to 80 s on 4 CPUs and needs a few GB of memory for a large map.
- A map frame takes about 0.77 s to render with the browser's software raster (the cloud machines), against about 0.2 s for the same frame without the map: the cost is drawing the map tiles in view, scaled. Hiding off-view tiles is already done, and removing them from the page or pixelated scaling saved little (0.6 s at best, with visibly blocky lines). A 10-minute map film is about 4 hours of frame time there, split across `render`'s parallel 30 s chunks (how much that saves depends on free memory and CPUs); the Mac's GPU raster should be much faster, but that is not measured yet.
- Land outside the featured regions is plain (the reference look), not hatched as the source prompt says; `land_hatch` restores the prompt's version.
- Pinned text that moves during a zoom doesn't count as readable then, so a piece cued just before a move can be flagged as held too briefly although it read on screen.
