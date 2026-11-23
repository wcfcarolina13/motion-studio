# Publishing a long-form film and its Short

The path from a locked script to a public video and its 48-hour review, written from the toll-text launch (2026-10-24). `L=${CLAUDE_SKILL_DIR}/scripts/longform.py`, `Y=${CLAUDE_SKILL_DIR}/scripts/youtube.py`. **[Studio]** marks a step done by hand in YouTube Studio on the desktop web, because the Data API has no call for it. Nothing goes public without the owner's explicit go-ahead in their own words.

## Gates before a full render

A 10-minute render takes about 45 minutes, so every gate runs on the timeline and on stills first, in this order:

1. `lines` before any voice is recorded: fix every `SAY:` misread and pin or reword every heteronym (it refuses unpinned ones).
2. Voice, then `voiced`, then `align`. Every clip uses one voice, pack and speed; fix a fast clip with pauses or holds, never with speed.
3. `timeline`: it stops on a clip that is unrecorded, changed, or voiced differently.
4. `stills --every 2.5` and look at the contact sheet. A text-only stretch longer than about 6 s is a slide; rebuild it as motion.
5. `check`: the page gate (no JavaScript or console errors; `render` refuses a film without a passing check), dead air, voice pace drift, text over text, cards over heads, reading text under the size floor, text cut by the frame edge (`critic.md`).
6. A suspected fix is measured on stills at the exact times (`stills <film> 61.2 61.25`), not on a new render.

## Render, mix, final

`render <film> --workers 4` → `mix <film> --plan` → `credits <film>` → `final <film>` → `captions <film> --lang <code> --text <code>.json` per language → `share <film>` for the owner's review link.

Render keeps finished chunks, so a failed run is re-run, not restarted (`longform.md`, "Render resumes"). Keep `--workers` at 4: at 6 a browser worker died and cost an hour (2026-10-24). `final` must pass verify (no pops, frame 0 drawn, loudness -14 LUFS within 1 dB, the decoded track peaking at or under -1 dBFS; `sound.md`).

The Short is cut from the finished episode with `short` and goes through the same gates in its own folder (`longform.md`, "Shorts"). It opens on its own voiced first line, not on a line from the middle of the episode.

## Upload, private

1. `$Y --channel <show> whoami` must name the show's channel; the token check refuses any other.
2. Chapters: copy the times from `out/chapters.txt` into the description and into every localized description. YouTube shows chapters only when the first is at 0:00, there are at least three, and each lasts 10 s or more.
3. Episode: `$Y --channel <show> upload out/<film>.mp4 --title "..." --description-file desc.txt --privacy private --category 27 --thumbnail <thumb.png> --synthetic --language en --localizations localizations.json`. The localizations file holds a title and description per language (`{"es": {...}, "pt-BR": {...}}`); the channel's disclosure line is added to each. To gate the upload on your own check (a translation report, a claims sheet), add `--pre-upload-check "<command>" --package <dir>`, set `YT_PRE_UPLOAD_CHECK`, or (for a check every upload to a channel needs) put `"pre_upload_check": "<command>"` on the channel in `channels.json` and pass `--package`. The flag wins over the variable, which wins over the channel's: the command runs before anything is sent to YouTube, gets the folder as its last argument, and a non-zero exit refuses the upload with the command's output as the reason. It runs without a shell, so wrap pipes or `&&` in `sh -c '...'`. A channel can list several checks (`"pre_upload_check": ["<translation check>", "python3 <skill>/scripts/policycheck.py"]`); they run in order and the first to fail refuses.
4. Captions: `$Y --channel <show> captions <id> out/captions.vtt --language en --name English`, then one call per language file (`out/captions.es.vtt` and so on).
5. Short: its own title (the hook, short enough to show whole on a phone), a description that opens with the link to the full episode, and the same localizations.

## Policy checks before upload

YouTube can demonetise a channel of AI-assisted videos for "inauthentic" (mass-produced or repetitive) content, and remove videos or channels under its spam policy. These checks come from the project's audit of YouTube's own pages (2026-11-23). The page ids below are YouTube Help answers (`support.google.com/youtube/answer/<id>`), with the section each check rests on: 1311392 is the channel monetization policies, 2801973 the Spam Policy, 14328491 disclosing altered or synthetic content, 2802168 channel terminations. Re-read them before relying on a check: YouTube rewrites them.

**Per episode.** After watching the cut, write `policy.json` in the upload package (`python3 scripts/policycheck.py --template` prints an empty one) and answer:

| Id | Question | Passes | Source |
|---|---|---|---|
| M1 `M1_new` | One sentence: what does this episode add that the channel's last three didn't? | a sentence of 8 words or more, not copied from another package | 1311392: "materially varied", a "distinct storyline, focus, or concept" |
| M2 `M2_same_outcome` | The same situation with the same outcome as an earlier episode, only names or numbers swapped? A recurring frame is fine. | false | 1311392 |
| M3 `M3_unnarrated_slideshow` | Any slideshow or scrolling text with no narration? `longform.py check` already flags text-only stretches. | false | 1311392, Generic or repetitive content |
| M4 `M4_ai_expert_advice` | An AI voice or character posing as a human expert giving finance, health, legal or political advice, instead of attributing it to named sources? | false | 1311392, AI personas related to sensitive topics |
| M5 `M5_realistic_fake_events` | Realistic shocking imagery, or events that didn't happen? | false | 1311392, Unsatisfying or off-putting content |
| S1 `S1_title_thumbnail_deliver`, `S1_payoff_at` | Do the title and thumbnail promise only what the video shows, and where (m:ss) is that paid off? | true, and a time | 2801973, Malicious clickbait |
| S2 `S2_schemes_or_payouts` | Does it promote a scheme, a job offer or a payout? | false | 2801973 |
| D1 `D1_ai_disclosure`, `D1_provenance_kept` | Is the altered or synthetic content disclosure set (`--synthetic`), and are C2PA data and model watermarks left in place? | disclosure true, or `"not needed: <why>"` for clearly animated work in which nothing looks real; provenance true | 14328491 (disclosure); 2801973 (stripping them to evade detection is spam) |

`policycheck.py <package>` refuses a missing file, an unanswered question, a failing answer, a misspelt key, or an M1 sentence identical to another package's beside it. Wire it as the channel's pre-upload check (above), so no upload skips it. Trade-offs: it checks that each question was asked and answered, not that the answer is true; a person watching the cut is the check. M1 is compared only with the packages in the same parent folder.

**Monthly, per channel.** Put the newest five uploads side by side (titles, thumbnails, a contact sheet each, the M1 sentences). If two could swap substance, the next episode changes its angle. Vary the music bed and the picture set per episode: YouTube's own spam example is the same music, repetitive AI imagery and an AI-read script.

**Never.** Re-rendering or editing to remove AI watermarks or C2PA data, and splitting a channel into several accounts to spread a strike (the audit cites 2802168, channel terminations, against it).

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
