# Optional second copy of every output on Google Drive, for attended runs (run_colab.sh runs this when
# COLAB_DRIVE_DIR is set). The Mac puller stays the main copy: mounting Drive asks for a consent click in a browser
# for each new runtime and grants access to the whole Drive, so an unattended run would stall here.
# A background thread in the kernel copies each whole file from /content/out to MyDrive/<dir>/ (through a temporary
# name, then a rename), checks it against MANIFEST.tsv when there is one, and copies RENDER_DONE last.
import os, shutil, threading, time
OUT = '/content/out'
DIR = open('/content/drive_dir').read().strip().strip('/')
if not os.path.isdir('/content/drive/MyDrive'):
    try:
        from google.colab import drive
        drive.mount('/content/drive')
    except Exception as e:
        print(f'DRIVE not mounted ({type(e).__name__}: {str(e)[:200]}); outputs come only through the Mac puller', flush=True)
if os.path.isdir('/content/drive/MyDrive'):
    dest = os.path.join('/content/drive/MyDrive', DIR)
    os.makedirs(dest, exist_ok=True)

    def listed():
        m = os.path.join(OUT, 'MANIFEST.tsv')
        if not os.path.exists(m):
            return None
        return {l.split('\t')[0]: int(l.split('\t')[1]) for l in open(m) if l.count('\t') == 2}

    def copy(n):
        tmp = os.path.join(dest, f'.{n}.tmp')
        shutil.copyfile(os.path.join(OUT, n), tmp)
        os.replace(tmp, os.path.join(dest, n))

    def mirror():
        while True:
            try:
                done = os.path.exists(os.path.join(OUT, 'RENDER_DONE'))   # before listing: every file is in by then
                man = listed()
                names = [n for n in os.listdir(OUT) if not n.startswith('.') and not n.endswith(('.part', '.tmp'))
                         and n != 'RENDER_DONE' and os.path.isfile(os.path.join(OUT, n))]
                for n in names:
                    if man is not None and n != 'MANIFEST.tsv' and n not in man:
                        continue
                    size = os.path.getsize(os.path.join(OUT, n))
                    d = os.path.join(dest, n)
                    if n == 'MANIFEST.tsv' or not os.path.exists(d) or os.path.getsize(d) != size:
                        copy(n)
                if done:
                    copy('RENDER_DONE')
                    print(f'DRIVE copy complete: {dest}', flush=True)
                    return
            except Exception as e:             # Drive's FUSE mount drops now and then; the next pass tries again
                print(f'DRIVE copy error ({type(e).__name__}: {str(e)[:200]})', flush=True)
            time.sleep(30)

    threading.Thread(target=mirror, daemon=True).start()
    print(f'DRIVE mirroring /content/out to {dest}', flush=True)
