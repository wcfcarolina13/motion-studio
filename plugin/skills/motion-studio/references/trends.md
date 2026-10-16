# Trend radar

`scripts/trends.py` answers one question: which videos in a niche are doing far better than their creator usually does? That ratio, a video's views over the median of the same creator's other recent videos, is the "outlier score" paid tools sell. A 3x video from a 5,000-view creator is a stronger topic signal than a 2M-view video from a creator who always gets 2M.

## Commands
- `trends.py tiktok @a @b` or `--watchlist file.txt` (one handle per line, `#` comments): pulls each creator's recent videos (`--per-creator 30`) and ranks the breakouts.
- `trends.py tiktok-tag scam`: pulls a hashtag's videos (`--videos 60`), then each creator's recent videos for the baseline. Only the hashtag's videos are ranked.
- `trends.py youtube "tiktok scam" --days 14`: searches YouTube for the most-viewed recent videos on a query, then pulls each channel's latest uploads for the baseline.
- All three write `trends-<date>-<label>.md` (ranked table) and `.json` (every video seen) to `--out` (default `research/trends`). `--min-score 3` is the breakout threshold. Videos younger than `--min-age-days 1` get no score.

## Sources and their limits
- **TikTok** goes through yt-dlp (`python3 -m pip install yt-dlp`), with no account or cookie. It is unofficial, so it breaks whenever TikTok changes its site; the first fix is always `pip install -U yt-dlp`. A creator whose page fails is reported and skipped. Tested live on a GitHub runner on 2026-10-16: creator pages worked without a cookie (views and uploader came back), but hashtag pages failed with "No working app info is available", because yt-dlp's hashtag extractor needs TikTok mobile-app credentials. Until that changes, build a watchlist of creators per niche and use `tiktok`, not `tiktok-tag`. Scraping is against TikTok's terms; without a login, the exposure is an IP block, not a banned account.
- **YouTube** uses the official Data API v3 with a free API key (Google Cloud Console > APIs & Services > Credentials > Create API key, then enable YouTube Data API v3). The quota is 10,000 units a day; a search costs 100 and every other call 1, so one query with 25 channels uses about 150. The key goes in `YT_API_KEY` or `~/.motion-studio/youtube/api_key`, never in the repo.
- **Network:** tiktok.com is blocked from the default cloud container. Run TikTok scans on the owner's Mac or in a cloud environment that allows tiktok.com. googleapis.com is reachable from the cloud.

## How to use the results
- The score picks topics. It is not a reason to copy footage. The episode is original: narration, figures, and at most a few credited seconds for commentary. See `longform.md`.
- Prefer topics with a story that lasts (scams, money, people, mysteries) over dances and sounds. A trend that will be dead before the episode ships is a Short at most.
- Read the median column: a breakout against a tiny median (under about 1,000 views) is noise until it repeats.
