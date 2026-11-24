# Colab rules: every job, every stream

Colab runtimes drop without warning, GPUs are refused for hours, and units burn while a runtime is held. These rules make a run lose at most the file in progress and never burn units unattended. `assets/colab/cli/run_colab.sh` follows them by default; a new remote job should run through it (swap `SETUP=` and `RENDER=`) rather than a script of its own. A fuller rulebook with sources is kept on the owner's machine; this page is the part the engine enforces.

## Outputs survive a dropped session

- **Whole files only.** A remote script writes each output somewhere else (`/content/tmp`), then `os.replace()`s it into `/content/out`, and lists it in `/content/out/MANIFEST.tsv` (name, size, sha256). `publish()` in `remote_render.py` does all three; copy it. `colab ls` gives no size or date, so a file written in place could be pulled half-written.
- **Pulled as they finish.** `pull_live.sh` starts before setup and lists `/content/out` every 30 s (the listing goes through the contents API, so it answers while the kernel is busy). Each new file is downloaded beside its target, checked against the manifest, and renamed into place only when it matches; a failed or mismatched download is deleted and tried again next pass. On 2026-11-24 a run lost 64 of 76 finished stills because downloads were left until the end.
- **Done marker last.** The remote script writes `/content/out/RENDER_DONE` after its last output (and prints `RENDER DONE`). The puller stops after one more pass once it sees it.
- **Resume, don't redo.** `retry_colab.sh` sends only the jobs in `jobs.json` that have no output yet (a non-empty `<id>.*` in the output folder), so a rerun after any failure pays only for what is missing. A remote script also skips an output that already exists.
- **Belt and braces.** At the end, anything the render named (`OUTFILE <name>`) that is still missing is fetched once more, with the same check. Anything still missing is listed as `MISSING` and the run exits 2.

## A run never burns units unattended

- **Every call has a local time limit** (`timeout`), because a dropped connection can leave a CLI call waiting forever. The render exec has no remote time limit (`--timeout 0`, which is an idle limit, not a total), and the stall watch takes its place.
- **Stall watch.** Activity is a line of render output or a new file pulled. After `STALL_ALERT` seconds without either (default 900) the run alerts; after `STALL_STOP` (default 1800) it gives the render up, makes the last pull and stops the session. Render scripts print progress at least every few minutes (Wan prints every step). No heartbeat thread: it would keep the watch quiet while the GPU hangs.
- **Exec returned early.** "Connection was lost" does not stop the kernel. Unless the output shows a crash, the run keeps the session and keeps pulling under the same stall watch until `RENDER_DONE`. Never resubmit a render after a lost connection without checking what the runtime already made: the first one may still be running.
- **Alerts.** `COLAB_ALERT` names a command run with the message (a desktop notification, a message to a thread). Unset, alerts go to the console and `usage.log` only.
- **Unit budget.** The balance is logged at the start and end of every run (`usage.log`), with the units spent. During the render it is read every 5 min; past `UNIT_BUDGET` units for the run (default 20) the render is given up. The balance moves in steps, so this is a backstop. Top-ups need the owner's word with the cost first.
- **Teardown.** The session is stopped on every exit path, by name (`colab stop -s <name>`), never all sessions: another stream may be running one. One name per concurrent run (`COLAB_SESSION`).
- **Hidden runtimes.** A failed `colab new` can leave a runtime assigned and billing with no local name (`[?]` in `colab sessions`). The run checks after each failed request and alerts; it can't stop one by name, so stop it in Colab's Manage sessions.

## Getting a GPU

- `COLAB_GPUS` sets the order (default `A100 L4`). G4 is never offered to the CLI. T4 (16 GB, no bf16) can't hold Wan 14B; list it only for jobs that fit (`COLAB_GPUS="L4 T4"`). Since about 2026-11-23 the CLI often answers 503 for A100 and L4; `retry_colab.sh` waits that out (every 20 min, 9 tries).
- **Smoke test before uploading anything:** `nvidia-smi`, a tiny CUDA operation and free disk, logged with `colab version` and `colab usage`. A runtime that fails it is treated like no GPU (exit 1, retried).

## Models

- Models download on the runtime, never through the Mac. Setup retries each download with backoff and keeps finished files in the runtime's cache; a new runtime downloads again. Hugging Face's Xet CDN is off (`HF_HUB_DISABLE_XET=1`) after a 429 killed a run. With a read-only Hugging Face token (owner OK'd 2026-11-24), run_colab.sh uploads it from `HF_TOKEN_FILE` (default `~/.config/motion-studio/hf_read_token`, mode 600 on the Mac) to `/content/.hf_token`, and setup turns Xet back on with `HF_XET_HIGH_PERFORMANCE=1`; after two failed tries it falls back to plain HTTP. No token file means anonymous downloads with Xet off. The token must be read-only (fine-grained, no write scope), is never printed, and never goes in chat, memory, logs or the repo. Trade-off: it sits on the runtime's disk until the runtime stops, so a shared or leaked notebook could expose it; read-only scope limits that to downloading public and gated models as the owner.

## Drive (opt-in, attended runs only)

`COLAB_DRIVE_DIR=<folder under My Drive>` adds a second copy: `remote_drive.py` mounts Drive and a thread in the kernel copies each whole file there (temporary name, then rename, checked against the manifest), `RENDER_DONE` last; the run gives it up to 5 min to catch up before stopping. The Mac puller stays the main copy because mounting Drive asks for a consent click in a browser on each new runtime and grants the whole Drive, so an unattended run would sit at the prompt. If the mount fails, the run alerts and carries on with the puller. The Drive layer is untested on a real runtime as of 2026-11-24.

## Content

- Colab forbids deepfakes. Any lip-sync, face-swap or voice job that involves a real person's face or voice is checked with the owner first, and never runs on Colab without that. Original characters are fine.
- The account holder accepts Colab's terms and signs the CLI in themselves (see longform.md).
