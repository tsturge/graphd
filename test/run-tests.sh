#!/bin/bash
#
# run-tests.sh -- run the test/unit cases in parallel against a Make build.
#
#   test/run-tests.sh            run every case
#   test/run-tests.sh anchor sort  run only those cases
#
# Environment:
#   BUILDDIR  directory holding bin/graphd, bin/gld, bin/gpush (default: out)
#   JOBS      number of cases to run at once (default: 8)
#
# The cases write databases and output files next to themselves, so they
# run in a scratch copy of test/unit under $BUILDDIR/test-unit; that copy
# is left behind for inspecting failures (X.out, X.err, X.log per case).
#
# Cases that start a long-lived graphd server (fixed ports, pid files,
# wall-clock waits) run one at a time after the parallel batch.

set -u

top=$(cd "$(dirname "$0")/.." && pwd)
builddir=${BUILDDIR:-$top/out}
case $builddir in /*) ;; *) builddir=$top/$builddir ;; esac
jobs=${JOBS:-8}

export GRAPHD=$builddir/bin/graphd
export GLD=$builddir/bin/gld
export GPUSH=$builddir/bin/gpush

for b in "$GRAPHD" "$GLD" "$GPUSH"; do
  if [ ! -x "$b" ]; then
    echo "$0: $b not found; run make tools first" >&2
    exit 2
  fi
done

serial="dateline-5 doubletrouble gpush logrotate start-twice"

work=$builddir/test-unit
rm -rf "$work"
mkdir -p "$work/test"
cp -R "$top/test/unit" "$work/test/unit"
cd "$work"

if [ $# -gt 0 ]; then
  cases="$*"
else
  cases=$(cd test/unit && ls *.sh | sed 's/\.sh$//' | grep -vx test)
fi

parallel=
sequential=
for c in $cases; do
  if [ ! -x "test/unit/$c.sh" ]; then
    echo "$0: no test case $c" >&2
    exit 2
  fi
  case " $serial " in
    *" $c "*) sequential="$sequential $c" ;;
    *) parallel="$parallel $c" ;;
  esac
done

run_case() {
  bash test/unit/test.sh "$1" > "test/unit/$1.log" 2>&1
  echo "$1 $?" >> status
}
export -f run_case

: > status
if [ -n "$parallel" ]; then
  printf '%s\n' $parallel | xargs -P "$jobs" -I{} bash -c 'run_case {}'
fi
for c in $sequential; do
  run_case "$c"
done

# test/unit/pending lists tests expected to fail until what they test
# is implemented: they fail quietly, and passing is an error (take them
# off the list).
pending=$(sed -e 's/#.*//' test/unit/pending 2> /dev/null | tr -s ' \t\n' ' ')
is_pending() { case " $pending " in *" $1 "*) return 0 ;; esac; return 1; }

total=$(wc -l < status | tr -d ' ')
failed=
waiting=
passing=
for c in $(awk '$2 != 0 { print $1 }' status | sort); do
  if is_pending "$c"; then waiting="$waiting $c"; else failed="$failed $c"; fi
done
for c in $(awk '$2 == 0 { print $1 }' status | sort); do
  if is_pending "$c"; then passing="$passing $c"; fi
done

if [ -n "$failed" ]; then
  for c in $failed; do
    echo "=== $c"
    grep -v '^OK\.$' "test/unit/$c.log" | tail -20
  done
  echo
  echo "FAILED: $(echo $failed | wc -w | tr -d ' ') of $total:" $failed
fi
if [ -n "$passing" ]; then
  echo "PASSING, but listed in test/unit/pending (remove them):" $passing
fi
if [ -n "$waiting" ]; then
  echo "pending: $(echo $waiting | wc -w | tr -d ' ') (test/unit/pending):" $waiting
fi
if [ -n "$failed" ] || [ -n "$passing" ]; then
  echo "logs in $work/test/unit"
  exit 1
fi
echo "passed: $(($total - $(echo $waiting | wc -w))) of $total"
