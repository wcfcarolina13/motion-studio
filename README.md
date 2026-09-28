# motion-studio

A Claude Code skill for making short motion-graphics films in code: article trailers, explainers, product launch films, inbox digests. Claude writes the film as one HTML file where every frame is a function of time, renders it in headless Chrome, cuts licensed music to the beat, places recorded sound effects on their peaks, and checks the result before you see it.

What makes it more than "Claude, make me a video":

- **A thesis contract.** Before anything moves, the storyboard states the source's thesis and argument chain. Every edit and every reviewer note answers to it, because the easiest way to ruin a film is a series of reasonable cuts that quietly remove its point.
- **A blind read before shipping.** A fresh agent that has never seen the source looks at one still per bar plus the on-screen text and says what the film argues. If that doesn't match the contract, the film isn't done.
- **Music first, on the grid.** Tracks are measured (tempo, downbeat, a bar-by-bar loudness map showing the build, the drop and the breakdown), and the film is cut to the song's bars rather than the other way round. The downbeat is corrected automatically from where the song's sections change.
- **A live preview you can annotate.** Scrub or play the film with its soundtrack in the browser, pin notes at the playhead ("slow this push", "hard cut here"), and Claude reads them, fixes the film and marks them done while the page hot-reloads.
- **Verification.** Every final is checked for format, fast start, a clean decode, colour range, loudness (−14 LUFS), and single-frame glitches.
- **Variety.** A film ledger records each film's signature move, track and sound palette so the next one doesn't repeat them.

## Install

```bash
claude plugin marketplace add wcfcarolina13/motion-studio
claude plugin install motion-studio@motion-studio
```

Requirements: Python 3.10+ with `pip install playwright numpy scipy`, Google Chrome (rendering uses the installed Chrome through Playwright, so there's no browser download), and ffmpeg (`brew install ffmpeg`). The Codex CLI is optional, for a second-model reviewer.

Then ask for a film in plain words, e.g. *"Make a 30-second 4:5 video of this article: https://…"*, and the skill takes it from there.

## Configure (optional)

Copy `examples/motion-studio.json` to `~/.motion-studio.json` to choose where films, the audio library and the ledger live (defaults are under `~/motion-studio/`). Copy `examples/house.md` to the `house` path and fill it in: your palette, your standing rules, which models play reviewer. The skill reads it before every film, so nothing personal has to live in the skill itself.

## What's inside

```
plugin/skills/motion-studio/
  SKILL.md                    the workflow
  scripts/studio.py           the CLI: new, stills, draft, final, verify, serve, notes, mix, fetch, measure, blindpack…
  scripts/kit.js              the engine: springs, keyframes, camera, mask captions, clip helpers (copied into each film)
  scripts/audio.py            the audio library, track measurement, and the Mix every film's sound.py uses
  scripts/preview.*           the live preview server and page
  references/                 story, motion vocabulary, sound, review checklists, the review panel, formats
  assets/templates/           film.html, storyboard.md, sound.py, ledger.md
```

Formats: 4:5 (default), 9:16 and 16:9, each with its own caption band.

## Audio licences

No audio ships with this repo. `studio.py fetch` downloads from [Mixkit](https://mixkit.co) (the Free License covers social, YouTube and ads, but not broadcast, games or music-only remixes; sound effects are free for any video) and [Kenney](https://kenney.nl) (CC0). Every downloaded asset is recorded in the library's `catalog.json` with its source and licence.

## Credits

Built from four X threads and what three films taught us:

- [@rexan_wong](https://x.com/rexan_wong/status/2103707054108299437): the workflow (reference styles, a code renderer, real components, storyboard variants, stills first, director notes).
- [@shiri_shh](https://x.com/shiri_shh/status/2103521939134550246): [latent-spaces/brag](https://github.com/latent-spaces/brag), a teaser-with-sound-design skill.
- [@anthonyriera](https://x.com/anthonyriera/status/2103801891528573089): [Rieranthony/product-film-skill](https://github.com/Rieranthony/product-film-skill). Its `beats.py` is vendored here under its MIT license (see `scripts/LICENSE-product-film.txt`).
- [@twoclipping](https://x.com/twoclipping/status/2103835273813496100): the pure-code method (one HTML file, `seek(t)`, Playwright + ffmpeg, subframe motion blur).

## License

MIT (see `LICENSE`). The vendored `beats.py` keeps its own MIT notice.
