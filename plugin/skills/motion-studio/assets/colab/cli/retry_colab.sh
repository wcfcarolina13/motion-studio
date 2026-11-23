#!/bin/bash
# Colab often answers "Service Unavailable" for every GPU, or hands over a runtime that drops at once.
# Retries run_colab.sh every 20 min, up to 9 tries (about 3 h). Same arguments and env as run_colab.sh.
# Under longjob, scripts are snapshotted and their names rewritten, so this resolves its sibling by absolute path.
HERE=$(cd "$(dirname "$0")" && pwd)
R=${RUNNER:-$HERE/run_colab.sh}
for i in 1 2 3 4 5 6 7 8 9; do
  echo "PHASE try $i"
  /bin/bash "$R" "$@" && exit 0
  [ $i -lt 9 ] && sleep 1200
done
exit 1
