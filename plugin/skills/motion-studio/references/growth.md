# Growth specialist (packaging seat)

This seat decides whether a stranger's thumb stops and stays. It owns **packaging**: frame 0, the hook, the loop, the title, the cover and the publish slot. Facts belong to the claim audit, meaning to the creative director and timing to the picture editor. It argues for attention inside the truth rules, never around them.

Adopted 2026-11-16, after Bradley rejected two rounds of roadkill Short titles as "LinkedIn posts". The research behind it is in `research/shorts-business-trial/growth/shorts-growth-research-2026-11-16.md`. Source tags: [YT] YouTube official, [M] measured study, [P] practitioner, [J] synthesis.

## What matters, in order

1. **Stayed to watch** (viewed vs swiped away), then **average % viewed** and average view duration, then loops, then subscribers. Likes, comments and shares come last; Galloway found no strong link between them and performance [M].
2. **Raw views lie.** Since 2025-03-31 YouTube counts a view from frame 1 and counts every loop [YT]. Judge on engaged views, stayed and APV.
3. **Benchmarks:**
   - Shorts under about 60% stayed rarely do well; top Shorts sit at 70–90% (Galloway, 5,400 Shorts) [M].
   - 70–79% stayed is working and 80%+ is strong. APV on a 60 s Short: 60–75% working, 75%+ strong. Under 35 s, aim for 80–90%+ (1of10) [P].
   - **Shoestring bar:** at least 75% stayed and at least 70% APV at about 60 s, judged against the channel's rolling median of its last 10 Shorts, not against fixed numbers [J].
4. **The feed is won by frame 0 and the first 3 seconds, not the title.** A low stayed share means the hook. The title drives search and the Home shelf [P/M]. The cover shows on Home and the channel tab, not in the feed, so treat it as hygiene, not a lever [P].

## When it runs

1. **On the script, before voicing.** Run the pre-voice checklist below. Write the hook and the last line first [P].
2. **On the package, before upload.** Check title, frame 0 and cover together.
3. **After publish:**
   - 1 h: breakage only (processing, claims, wrong channel).
   - 24 h: first read of stayed.
   - 48–72 h: the verdict and post-mortem.
   - 28 d: close [P].

## Pre-voice checklist

- [ ] Frame 0 reads muted, like a thumbnail: one centred subject, 3 objects at most, already mid-action, with no setup or title card [P].
- [ ] The hook lands in under 3 s and opens a question that the last line answers [P].
- [ ] The payoff is foreshadowed by 3 s [P].
- [ ] Beats are linked by "but" or "so", never "and then". There's no intro [P].
- [ ] The picture changes every 2–3 s, and there's a peak in the middle [P].
- [ ] Try cutting the last second. The end loops into the start [P].
- [ ] Length is earned: hits on comparable channels run 14–43 s, and ours run about 60 s [M, Koerner pull].
- [ ] Title: 40 characters or fewer, spoken style, nothing corporate [M/P].
- [ ] "Speak within 0.5 s" is unproven, so don't enforce it [P].

## Titles

- About 40 characters show on mobile. The median trending Shorts title is 4–6 words and 20–40 characters (TunePocket, 10k titles) [M].
- It should sound like a person texting a friend. No colon-subtitle pitch ("Starting a X Business: About $Y"), no "here's how", no consultant words. If it would fit on LinkedIn, rewrite it (Bradley, 2026-11-16).
- Include the word people would search (roadkill, porta-potty).
- A specific number beats a round one ($75.73, not $75). Use one number per title.
- **The series option** [J, from Koerner]: one fixed series title plus a part number in a box on frame 0, with the business named on screen rather than in the title. The title then stops pitching and signals "the show you've seen before". It's Bradley's call; it hasn't been tried on Shoestring.
- Re-titling a live Short is low risk [P]. Re-uploading a dud is not (spam risk): rebuild it with a new hook instead [P].

## Truth gate (non-negotiable, checked before anything goes to Bradley)

- Every title is literally true, including where a phone cuts it. One county's contract never becomes the whole state's price, an estimate never becomes a fact, and revenue never reads as profit.
- No promise the video doesn't pay off in its first 10 seconds.
- Never call a company a scam or a wrongdoer.
- No "you won't believe", no emoji walls, no all-caps sentences.

## Timing

- YouTube says it doesn't optimize for posting time (Sherman) [YT]. Buffer's 1.8M posts show Friday 4–7 pm local as best, with a small effect [M].
- Our two ~1,200-view Shorts went out around 5:30 PM Eastern. The one 2:41 AM post and the 3:48 PM Monday post did worse, but with n=5, that's a hint, not proof [J].
- Default slot: late afternoon Eastern, kept constant, so the slot isn't a variable in tests.

## Testing

- Change one variable per batch, with at least 5 Shorts per arm, in the same slot.
- Compare medians, and call a winner only on a gap of about 5+ points [P].
- Log every test in `tools/channels/title-log.md`.

## 48-hour post-mortem

1. Record:
   - views and engaged views;
   - stayed and APV;
   - AVD and loops;
   - subscribers per 1,000 engaged views;
   - traffic sources (Studio → video Insights).
2. Compare each number with the median of the last 10 Shorts.
3. Diagnose:
   - **Low stayed:** the hook or frame 0.
   - **Good stayed, low APV:** pacing. Find the first steep drop on the retention curve.
   - **Good APV, few subscribers:** topic fit.
4. Log one hypothesis and one variable for the next Short. A bad first second can't be fixed after upload; it becomes a rule for the next build.

## Method (running the seat)

1. Brief a fresh agent (model `opus`) with `prompts/growth-specialist.md`, filled in with:
   - the script and a description of frame 0;
   - the claim limits;
   - the title log rows and the current numbers;
   - Bradley's latest complaint in his own words.
2. Check every candidate against the truth gate yourself. Drop any that fail; don't soften them.
3. Show Bradley at most 5 options, top pick first, with one line of trade-off each. Nothing goes live without his typed line.

## What we know so far (Shoestring Shorts; update as data comes in)

| Short | Title | Day-1 views | Stayed |
|---|---|---|---|
| Dog poop | "$3M in One Year Scooping Dog Poop? What It Costs to Start" | ~1,200 | — |
| Trash bin | "Starting a Trash Can Washing Business in San Diego: About $1…" | ~1,200 | — |
| Porta-potty | "$4.3M in One Year Renting Porta-Potties? …" | ~600 by 40 h | 58% |
| Roadkill | "NC Pays $75.73 Per Dead Animal Around Durham" | 71 at 3.7 h | 29% |
| Smoothie | "Copying a Seoul Smoothie Stand in San Diego Starts at $3,700" | 61 | — |

All five are below the stayed bar, so the hook is the first lever, not the title.

### The Koerner Office, as a comparison (pulled 2026-11-16)

Data: 153k subscribers, last 150 Shorts, saved in `research/shorts-business-trial/koerner-shorts-titles.tsv`.

- **Titles:** about 140 of the 150 carry the identical title, "The Most Overlooked Business Ideas of 2026", including all of the top 13 (10k–33k views each). The few distinct titles are plain speech ("He works 10 hours a week and makes $58k a year").
- **Length:** most hits run 14–43 s.
- **Frame 0, seen in two of the top Shorts:**
  - the odd thing is already mid-action (kids' cars driving into the sea, a machine eating a yoga mat);
  - a white box at the top shows the series name and part number ("…- Part 630");
  - captions show one or two words at a time, with a red key word;
  - the host sits in a corner, talking to camera.
- **What carries over to us:** the series and part number, and the subject mid-action at frame 0. We have no host face.
- **Caveat:** their subscriber base makes their views a poor yardstick for ours. Copy their shapes, not their numbers.
