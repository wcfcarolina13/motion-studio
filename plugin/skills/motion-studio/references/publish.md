# Publishing a long-form film and its Short

The path from a locked script to a public video and its 48-hour review, written from the toll-text launch (2026-10-24). `L=${CLAUDE_SKILL_DIR}/scripts/longform.py`, `Y=${CLAUDE_SKILL_DIR}/scripts/youtube.py`. **[Studio]** marks a step done by hand in YouTube Studio on the desktop web, because the Data API has no call for it. Nothing goes public without the owner's explicit go-ahead in their own words.

## Gates before a full render

A 10-minute render takes about 45 minutes, so every gate runs on the timeline and on stills first, in this order:

1. `lines` before any voice is recorded: fix every `SAY:` misread and pin or reword every heteronym (it refuses unpinned ones).
2. Voice, then `voiced`, then `align`. Every clip uses one voice, pack and speed; fix a fast clip with pauses or holds, never with speed.
3. `timeline`: it stops on a clip that is unrecorded, changed, or voiced differently.
4. `stills --every 2.5` and look at the contact sheet. A text-only stretch longer than about 6 s is a slide; rebuild it as motion.
5. `check`: dead air, voice pace drift, text over text, cards over heads, reading text under the size floor, text cut by the frame edge (`critic.md`).
6. A suspected fix is measured on stills at the exact times (`stills <film> 61.2 61.25`), not on a new render.

## Render, mix, final

`render <film> --workers 4` → `mix <film> --plan` → `credits <film>` → `final <film>` → `captions <film> --lang <code> --text <code>.json` per language → `share <film>` for the owner's review link.

Render keeps finished chunks, so a failed run is re-run, not restarted (`longform.md`, "Render resumes"). Keep `--workers` at 4: at 6 a browser worker died and cost an hour (2026-10-24). `final` must pass verify (no pops, frame 0 drawn, loudness -14 LUFS within 1 dB, the decoded track peaking at or under -1 dBFS; `sound.md`).

The Short is cut from the finished episode with `short` and goes through the same gates in its own folder (`longform.md`, "Shorts"). It opens on its own voiced first line, not on a line from the middle of the episode.

## Upload, private

1. `$Y --channel <show> whoami` must name the show's channel; the token check refuses any other.
2. Chapters: copy the times from `out/chapters.txt` into the description and into every localized description. YouTube shows chapters only when the first is at 0:00, there are at least three, and each lasts 10 s or more.
3. Episode: `$Y --channel <show> upload out/<film>.mp4 --title "..." --description-file desc.txt --privacy private --category 27 --thumbnail <thumb.png> --synthetic --language en --localizations localizations.json`. The localizations file holds a title and description per language (`{"es": {...}, "pt-BR": {...}}`); the channel's disclosure line is added to each.
4. Captions: `$Y --channel <show> captions <id> out/captions.vtt --language en --name English`, then one call per language file (`out/captions.es.vtt` and so on).
5. Short: its own title (the hook, short enough to show whole on a phone), a description that opens with the link to the full episode, and the same localizations.

## Before public: Advanced features

Chapters, clickable links in descriptions, a Short's Related video, pinned comments, thumbnail Test & compare and extra audio languages all need the channel's Advanced features. Check Studio → Settings → Channel → Feature eligibility in a browser signed in as the channel's owner before going public, because a video published without them misses its first-day traffic on every one of those. Which features sit behind Advanced is YouTube's list as of 2026-10 and may change.

## Going public

Only after the owner's typed go-ahead, and in one sitting so the Short can send viewers to a live episode:

1. Episode: `$Y --channel <show> set-privacy <id> public`. It re-sends the AI disclosure with the privacy change, because a status update without it clears the label.
2. Short: the same command, straight after.
3. Check that the altered-or-synthetic label shows on both in Studio.
4. **[Studio]** Short → Related video → the episode → Save.
5. **[Studio]** Episode → Test & compare → Thumbnail → up to three thumbnails (`out/meta.json` `thumbnail_test`) → Set test. It only runs while the video is public.
6. Post the episode's first comment (there is no `youtube.py` command for it yet; Studio or the API's `commentThreads.insert`), then **[Studio]** pin it and check it shows as pinned by the channel.

YouTube then adds its own title translations and auto-dubbed audio tracks. If the channel reviews auto-dubs, approve them under Studio → Languages when they appear. An uploaded dub replaces YouTube's automatic one for that language, so delete the automatic one first.

## 48-hour review

At 48 hours, and again at 7 days, read these in Studio → Analytics:

- **Click-through rate** and the thumbnail test's standing (the test can run up to two weeks and picks on watch time, not clicks).
- **Average view duration and retention**, mainly the first 30 s: a steep drop there points at the hook or the intro's pace.
- **The Short's viewed-versus-swiped-away share**: if it is low, change the Short's opening line or hook text and render the Short again.
- **Comments**: remove any that claim to be the channel or ask viewers to message someone.

Write the numbers into the film's storyboard ("as shipped") or the owner's content notes, so the next topic is picked against them.

Trade-offs: four Studio steps stay manual (Related video, Test & compare, pinning, the Advanced check) because the API offers no call for them. A new channel's first 48 hours carry very little traffic, so read them as a check that nothing broke, not as a verdict on the film.
