# Style catalogue

The looks and formats the engine can make, and the ones studied but not built yet. A built style has its own reference with the settings that turn it on. A studied one has a short breakdown and a build route here until someone builds it, and then it moves up and gets its own file.

Every style follows the same rules. We reverse-engineer from frames of public videos and describe the look in our own words. The cast, palette and sets are always our own. No code, prompt, character or asset comes from the source unless its licence is recorded and allows commercial use. A build starts with a frame study of the original, the same way the cel style did.

## Built

| Style | Reference | Turn it on |
|---|---|---|
| Old hand-inked cartoon cel | `styles/cel.md` | `"finish": "cel"`, the cel prompt block in codex_stills |
| Engraved paper map with collage pieces | `maps.md` | a `map` segment |
| Collage house style (tags, marker ink, paint on stills) | `longform.md`, "Collage style" | `"style": "collage"` in script.json |
| Paper cutouts in toon-lit 3D sets (2.5D) | `set3d.md` | a `set3d` segment |
| Sprite stacks: pixel props that turn | `sprite-stacks.md` | props in a canvas-drawn pixel film |

## Studied, not built

### Five Shorts formats from one X article (2026-11-27)

Source: an X article by @theoardley, "i turned 5 of the biggest video formats on YouTube into Claude prompts", posted 2026-11-27 and read on the Mac through fxtwitter the same day. Only the article's text came through. Its frames, its clips and its five prompt boxes did not, and the prompts carry no licence, so we don't use them. Each entry below comes from the article's description and has not been measured against the original channel yet. The author tested every prompt on one Windows PC with an NVIDIA card and never on a Mac.

The article's own lessons match rules we already keep:
- Judge a format by its worst video, not its best. growth.md already reads the floor.
- Measure the original's sound before adding any. Is there music, at what tempo, and how loud is it under the voice? Two of the five run no music at all.
- Put your frames under the original's before posting. His first building Short had sky in 10% of its frames against the original's 69%.
- When the result is plainer than the original, fix the prompt or the engine and run it again. Don't fix the video by hand. This is the same as our rule that every fix goes into the engine.

All five would use Kokoro (Apache-2.0) for every draft voice. ElevenLabs comes in only after Bradley signs off the final cut. Sound effects come from Kenney (CC0), Mixkit or our own recorded foley, never ElevenLabs effects.

#### 1. World-history whirlwind (after bill wurtz)

- **What it is:** a 45 to 60 second Short. A globe dives from place to place while big lowercase words land one phrase at a time, fast enough that people watch it twice. The subject is one thing that travelled, such as a food, a drink or an invention. The originals run no music under the voice.
- **Reuses:** three.js (already in `scripts/`, MIT) for the globe, the map pipeline's camera-on-words timing, and the panel's word cues for the type.
- **New work:** a globe segment (a sphere textured from Natural Earth, which is public domain) and a kinetic-type layer of big lowercase phrases cued to words.
- **Fit:** Longtok Shorts. A trend topic that travelled becomes a 60 second history.
- **Trade-off:** most of the appeal is the creator's own deadpan voice and timing. A Kokoro voice may make it feel flat, which the article itself found with its free voice. The look is plain on purpose, so it can read as a copy of one person. It needs our own type and palette to stand apart.

#### 2. Stick figure against the screen (after Alan Becker)

- **What it is:** a 30 to 50 second wordless cartoon. A stick figure fights an app on the screen, friends join in, and it ends in a smash. The sound is all effects and the originals run no music.
- **Reuses:** `figure.js` posing and blending, panel UI screens, and the foley pipeline.
- **New work:** a thin stick-figure rig with fast action poses (a fight needs speed that `figure.js` poses weren't built for), smear frames, and UI parts that break apart. The article says the figure's size against the screen mattered and had to be measured from the originals.
- **Fit:** weak. Neither K-factory nor Longtok is wordless.
- **Trade-off:** this is the highest look-alike risk of the five. The creator's characters are distinctive and well known, so anything close reads as a rip-off. If built at all, it needs our own figure, colours and screens. Recommended last.

#### 3. One odd act, then "You see," (after Zack D. Films)

- **What it is:** a 38 to 50 second 3D story Short. Line one is the strangest act, every sentence after it is one visible action, and the reason comes last, after "You see,". The article says to keep the camera calm because fast action looks stiff in Blender.
- **Reuses:** the story grammar needs no new code. It is a script template for `contract-and-story.md` and works with any look, set3d included.
- **New work for the full look:** the engine has no Blender route. A 3D build needs one: Blender (GPL, and what it renders is ours), CC0 characters and props (Quaternius, Kenney or Poly Haven, licence recorded per asset), and a headless render step with word cues. That is a large build.
- **Fit:** strong for K-factory Shorts and Longtok hooks, as a grammar first.
- **Trade-off:** the grammar is cheap and portable. The Blender look is the costliest build here, and the article warns that it looks stiff.

#### 4. Science Short in flat shapes (after Kurzgesagt)

- **What it is:** a science Short of about a minute. Flat shapes in a few tones on a dark indigo base, round-eyed characters, and one surprising answer to an everyday question (why onions make you cry), with every action the script names happening on screen. A quiet music bed is fine here.
- **Reuses:** the code-drawn pipeline, `figure.js` restyled with round-eyed faces, panel reveals, and the mix with its gap check.
- **New work:** a flat-vector house look (palette, soft glows, round-eyed figures) and a "the script names it, the picture does it" check, which is close to our picture-truth rules.
- **Fit:** strong for Longtok explainers.
- **Already queued:** the Content Factory research queue has an open item for the same look from a different author's prompt ("Can't replicate kurgezakt? Bah! Examine this"). Do them as one study.
- **Trade-off:** the original's polish is very high, and a near miss looks cheap. It is also the most imitated look on YouTube, so ours needs its own palette to stand out.

#### 5. "How it was designed" building Short

- **What it is:** a 3D city to the horizon with the real neighbouring buildings. You are the designer, each site problem gets its fix, and the building's name comes in the last line. Sky fills most frames, from a long lens and a low camera.
- **Reuses:** set3d's camera-on-words, and three.js if we build it in the browser.
- **New work:** a city from OpenStreetMap footprints and heights. OSM is ODbL, so every render needs the credit "© OpenStreetMap contributors" in the description. It also needs a sky (a CC0 HDRI from Poly Haven) and a lens and camera spec measured from the original. In the browser, three.js can extrude the footprints, which avoids Blender at the cost of a plainer look.
- **Fit:** a Longtok niche (architecture "why"), not a stream we run today.
- **Trade-off:** fetching OSM data, sky files and models needs the Mac, because the cloud can't reach those sites. Real buildings shown as designed by named firms need the facts sourced.

#### Order if we build any

1. Story grammar from #3, as a script template. It costs little and K-factory can use it straight away.
2. #4 science Short, together with the queued Kurzgesagt item. It has the most engine reuse and fits Longtok.
3. #1 globe whirlwind. It reuses three.js and the map timing.
4. #5 building Short, only if Longtok takes on the niche.
5. #2 stick figure, last or never, because of the look-alike risk and the weak fit.
