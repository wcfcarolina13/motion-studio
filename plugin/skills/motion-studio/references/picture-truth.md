# Picture truth: scale, objects, light, places

These are the owner's rules from a review of a long-form explainer's stills (2026-11-24). The notes were: a man shining a flashlight into an overturned portable toilet drawn the size of a car; no shot showing what the unit's underside really looks like; a night scene under a bright sky; a ground too white to look at for long; and the job never shown in the places it actually happens. He asked for them to be thought through "with high creative temperature" and applied to every film. Each rule below is the rule, then what it opens up. Rules marked **gate** are enforced by the engine; the rest are checked in review (review.md) and by the "spot the mistake" pass in rule 10.

## 1. True scale, every time (gate)

- **Size sheet.** script.json `"sizes"` lists every person and recurring prop with its real height in metres, plus width and depth when it can lie on its side or be seen end-on, and a `source` (spec sheet, catalogue, measured note):
  ```json
  "sizes": {"man": {"h": 1.78, "source": "adult male median, CDC"},
            "porta-potty": {"h": 2.31, "w": 1.12, "d": 1.22, "source": "https://www.satelliteindustries.com/restrooms/standard/tufway/"}}
  ```
  A panel names what is in it with `char_in_frame` and `"props": [...]`. This is the studio line-up sheet: every character and prop drawn to one scale.
- **Prompts.** `longform.py panels` adds a scale sentence before each prompt's style ending, built from the sheet: "Scale: the porta-potty stands about 1.3 times the man's height (2.31 m against 1.78 m), its top well above his head." Image models pull objects toward a "typical" size, and without size cues the best of them get only about 57% of object pairs plausible (GenScale, 2026). Body landmarks ("level with his hip") work better than numbers alone. Reference images can override scale: say a reference is for face and clothes only, and when scale and identity fight, get the scale right first and apply identity in a second edit.
- **Check.** `longform.py scale <film>` writes `out/scale/index.html`. For each panel with two or more sized things: click the horizon (eye level; type a percentage when it is off the frame), then mark each thing's base where it meets the ground and its top, choosing height, width or depth (an overturned unit is measured by the side now facing up). **Ruler** draws, wherever you click on the ground, how tall each thing in the sheet should stand there; **Grid** stands 2 m posts, with a tick at 1 m, on the ground at several depths (the owner's grid idea, drawn in perspective so it reads true anywhere in the frame). Download the marks and run `longform.py scale <film> --marks marks.json`; verdicts go to `out/scale.json`. A failure names the size error and the exact fix ("make the porta-potty 0.72x its drawn size relative to the people"), which edit models follow far better than "fix the scale".
- **Gate.** A film with `"sizes"` will not `render` until every such panel has passed on its current image; a redrawn image needs a new check. `"skip": "<reason>"` on a panel's marks (the Skip button) passes a still that can't be measured: an overhead shot, a hard camera tilt, or nothing standing on the ground.
- **How it measures.** Single-view metrology (Criminisi, Reid and Zisserman; Hoiem, Efros and Hebert 2008, eq. 6): on level ground, a thing standing at image row `y_base` with its top at `y_top` is `H_cam * (y_base - y_top) / (y_base - y_horizon)` tall. Each mark gives an estimate of the camera height; the people in frame set it, and each prop must agree within 20%. So a toilet nearer the camera can look bigger than the man and still pass. Without a horizon, only things standing on the same ground row are compared, by pixel height. Trade-offs: someone marks each still by hand (a few clicks a still), because no licence-safe detector reliably finds a drawn porta-potty (OWLv2 and Grounding DINO are Apache-2.0 and Florence-2 is MIT, but none was tested on our drawings; Depth Anything V2 is Apache-2.0 only in its Small size, and the larger ones are non-commercial). The formula assumes a level camera and upright things on the ground; it drifts for objects far from the image centre with a wide lens, and the method's own error is around 5-10%, which is why the tolerance is 20% rather than tighter.
- **Further.** Scale is a storytelling tool: plan at least one deliberate scale shot per act (one truck against a yard of two thousand units; a small tank beside a vault; a tiny wallet beside a huge receipt).

## 2. Object sheets, like character sheets

- Any object the story keeps returning to (the product, the vehicle, the tank) gets a researched object sheet before any drawing: real dimensions (they go in `"sizes"`), parts, the underside, materials, and sources, kept in the film's `sources/`. Its visual description is reused word for word in every prompt, as a character's is.
- **Further.** The sheet doubles as a teaching asset (rule 3). The hero object can age through the film: clean in month 1, scuffed by month 6, cracked by winter.

## 3. Breakdown segments when parts matter

- When the script turns on how something is built (repairs, costs, failure points), add a 30-60 s anatomy segment early, before the problems it explains: a cutaway or exploded view drawn without text, with labels in a real font laid over it by the engine, each arriving on its word. Each part named there should pay off later in the film (the part that breaks in act 2 was introduced in the breakdown).
- Draw anatomy from the object sheet's sources, not from a model's guess. Image models invent internals, so a diagram-style drawing the engine builds is usually more accurate than a generated still. The `cutaway` scene (longform.md) is that drawing: the object to scale from `"sizes"`, a person beside it, and each part in real units with its label on its word. **When the image model gets an object's insides wrong twice, stop re-prompting and use the cutaway.** A generated still can still carry the mood around it (the hero lifting the lid), with the cutaway carrying the facts.
- **Further.** An X-ray reveal (the shell fades to show the tank); "the five parts that break" as a countdown; a before-and-after of a repair; a cost tag pinned to each part, only with a sourced figure (or "price on request" when the price isn't public).

## 4. Light matches time and place (lint)

- Every shot's prompt says its time of day and weather. Night, dusk and dawn use their own style recipe: the sky is dark, the light sources in the scene (a flashlight, headlights, a work lamp) are the only light, and shadows fall away from them. A layout shared across shots (a pale sky band, a paper corner) never applies to a dark scene. Weather stays the same within a scene.
- `lint` and `panels` warn on a dark scene (night, flashlight, dusk…) whose prompt doesn't say its sky, and on a prompt that says both night and a bright sky.
- **Further.** The palette can follow the calendar (warm spring, hard summer light, grey-blue winter), so the season reads before any month tag does; a HUD counter's colour can echo the light.

## 5. Show every real place the work lives

- The shot list covers every place the script names, and every place the business actually runs (for a rental business: the yard, the route, construction sites, remote campgrounds, festivals, the dump station). A business explainer that never leaves the yard has a missing shot list.
- **Further.** A recurring route montage (the same truck in a new place every 2 s) shows a week of work; a map scene (maps.md) shows how far apart the places are.

## 6. Show the work, not just the worker

- For every process the narration describes, at least one close shot of hands doing that step. POV shots put the viewer's own hands on the tool, which suits second-person scripts. Insert shots can carry a number without text: a fuel gauge, a tank gauge, an empty wallet.

## 7. No static holds (flag)

- Something new every 5-8 s. `check`'s beat-length flag (critic.md, 5 s) already flags any stretch of voice where nothing new enters or starts moving; a camera move that only continues does not count as new. Vary the kind of change, not just the picture: a push-in on the same still, a cut to a close-up, a map flight, a counter tick, a match cut between months (the same yard, fuller). Trade-off: it is a flag, not a stop, because a held moment can be the point; mark those `"steady": true`.

## 8. Easy on the eyes (flag)

- Grounds are warm cream paper, never paper white. `panels` flags a still where over 25% of pixels have every channel at 235 or more, and `lint` flags prompts asking for a white background. Grade upscaled stills down after upscaling, since upscalers brighten highlights. Map paper matches the stills.
- **Further.** Each channel can have its own paper tone, so a thumbnail is recognisable by its tone alone.

## 9. No fake writing

- Covered by the generated-text rule (review.md): prompts never ask for writing; any text on screen is ours, in a real font, laid on top; every still gets a garbled-text check. Real-font text can become a design element of its own: tags, stamps, receipts, cut-paper letters.

## 10. Places and objects true to life

- Settings are researched like facts (forest vault toilets are small wooden or concrete buildings, not plastic units). When unsure, find a reference photo first and record it in `sources/`.
- **Spot the mistake.** Before render, a second model looks at each still for physics, scale, lighting and anatomy only, never style: is anything floating, too big, lit from the wrong side, lit by a sky that shouldn't be bright, built wrong? Its findings are leads for a person to confirm, since vision judges agreed with people only about 60% of the time in our tests (longform.md, Panel quality).

## 11. High creative temperature, on purpose

- For each act, sketch three to five distinct visual ideas before writing shot prompts, then pick the strongest. Never only the literal illustration of the sentence.
- **Idea bank:** visual metaphors (cash draining out like waste from a tank); recurring motifs (the truck as a character, the hero's jacket getting dirtier); match cuts; split screens (your month 3 against a big company's month 3); a time-lapse of the yard filling; weather as mood; one colour accent tracking money through the film.
