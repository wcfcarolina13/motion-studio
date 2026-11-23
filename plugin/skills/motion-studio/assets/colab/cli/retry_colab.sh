#!/bin/bash
# Colab often answers "Service Unavailable" for every GPU, or hands over a runtime that drops at once.
# Retries run_colab.sh every 20 min, up to 9 tries (about 3 h). Same arguments and env as run_colab.sh.
# A job runner may snapshot scripts under other names, so this resolves its sibling by absolute path.
HERE=$(cd "$(dirname "$0")" && pwd)
R=${RUNNER:-$HERE/run_colab.sh}
for i in 1 2 3 4 5 6 7 8 9; do
  echo "PHASE try $i"
  /bin/bash "$R" "$@"; rc=$?
  [ $rc -ne 1 ] && exit $rc        # 0 done, 2 render didn't finish: only 1 (no GPU, dead runtime) is worth retrying
  [ $i -lt 9 ] && sleep 1200
done
exit 1
