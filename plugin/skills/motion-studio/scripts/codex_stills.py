"""Stills from ChatGPT images through the Codex CLI on the Mac: one `codex exec` per shot, no Colab.
    python3 codex_stills.py shots.json <out_dir> --allow-codex [--tries 3] [--only s03 s07] [--sizes script.json] [--edit-from base.png]

Every call uses the owner's Codex plan allowance, so nothing runs without --allow-codex (or CODEX_STILLS_OK=1). Since
2026-11-25 it is the default route for finished stills (the owner: "Chatgpt makes the best images by far"); the
open-model Colab route is the fallback when the allowance runs short. It won the bake-off on anatomy, scale, night
skies and text.

shots.json is the stills route's format: {"style", "faceless"?, "sizes"?, "cast"?, "props"?, "shots": [{"id", "prompt",
"style"?, "faceless"?, "char_in_frame"?, "props"?, "write_on"?}]}. File-level "cast" and "props" are {"name": "what it looks like"}
(the lead, the unit model, the truck) and go into every prompt, edits included, so each picture keeps them the same
and adds none that aren't there. A shot's own "props" stays a list of size-sheet names. A shot's "write_on" names the
signs, tags, papers or screens the engine will letter (["the price tag"]): the prompt asks for each with a clean, empty
writing area, and the panel's "screen" writes the words on at their cue (references/styles/cel.md, "Blank props"). Settings, negative prompts and seeds don't apply here; "model" is ignored.

Scale (picture-truth.md, rule 1): "sizes" is the film's size sheet (script.json's, same format), or pass
--sizes script.json to read it from there. A shot naming two or more sized things in "char_in_frame" and "props" gets
scalecheck's scale sentence in its prompt, and after the run those shots go on a marking page, <out>/scale/index.html,
listed in review.tsv. The prompt alone is not enough: in the first real run a booth asked for at 1.3 times the man's
height came out about 1.1 times. Mark the page, download marks.json, then `python3 scalecheck.py check
<out>/scale-list.json marks.json`; a failing still is re-rolled with `--only <id>` after deleting it. A bad size sheet
stops the run before any Codex call.

For each shot without output yet (a rerun redoes only the missing ones):
  1. Runs CODEX_CMD (default `codex exec --skip-git-repo-check`) plus CODEX_ARGS (default `-m gpt-6-luna -c
     model_reasoning_effort="low"`) in a fresh empty folder, asking for one landscape 1536x1024 image (portrait 1024x1536 with "aspect": "portrait",
     per shot or for the file, for vertical Shorts; cropped to 9:16).
  2. Takes the newest PNG written during that call, either in the folder or under CODEX_IMAGES
     (~/.codex/generated_images). Shots run one at a time, so a new file can't belong to another shot.
  3. Re-rolls, up to --tries, when the picture isn't landscape between 1.4:1 and 1.85:1 (portrait: 0.55 to 0.72) (2 of 9 came out at an odd
     aspect in the bake-off), or when the shot is faceless and the face check says FACE (a face showed in 3 of 5 hero
     shots although the prompt said faceless).
  4. Keeps the untouched original at <out>/originals/<id>.png, byte for byte, so its provenance metadata (OpenAI's
     C2PA manifest) survives, and writes the working copy <out>/<id>.png centre-cropped to 16:9. The crop re-encodes
     the pixels and does not carry the C2PA manifest, so the copy's PNG text names the model and its original.

The face check is FACE_CHECK_CMD with {path} for the image (default: the Claude CLI reads the image and answers FACE
or NOFACE; the owner's Claude plan, no extra cost). FACE_CHECK_CMD=off skips it. An answer that is neither is kept and
listed in <out>/review.tsv for a person to look at, so an unclear judge never spends another Codex call.
Flipbook frames (longform.md, panel "frames"): a shot with "edit_from" and "change" instead of "prompt" is an edit of
another still, so its composition stays locked: {"id": "s03b", "edit_from": "s03", "change": "the flashlight is raised
to her shoulder"}. "edit_from" names an earlier shot in this run (its untouched original is used) or a PNG path;
--edit-from PNG is the base for every shot in the file that names none. The base goes to Codex as an attached image
(`--image=<path>`, one argument: a bare `-i` swallows the prompt that follows it) with "the same picture, change only ...", and the result is cropped to 16:9 like any shot. A frame must match
its base's size to flip cleanly, so an edit that comes back at the base's shape but another size is resized to it, and
one at another shape is re-rolled. After each edit the frame is compared with its base (gray, 64x36, correlation): a
frame under 0.9 is kept but listed in review.tsv as "composition may have moved". On the first real test (2026-11-25)
edits asking for one small change scored 0.71 and 0.82, with the figure moved and the whole texture redrawn, which
flipped at 4 fps reads as jitter. So edits suit a deliberate cut to a new drawing (a new pose), not flipbook in-betweens;
motion over one still is the panel's "fx" layer (longform.md). After the crop, a frame within a pixel or two (half a
percent) of its base frame is snapped to that exact size, since the crop can round one pixel differently; a bigger
difference is listed in review.tsv. Low scores are not re-rolled, since each try is a
Codex call and the test suggests a re-roll rarely holds still either.

How to redraw (picture-truth.md rule 16, 2026-11-27): a shot with "photo": path and "photo_licence" restyles that real
photo, keeping its layout and changing setting, light and style (a photo with no licence recorded stops the run). A
shot with "blockout": path paints over a to-scale blockout and then makes a second call, a style pass over its
surfaces. "style_refs": [paths] (per shot, or for the file) are attached after the picture as style references (on a plain
shot they are the only images; an edit takes none, because its base already carries the look). Every
prompt names each image by number and role, with the picture to work from first, and codex takes at most 5 images.
An edit that names a "note" is refused (a note is fixed with `longform.py repair --box` or a reframe), and so is an
edit "change" of more than one sentence. A shot with two or more sized things and neither a photo nor a blockout is
linted.

The owner's review of the first 85 stills (picture-truth.md, rule 12) is built in: every prompt says each visible hand
belongs to a person in the frame with exactly two hands, and that head, neck and limbs face and bend as a real body can;
shots are linted before any call (money, hands with no body, tipped objects that don't say where their insides are,
hidden things not drawn X-ray style), and the warnings go to review.tsv. The anatomy gate (anatomy.py; the owner: "Be
sure anatomy horror is nipped in the process") judges every shot with people for neck twist, joint bends, limb and
finger counts, proportion, floating parts and, on a faceless shot, a face. A FAIL re-rolls within --tries, up to
--anatomy-rerolls per run (a quarter of the shots, at least 3); every verdict and its reason goes to judge.tsv and onto
the still as PNG text motion-studio-anatomy; a still failing its last try is kept and listed in review.tsv.

`--check` runs every check above that comes before a Codex call and stops, with no call, no files and no --allow-codex: run
it before handing a job to longjob, so a refused shot fails in seconds instead of after the job is queued.

A Codex answer that says the plan's usage limit is hit stops the whole run at once, with no retry, the reset time if
Codex gave one, and every shot not run listed in review.tsv as "not run: Codex usage limit".

A try that is rejected (wrong shape, a face on a faceless shot, an anatomy FAIL) is kept as originals/<id>.try<n>.png.
When Codex answers that it never got an --image it was passed (it sometimes drops one), the call is sent again without
spending a try, up to twice per shot. A shot with no image logs Codex's last words. review.tsv gets a "# run" header
line per run. A shot marked "sheet": true (a character model sheet) tells the anatomy judge that separate head close-ups
are intended views.

Exit 0 when every shot has output; 3 when some did and some ran out of tries (PARTIAL); 1 when none did, or the usage
limit stopped the run (all listed in review.tsv)."""
import argparse, json, os, re, shlex, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scalecheck  # noqa: E402
import anatomy  # noqa: E402

MODEL_TAG = "chatgpt-image via codex"
# the owner's review of the first 85 stills (picture-truth.md, rule 12): a hand-only close-up gave a bent arm from the
# frame edge, and one shot a third hand
HANDS = ("Every visible hand belongs to a person whose arm and shoulder are in the frame, and each person has exactly two "
         "hands and two arms. Each person's head, neck, torso and limbs face and bend the way a real body can, with no "
         "impossible twist: the head turns no further than over the shoulder line, and elbows and knees bend only one way.")
LIKENESS = 0.9      # an edit under this correlation with its base has moved too much to flip as a frame
FACE_DEFAULT = ('claude -p "Read the image file at {path}. Does it show any human face, including a partial, distant, '
                'drawn or silhouetted face with visible features? Answer with one word on the last line: FACE or NOFACE." '
                '--allowedTools Read')


def find_new_png(since, roots):
    best = None
    for root in roots:
        for d, _, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".png"):
                    p = os.path.join(d, f)
                    try:
                        m = os.path.getmtime(p)
                    except OSError:
                        continue
                    if m >= since and (best is None or m > best[0]):
                        best = (m, p)
    return best and best[1]


def codex_cmd():
    """The codex command every ChatGPT image call uses ($CODEX_CMD and $CODEX_ARGS override it, as the tests do)."""
    # every call to codex (and to the face and anatomy judges) passes stdin=DEVNULL: `codex exec` reads stdin when it is
    # a pipe, so from an agent's background shell (stdin an open pipe) each call hung until CODEX_TIMEOUT without
    # starting a session (six timeouts, 2026-11-26); from longjob, or with < /dev/null, the same calls worked
    cmd = shlex.split(os.environ.get("CODEX_CMD", "codex exec --skip-git-repo-check"))
    return cmd + shlex.split(os.environ.get("CODEX_ARGS", '-m gpt-6-luna -c model_reasoning_effort="low"'))


def allowed(flag):
    return bool(flag) or os.environ.get("CODEX_STILLS_OK") == "1"


class UsageLimit(Exception):
    """Codex said the plan's usage limit is spent: no retry can succeed until it resets."""


LIMIT = re.compile(r"usage limit|rate.?limit|hit your limit|quota|too many requests|\b429\b", re.I)
RESETS = re.compile(r"(?:try again|resets?|available again)\s+(?:at|in|on|after)\s+[^.\n]{1,60}", re.I)


def limit_hit(*texts):
    """The limit message ("... try again at 3:14 PM") when Codex output says the usage limit is hit, else None.
    Cel test on the Mac, 2026-11-28: a limit answer still spent a second try on every shot (4 calls, 0 images)."""
    t = "\n".join(x for x in texts if x)
    if not LIMIT.search(t):
        return None
    m = RESETS.search(t)
    return m.group(0).strip() if m else next((ln.strip() for ln in t.splitlines() if LIMIT.search(ln)), "")[:160]


# Codex sometimes drops an --image it was given: its session log shows no attachment, and it answers that the sheet isn't
# available and asks for it again, exiting 0 with no picture (cel test, 2026-11-28: 6 of 24 calls with style_refs). Such
# an answer is re-sent without spending a try.
DROPPED = re.compile(r"(?:re-?)?(?:attach|upload|provide|share|send)\w*\s+(?:\w+\s+){0,3}(?:again|image|sheet|file|picture|reference)"
                     r"|(?:isn't|is not|wasn't|was not|aren't|are not|not)\s+(?:\w+\s+){0,2}(?:attached|available|included|provided|visible)"
                     r"|(?:can't|cannot|couldn't|could not|don't|do not|didn't|did not)\s+(?:see|find|access|open|locate)\s+(?:\w+\s+){0,3}"
                     r"(?:image|attachment|sheet|picture|reference|file)", re.I)
RESENDS = 2


def dropped_image(text):
    """Codex's answer when it never got the image it was passed, else None."""
    m = DROPPED.search(text or "")
    return m.group(0) if m else None


def tail(text, n=240):
    """The last words Codex said, on one line, for a log line that explains a missing image."""
    return " ".join((text or "").split())[-n:]


def edit_image(prompt, images, dest, timeout=None):
    """One ChatGPT image edit through codex: the pictures go in as --image=<path> (one argument each), the newest PNG it
    writes (in its working folder or ~/.codex/generated_images) is copied to dest. Returns None, or why it gave none."""
    work = tempfile.mkdtemp(prefix="codex-edit-")
    since = time.time() - 1
    note = ""
    try:
        r = subprocess.run(codex_cmd() + [f"--image={os.path.abspath(x)}" for x in images] + [prompt], cwd=work,
                           capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=timeout or int(os.environ.get("CODEX_TIMEOUT", 600)))
        note = "" if r.returncode == 0 else f"codex exit {r.returncode}: {(r.stderr or r.stdout).strip()[-200:]}"
        lim = limit_hit(r.stdout, r.stderr) if r.returncode else None
        if lim is not None:
            shutil.rmtree(work, True)
            raise UsageLimit(lim)
    except subprocess.TimeoutExpired:
        note = "codex timed out"
    png = find_new_png(since, [work, os.path.expanduser(os.environ.get("CODEX_IMAGES", "~/.codex/generated_images"))])
    if png:
        shutil.copyfile(png, str(dest) + ".part"); os.replace(str(dest) + ".part", dest)
    shutil.rmtree(work, True)
    return None if png else (note or "codex returned without an image")


ASPECTS = {"landscape": ((1536, 1024), (1.4, 1.85), 16 / 9), "portrait": ((1024, 1536), (0.55, 0.72), 9 / 16)}


def orient(w, h):
    return "portrait" if h > w else "landscape"


def aspect_of(shot, cfg):
    """"landscape" (default) or "portrait", per shot or for the file. Portrait asks for 1024x1536, accepts 0.55 to 0.72
    width/height and crops the still to 9:16 (cel style test for Shorts, 2026-11-28: a 9:16 crop of a landscape still
    came out 576x1024 and had to be upscaled, which blurred fine ink)."""
    return shot.get("aspect") or cfg.get("aspect") or "landscape"


def write_on_sentence(shot):
    """A shot's "write_on" (["the price tag", "the receipt"]) as one sentence. It says what each thing has (rule 14:
    "blank doors" lost their handles), so the model draws a plain writing area the engine can letter, not scribbles."""
    names = [str(n).strip() for n in (shot or {}).get("write_on") or [] if str(n).strip()]
    if not names:
        return ""
    both = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return (f"{both[0].upper() + both[1:]} {'has' if len(names) == 1 else 'each have'} a clean, empty writing area in "
            "plain paper or screen colour, turned enough toward the camera to read, with room for a short number or a few words to be added later.")


def cast_sentence(cfg, shot=None):
    """The job's "cast" and "props" (file level, {"name": "description"}) as one sentence for every prompt, so an edit
    keeps the lead and the props looking the same (Survive Year 1, 2026-11-28: 31 gap edits went out without them, and 6
    came back with a porcelain bowl, strangers in place of the lead, a blue truck or a wrong count)."""
    def items(k):
        v = cfg.get(k)
        return [(n, str(d).strip().rstrip(".")) for n, d in v.items() if str(d).strip()] if isinstance(v, dict) else []
    cast, props = items("cast"), items("props")
    if not cast and not props:
        return write_on_sentence(shot)
    parts = []
    if cast:
        parts.append("People: " + "; ".join(f"{n}: {d}" for n, d in cast) + ".")
    if props:
        parts.append("Things: " + "; ".join(f"{n}: {d}" for n, d in props) + ".")
    return ("Whoever and whatever of these is in the picture looks exactly like this, the same in every picture of this "
            "film. Do not add any that aren't in it, and do not replace them with other people or things. " + " ".join(parts)
            + (" " + write_on_sentence(shot) if write_on_sentence(shot) else ""))


def ask(shot, style, faceless, scale="", w=1536, h=1024, cast="", refs=()):
    """A shot drawn from words. Its "style_refs", when it has any, are attached as images 1..n (cel test, 2026-11-28:
    they were dropped on this path, so plain shots never saw the look they were given)."""
    lead = " ".join(f"Image {i} is a style reference only: take its medium, palette, line weight and paper texture, "
                    "never its subject, layout or objects." for i, _ in enumerate(refs, 1))
    parts = [lead, shot["prompt"].strip(), cast]
    if scale:
        parts.append(scale + " Keep these proportions exactly, even where they look unusual.")
    parts.append((shot.get("style") or style).strip())
    parts.append(f"Make exactly one image, {orient(w, h)}, {w}x{h}, and save it as image.png in the current folder.")
    parts.append("No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame.")
    parts.append(HANDS)
    if faceless:
        parts.append("No human face may be visible anywhere: show people from behind or cropped at the shoulders, with "
                     "their arms and shoulders in the frame. This is a hard rule.")
    return " ".join(p for p in parts if p)


MAX_IMAGES = 5      # codex's image tool takes at most five input images (codex-rs image-generation tool, 2026-11)


def refs_of(shot, cfg):
    """Style reference images for a shot (its own "style_refs", else the file's), attached after the picture itself."""
    return list(shot.get("style_refs", cfg.get("style_refs")) or [])


def ref_lines(first, refs, start=2):
    """Name every attached image by number and role: the picture to work from is image 1 (it alone gets the model's
    extra fidelity), style references follow. The OpenAI image guide: identify each input by number and purpose."""
    parts = [f"Image 1 is {first}."]
    for i, _ in enumerate(refs, start):
        parts.append(f"Image {i} is a style reference only: take its medium, palette, line weight and paper texture, "
                     "never its subject, layout or objects.")
    return " ".join(parts)


def ask_photo(shot, style, faceless, scale="", refs=(), w=1536, h=1024, cast=""):
    """Rule 16.1: restyle a real photo. Its layout, camera and the size of everything in it are kept; the setting, light
    and style are changed as the prompt says."""
    parts = [ref_lines("a reference photo: keep its layout, camera angle and height, and the position, size and "
                       "proportions of every object and person in it exactly", refs),
             "Redraw image 1 as an illustration, changing only the setting, light and style: " + shot["prompt"].strip(), cast]
    if scale:
        parts.append(scale + " Keep these proportions exactly.")
    parts.append((shot.get("style") or style).strip())
    parts.append(f"Make exactly one image, {orient(w, h)}, {w}x{h}, and save it as image.png in the current folder.")
    parts.append("No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame.")
    parts.append(HANDS)
    if faceless:
        parts.append("No human face may be visible anywhere: show people from behind or cropped at the shoulders, with "
                     "their arms and shoulders in the frame. This is a hard rule.")
    return " ".join(p for p in parts if p)


def ask_blockout(shot, style, faceless, scale="", refs=(), w=1536, h=1024, cast=""):
    """Rule 16.3, pass 1: paint over a to-scale blockout. The blockout is the source of composition and sizes only."""
    parts = [ref_lines("a rough to-scale blockout: it is the source of the composition, camera and the size and position "
                       "of every object only; its flat grey shapes and colours are not the look", refs),
             "Paint the finished picture over image 1, keeping every object exactly where it is and exactly its size: "
             + shot["prompt"].strip(), cast]
    if scale:
        parts.append(scale + " Keep these proportions exactly.")
    parts.append((shot.get("style") or style).strip())
    parts.append(f"Make exactly one image, {orient(w, h)}, {w}x{h}, and save it as image.png in the current folder.")
    parts.append("No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame.")
    parts.append(HANDS)
    if faceless:
        parts.append("No human face may be visible anywhere: show people from behind or cropped at the shoulders. This is a hard rule.")
    return " ".join(p for p in parts if p)


def ask_style_pass(style, refs=(), w=1536, h=1024):
    """Rule 16.3, pass 2: the painted-over blockout drifts in style, so a second edit restyles its surfaces only."""
    parts = [ref_lines("the picture to restyle: keep its composition, every object's position, size and silhouette, and "
                       "every person's pose exactly", refs),
             "Repaint only the surfaces of image 1 in this style: " + style.strip(),
             f"Make exactly one image, {orient(w, h)}, {w}x{h}, and save it as image.png in the current folder.",
             "No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame."]
    return " ".join(parts)


def shot_problems(shots, cfg):
    """Rule 16 checks before any Codex call: a photo needs its licence recorded, a note's fix is a box repair, an edit
    makes one change, and no call attaches more images than codex takes."""
    out = []
    for x in shots:
        i = x["id"]
        for k in ("photo", "blockout"):
            if x.get(k) and not os.path.isfile(x[k]):
                out.append(f"{i}: {k} {x[k]} not found")
        if x.get("photo") and not str(x.get("photo_licence") or "").strip():
            out.append(f'{i}: a reference photo needs "photo_licence" ("own" for his own photo, else the licence and '
                       "source), recorded before it is used")
        if x.get("photo") and x.get("blockout"):
            out.append(f"{i}: a photo or a blockout, not both")
        if x.get("change") and x.get("note"):
            out.append(f"{i}: fix a note with a box repair (longform.py repair --box) or a reframe, never a whole-picture "
                       "edit (rule 16.2): the whole frame is redrawn and other parts change")
        if x.get("change") and len([t for t in re.split(r"(?<=[.;!?])\s+", x["change"].strip()) if t.strip()]) > 1:
            out.append(f"{i}: one change per edit (rule 16.2); split it into one shot per change")
        if aspect_of(x, cfg) not in ASPECTS:
            out.append(f'{i}: "aspect" is "{aspect_of(x, cfg)}"; use "landscape" or "portrait"')
        n = (1 if x.get("photo") or x.get("blockout") else 0) + len(refs_of(x, cfg))   # a plain shot sends refs only
        if n > MAX_IMAGES:
            out.append(f"{i}: {n} images attached; codex takes at most {MAX_IMAGES}")
    return out


def ask_edit(shot, faceless, w=1536, h=1024, cast=""):
    parts = ["Edit the attached picture. Keep it the same picture: the same composition, camera angle, framing, people, "
             "objects, colours, line work and light, everything in the same place. Change only this: "
             + shot["change"].strip().rstrip(".") + "."]
    if cast:
        parts.append(cast)
    parts.append(f"Make exactly one image, {orient(w, h)}, {w}x{h}, the same size as the attached picture, and save it as "
                 "image.png in the current folder.")
    parts.append("No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame.")
    parts.append(HANDS)
    if faceless:
        parts.append("No human face may be visible anywhere: show people from behind or cropped at the shoulders, with "
                     "their arms and shoulders in the frame. This is a hard rule.")
    return " ".join(parts)


def likeness(a, b):
    """Correlation of two pictures' gray thumbnails (64x36): about 1 when the composition held, low when it moved."""
    import numpy as np
    from PIL import Image
    g = [np.asarray(Image.open(p).convert("L").resize((64, 36)), dtype=float).ravel() for p in (a, b)]
    g = [x - x.mean() for x in g]
    den = float(np.sqrt((g[0] ** 2).sum() * (g[1] ** 2).sum()))
    return float((g[0] * g[1]).sum() / den) if den else 0.0


SNAP = 0.005     # a frame within half a percent of its base frame's size (a pixel or two) is snapped to it exactly


def frame_of(base, out):
    """The still a flipbook frame flips against: an earlier shot's cropped still for one of our originals, else the base
    PNG itself (a path given as the base is used as a frame as it is)."""
    o = os.path.abspath(base)
    if os.path.dirname(o) == os.path.abspath(os.path.join(out, "originals")):
        f = os.path.join(out, os.path.basename(o))
        if os.path.isfile(f):
            return f
    return base


def snap_to(dst, frame):
    """Give dst exactly frame's size when they differ by a pixel or two, keeping its PNG text; a note for review.tsv.
    Episode thread, 2026-11-27: edits came back 1671x940 against a 1672x940 base, and timeline stopped on "flipbook
    frames differ in size". A bigger difference is left alone and listed, since stretching it would distort the frame."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    with Image.open(frame) as f:
        fw, fh = f.size
    im = Image.open(dst); im.load()
    w, h = im.size
    if (w, h) == (fw, fh):
        return None
    if abs(w - fw) > max(2, SNAP * fw) or abs(h - fh) > max(2, SNAP * fh):
        return f"{w}x{h}, not its base frame's {fw}x{fh}: flipbook frames must match; redo or resize by hand"
    info = PngInfo()
    for k, v in (im.text or {}).items():
        info.add_text(k, v)
    tmp = dst + ".part"
    im.convert("RGB").resize((fw, fh), Image.LANCZOS).save(tmp, format="PNG", pnginfo=info)
    os.replace(tmp, dst)
    return f"snapped {w}x{h} to its base frame's {fw}x{fh}"


def crop_16x9(src, dst, original_rel):
    crop_to(src, dst, original_rel, 16 / 9)


def keep_try(path, out, sid, t, move=False):
    """A rejected try kept as originals/<id>.try<t>.png for review, not overwritten by the next try (cel test,
    2026-11-28: try 2 replaced try 1 and the rejected picture was lost)."""
    if not os.path.exists(path):
        return
    dst = os.path.join(out, "originals", f"{sid}.try{t}.png")
    (shutil.move if move else shutil.copyfile)(path, dst)


def edit_base(shot, out, default):
    """The base picture for an edit: an earlier shot's untouched original, a PNG path, or --edit-from."""
    ref = shot.get("edit_from") or default
    if not ref:
        return None
    cand = [os.path.join(out, "originals", f"{ref}.png"), ref, os.path.join(out, ref)]
    return next((c for c in cand if os.path.isfile(c)), "")


def face_verdict(path, cmd):
    if cmd == "off":
        return "NOFACE"
    try:
        r = subprocess.run(cmd.replace("{path}", shlex.quote(path)), shell=True, capture_output=True, stdin=subprocess.DEVNULL, text=True,
                           timeout=int(os.environ.get("FACE_CHECK_TIMEOUT", 180)))
    except subprocess.TimeoutExpired:
        return "UNCLEAR"
    lines = [ln.strip().strip(".*").upper() for ln in r.stdout.strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    return last if last in ("FACE", "NOFACE") else "UNCLEAR"


def stamp(path, key, value):
    """Add one PNG text entry to a still, keeping the ones it has."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    im = Image.open(path); im.load()
    info = PngInfo()
    for k, v in (im.text or {}).items():
        if k != key:
            info.add_text(k, v)
    info.add_text(key, value)
    im.save(path + ".part", format="PNG", pnginfo=info); os.replace(path + ".part", path)


def crop_to(src, dst, original_rel, ratio=16 / 9):
    """Centre-crop to width/height `ratio` (16:9 for landscape, 9:16 for portrait), with the model and original tagged."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    im = Image.open(src); im.load()
    W, H = im.size
    if W / H > ratio:
        nw = round(H * ratio); box = ((W - nw) // 2, 0, (W - nw) // 2 + nw, H)
    else:
        nh = round(W / ratio); box = (0, (H - nh) // 2, W, (H - nh) // 2 + nh)
    info = PngInfo()
    info.add_text("motion-studio-model", MODEL_TAG)
    info.add_text("motion-studio-original", original_rel)
    tmp = dst + ".part"
    im.convert("RGB").crop(box).save(tmp, format="PNG", pnginfo=info)
    os.replace(tmp, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shots"); ap.add_argument("out")
    ap.add_argument("--allow-codex", action="store_true")
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--sizes", help="read the size sheet from this script.json (its \"sizes\") instead of shots.json")
    ap.add_argument("--anatomy-rerolls", type=int, help="most re-rolls the anatomy judge may ask for in this run (default: a quarter of the shots, at least 3)")
    ap.add_argument("--check", action="store_true", help="run every check made before a Codex call (size sheet, refused words, "
                    "rule 16, lint) and stop: no call, no files, no --allow-codex needed. Run it before handing a job to longjob")
    ap.add_argument("--edit-from", help="base PNG for every shot with a \"change\" and no \"edit_from\" (flipbook frames)")
    a = ap.parse_args()
    if not a.check and not allowed(a.allow_codex):
        raise SystemExit("codex_stills.py spends the owner's Codex allowance: pass --allow-codex once he has said yes")
    cfg = json.load(open(a.shots))
    shots = [s for s in cfg["shots"] if not a.only or s["id"] in a.only]
    sizes = (json.load(open(a.sizes)).get("sizes") if a.sizes else cfg.get("sizes")) or {}
    bad = scalecheck.sheet_problems(sizes) + [
        f'{s["id"]}: props with no entry in "sizes": {", ".join(k for k in s.get("props") or [] if k not in sizes)}'
        for s in shots if any(k not in sizes for k in s.get("props") or [])]
    bad += [f'"{k}" at the file level is {{"name": "what they look like"}}, not {type(cfg[k]).__name__}'
            for k in ("cast", "props") if k in cfg and not isinstance(cfg[k], dict)]
    bad += [f'{s["id"]}: "write_on" is a list of the things the engine will letter, e.g. ["the price tag"]'
            for s in shots if "write_on" in s and not (isinstance(s["write_on"], list) and all(isinstance(x, str) for x in s["write_on"]))]
    if bad and not a.check:
        raise SystemExit("size sheet problems, nothing run:\n  " + "\n  ".join(bad))
    if not a.check:
        os.makedirs(os.path.join(a.out, "originals"), exist_ok=True)
    # what-gets-drawn lint (money, hands with no body, tipped objects, hidden things; picture-truth.md rule 12) before
    # any Codex call. It warns, never stops: the words are a heuristic, and a person decides
    import panelqa
    todo = [x for x in shots if not os.path.exists(os.path.join(a.out, f"{x['id']}.png"))]
    pre = panelqa.lint([dict(x, id=x["id"], prompt=x.get("prompt") or x.get("change") or "") for x in todo], route="codex")
    for i, ws in pre:
        for wtxt in ws:
            print(f"LINT {i}: {wtxt}")
    stop = [f'{x["id"]}: "{m}" {why}' for x in todo for m, why in panelqa.refused(x.get("prompt") or x.get("change") or "")]
    stop += shot_problems(todo, cfg)
    # rule 16.1/16.3: a picture where sizes matter starts from a photo or a to-scale blockout, since the prompt alone
    # missed the size in the first real run; a warning, as the size sheet may list things that don't conflict
    for x in todo:
        if not (x.get("photo") or x.get("blockout") or x.get("change")) and len(scalecheck.sized_items(x, sizes)) >= 2:
            pre.append((x["id"], ["scale shot with no reference photo or blockout: start from one (rule 16)"]))
            print(f"LINT {x['id']}: scale shot with no reference photo or blockout: start from one (rule 16)")
    if a.check:
        # a dry run (2026-11-27: the one-change rule refused a 39-shot job only once it was queued under longjob)
        for x in bad + stop:
            print(f"STOP {x}")
        print(f"{len(todo)} shot(s) to draw: {len(bad) + len(stop)} problem(s) that stop the run, "
              f"{sum(len(ws) for _, ws in pre)} lint warning(s)")
        raise SystemExit(1 if bad or stop else 0)
    if stop:
        raise SystemExit("refused prompt words, nothing run:\n  " + "\n  ".join(stop))
    cmd = codex_cmd()
    images = os.path.expanduser(os.environ.get("CODEX_IMAGES", "~/.codex/generated_images"))
    face_cmd = os.environ.get("FACE_CHECK_CMD", FACE_DEFAULT)
    budget = a.anatomy_rerolls if a.anatomy_rerolls is not None else max(3, -(-len(shots) // 4))   # re-rolls the anatomy
    # judge may spend in this run: a quarter of the shots (at least 3), so a judge having a bad day can't run up the bill
    timeout = int(os.environ.get("CODEX_TIMEOUT", 600))
    review, calls, failed = [], 0, []
    limit = None
    try:
        for s in shots:
            dst = os.path.join(a.out, f"{s['id']}.png")
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                print(f"have {s['id']}"); continue
            faceless = s.get("faceless", cfg.get("faceless", False))
            base = edit_base(s, a.out, a.edit_from) if s.get("change") else None
            aspect = aspect_of(s, cfg)
            aw, (lo, hi), ratio = ASPECTS[aspect]
            if base == "":
                failed.append(s["id"]); review.append((s["id"], f"edit base {s.get('edit_from') or a.edit_from} not found (run its shot first)"))
                print(f"{s['id']}: no edit base"); continue
            if base:
                from PIL import Image
                with Image.open(base) as im:
                    bw, bh = im.size
                aspect = orient(bw, bh)                 # an edit keeps its base's orientation
                aw, (lo, hi), ratio = ASPECTS[aspect]
                # --image=<path> as one argument: codex's -i takes any number of files, so a prompt after "-i <path>" was
                # read as a second image and codex exited with "No prompt provided via stdin" (22 of 22 edits, 2026-11-25)
                prompt, extra = ask_edit(s, faceless, bw, bh, cast_sentence(cfg, s)), [f"--image={os.path.abspath(base)}"]
            elif s.get("photo"):
                refs = refs_of(s, cfg)
                prompt = ask_photo(s, cfg.get("style", ""), faceless, scalecheck.scale_sentence(s, sizes), refs, *aw, cast_sentence(cfg, s))
                extra = [f"--image={os.path.abspath(p)}" for p in [s["photo"]] + refs]
            elif s.get("blockout"):
                refs = refs_of(s, cfg)
                prompt = ask_blockout(s, cfg.get("style", ""), faceless, scalecheck.scale_sentence(s, sizes), refs, *aw, cast_sentence(cfg, s))
                extra = [f"--image={os.path.abspath(p)}" for p in [s["blockout"]] + refs]
            else:
                refs = refs_of(s, cfg)
                prompt = ask(s, cfg.get("style", ""), faceless, scalecheck.scale_sentence(s, sizes), *aw, cast_sentence(cfg, s), refs)
                extra = [f"--image={os.path.abspath(p)}" for p in refs]
            done = False
            last_why = ""
            resends = 0
            t = 0
            while t < a.tries:
                t += 1
                work = tempfile.mkdtemp(prefix="codex-still-")
                since = time.time() - 1
                calls += 1; t0 = time.time()
                r = None
                try:
                    r = subprocess.run(cmd + extra + [prompt], cwd=work, capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=timeout)
                    note = "" if r.returncode == 0 else f"codex exit {r.returncode}: {tail(r.stderr or r.stdout, 200)}"
                    lim = limit_hit(r.stdout, r.stderr) if r.returncode else None
                    if lim is not None:
                        shutil.rmtree(work, True)
                        raise UsageLimit(lim)
                except subprocess.TimeoutExpired:
                    note = f"codex timed out after {timeout} s"
                png = find_new_png(since, [work, images])
                if not png:
                    said = (r.stdout + "\n" + r.stderr) if r is not None else ""
                    lim = limit_hit(said) if r is not None else None
                    if lim is not None:                  # the limit message can come with exit 0 too
                        shutil.rmtree(work, True)
                        raise UsageLimit(lim)
                    drop = dropped_image(said) if any(x.startswith("--image=") for x in extra) else None
                    shutil.rmtree(work, True)
                    if drop and resends < RESENDS:
                        resends += 1; t -= 1             # Codex never got the picture: not the prompt's fault, not a try
                        print(f"{s['id']} try {t + 1}: Codex didn't get the image (\"{drop}\"); sending again ({resends} of {RESENDS})")
                        continue
                    why = note or (f"Codex said: {tail(said)}" if said.strip() else "codex returned without one")
                    print(f"{s['id']} try {t}: no image ({why})")
                    last_why = why
                    continue
                if s.get("blockout"):         # pass 2, the style pass over the painted blockout (rule 16.3)
                    styled = os.path.join(work, "styled.png")
                    calls += 1
                    why = edit_image(ask_style_pass(s.get("style") or cfg.get("style", ""), refs_of(s, cfg), *aw),
                                     [png] + refs_of(s, cfg), styled, timeout)
                    if why:
                        print(f"{s['id']} try {t}: style pass gave no image ({why})"); shutil.rmtree(work, True); continue
                    png = styled
                from PIL import Image, UnidentifiedImageError
                try:                          # a cut-off or corrupt file is a failed try, not the end of the run
                    with Image.open(png) as im:
                        W, H = im.size; im.load()
                except (UnidentifiedImageError, OSError, SyntaxError) as e:
                    last_why = f"Codex wrote an unreadable image ({type(e).__name__})"
                    print(f"{s['id']} try {t}: {last_why}; re-rolling"); shutil.rmtree(work, True); continue
                if not lo <= W / H <= hi:
                    keep_try(png, a.out, s["id"], t)
                    print(f"{s['id']} try {t}: {W}x{H} is not {aspect} ({lo:g} to {hi:g} wide per high); re-rolling"); shutil.rmtree(work, True); continue
                if base and (W, H) != (bw, bh) and abs(W / H - bw / bh) > 0.01:
                    keep_try(png, a.out, s["id"], t)
                    print(f"{s['id']} try {t}: {W}x{H} is not its base's shape ({bw}x{bh}); re-rolling"); shutil.rmtree(work, True); continue
                orig = os.path.join(a.out, "originals", f"{s['id']}.png")
                if os.path.exists(orig + ".fit.png"):
                    os.remove(orig + ".fit.png")
                shutil.copyfile(png, orig + ".part"); os.replace(orig + ".part", orig)
                if base and (W, H) != (bw, bh):      # the base's shape at another size: the frame is resized to flip cleanly
                    with Image.open(orig) as im:
                        im.convert("RGB").resize((bw, bh), Image.LANCZOS).save(orig + ".fit.png")
                    review.append((s["id"], f"resized {W}x{H} to its base's {bw}x{bh}; originals/{s['id']}.png.fit.png was used for the crop"))
                shutil.rmtree(work, True)
                v = face_verdict(orig, face_cmd) if faceless else "NOFACE"
                if v == "FACE":
                    keep_try(orig, a.out, s["id"], t, move=True)
                    print(f"{s['id']} try {t}: face check found a face; re-rolling"); continue
                src = orig + ".fit.png" if os.path.exists(orig + ".fit.png") else orig
                crop_to(src, dst, f"originals/{s['id']}.png", ratio)
                if base:
                    snapped = snap_to(dst, frame_of(base, a.out))
                    if snapped:
                        review.append((s["id"], snapped))
                    k = likeness(base, src)
                    print(f"{s['id']}: likeness to its base {k:.2f}")
                    if k < LIKENESS:
                        review.append((s["id"], f"composition may have moved from its base (likeness {k:.2f}): look before using it as a frame"))
                if v == "UNCLEAR":
                    review.append((s["id"], "face check unclear: look at it"))
                # the anatomy gate (anatomy.py): a FAIL re-rolls within --tries while the run's re-roll budget lasts; every
                # verdict is logged to judge.tsv and stamped on the still, and one still failing on its last try is kept and
                # listed for a person
                if anatomy.needs_check(s):
                    av, why = anatomy.judge(src, faceless, sheet=bool(s.get("sheet")))
                    last = t >= a.tries or budget <= 0
                    anatomy.log(a.out, s["id"], t, av if not (av == "FAIL" and last) else "KEPT", why, f"originals/{s['id']}.png")
                    stamp(dst, "motion-studio-anatomy", f"{av}: {why}" if why else av)
                    if av == "FAIL" and not last:
                        budget -= 1
                        print(f"{s['id']} try {t}: anatomy FAIL ({why}); re-rolling ({budget} anatomy re-rolls left this run)")
                        keep_try(orig, a.out, s["id"], t, move=True)
                        for x in (orig + ".fit.png", dst):
                            if os.path.exists(x): os.remove(x)
                        continue
                    if av == "FAIL":
                        review.append((s["id"], f"anatomy FAIL on its last try ({why}); kept for you to judge"
                                       + ("" if t >= a.tries else "; the run's anatomy re-roll budget was spent")))
                    elif av == "UNCLEAR":
                        review.append((s["id"], f"anatomy judge unclear ({why}): look at the people"))
                print(f"{s['id']}: {W}x{H} -> {'9:16' if ratio < 1 else '16:9'} in {time.time() - t0:.0f} s (try {t})")
                done = True; break
            if not done:
                failed.append(s["id"])
                review.append((s["id"], f"no usable image in {a.tries} tries" + (f" (last: {last_why})" if last_why else "")
                               + ("; rejected tries kept as originals/" + s["id"] + ".try<n>.png" if any(
                                   os.path.exists(os.path.join(a.out, "originals", f"{s['id']}.try{k}.png")) for k in range(1, a.tries + 1)) else "")))
    except UsageLimit as e:
        limit = str(e) or "no reset time given"
        left = [x["id"] for x in shots if not os.path.exists(os.path.join(a.out, f"{x['id']}.png")) and x["id"] not in failed]
        failed += left
        review += [(i, f"not run: Codex usage limit ({limit})") for i in left]
    to_mark = [s for s in cfg["shots"] if len(scalecheck.sized_items(s, sizes)) >= 2
               and os.path.exists(os.path.join(a.out, f"{s['id']}.png"))]
    if to_mark:
        panels = [{"image": f"{s['id']}.png", "beats": [s["id"]], "char_in_frame": s.get("char_in_frame") or [],
                   "props": s.get("props") or []} for s in to_mark]
        with open(os.path.join(a.out, "scale-list.json"), "w") as f:
            json.dump({"sizes": sizes, "panels": panels}, f, indent=1)
        page, _ = scalecheck.page(Path(a.out).resolve() / "scale", Path(a.out).resolve(), panels, sizes, "codex stills")
        review += [(s["id"], f"scale: mark it on {page}, then scalecheck.py check scale-list.json marks.json")
                   for s in to_mark if not a.only or s["id"] in a.only]
    with open(os.path.join(a.out, "usage.log"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tcodex_stills\t{calls} codex calls\t{len(shots)} shots\t{len(failed)} failed\n")
    review = [(i, "lint: " + wtxt) for i, ws in pre for wtxt in ws] + review
    with open(os.path.join(a.out, "review.tsv"), "a") as f:
        # one header per run (cel test, 2026-11-28: old "no usable image" lines read as current)
        f.write(f"# run {time.strftime('%Y-%m-%d %H:%M:%S')}\t{len(shots)} shots\t{len(failed)} without an image\t{calls} codex calls\n")
        f.writelines(f"{i}\t{why}\n" for i, why in review)
    print(f"{calls} codex calls for {len(shots)} shots; {len(failed)} without an image"
          + (f"; see {os.path.join(a.out, 'review.tsv')}" if review else ""))
    if limit is not None:
        raise SystemExit(f"stopped: Codex usage limit hit ({limit}); nothing was retried. Run again after it resets: shots "
                         "with a still are skipped.")    # exit 1, message on stderr
    # 3: some shots landed and some didn't, so a batch runner can say PARTIAL rather than FAILED (cel test, 2026-11-28:
    # 15 of 16 landed and longjob marked the batch failed); 1: none landed
    landed = sum(os.path.exists(os.path.join(a.out, f"{x['id']}.png")) for x in shots)
    if failed:
        print(f"PARTIAL: {landed} of {len(shots)} shots have a still" if landed else "FAILED: no shot has a still")
    sys.exit(0 if not failed else 3 if landed else 1)


if __name__ == "__main__":
    main()
