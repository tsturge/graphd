# Memory and load testing

`make test` runs each test against one graphd, one request at a time.
Three other checks look for what that misses: memory graphd never
frees, memory a request piles up as it works, and answers that change
when many connections share a graphd. None runs in `make test`.

## Memory graphd never frees: `-t`

`graphd -t` uses graphd's tracing allocator, which lists everything the
process allocated and didn't free when it exits. The tests treat
anything graphd prints as a failure, so to run the whole suite with it:

    GFLAGS="-voperator-error -t" test/run-tests.sh

All tests pass. (macOS's AddressSanitizer has no leak checker, so this
is the leak check there.)

## Memory a request piles up: `test/memory/heap-scaling.py`

A request allocates on its own heap, and graphd leaves much of that to
be freed with the heap when the request ends - the parsed request, the
output buffer. That is by design, so "nothing left on the heap" isn't
the test. A leak is what grows with the work: a request that leaves a
little behind per primitive read can run out of memory on a large
graph long before it ends.

Built with `-DCM_HEAP_CHECK`, the heap allocator records where each
fragment was allocated, and when a heap is destroyed it appends to
`$CM_HEAP_REPORT` how many fragments each allocation site left on it.
`heap-scaling.py` runs the same queries - lookups, value ranges, sorts,
cursors, counts, "or"s, nested subconstraints, history, asof, path
searches - on an 8 by 8 and a 24 by 24 grid, and fails if any site
leaves more on the larger one:

    make BUILDDIR=out-heap CFLAGS="-g -O1 -w -DCM_HEAP_CHECK" out-heap/bin/graphd
    test/memory/heap-scaling.py --graphd out-heap/bin/graphd

Nothing grows. The path module also checks its own allocations
exactly: built with `-DGRAPHD_PATH_CHECK_MEMORY`, graphd aborts if a
path search leaves anything at all on its request's heap.

## Many connections: `test/stress/stress.py`

graphd suspends a long read when a write is waiting behind it, and
resumes it later as of the time it started; requests from different
connections interleave between turns. The stress tool starts a graphd,
loads a 60 by 60 grid with values, two link types and some history,
and then for a minute (`--seconds`):

- readers run two dozen kinds of read, one at a time and pipelined, in
  random orders, and check each answer is the one it gave before any
  writing started (ignoring cursors, which name the database's size);
- pagers page through a sorted read with cursors, across requests;
- writers add nodes with links, give them new versions, and check that
  they read back what they wrote, find the newest version by an older
  version's guid (`guid~=`), and count what they have made.

It fails if any answer changes, a write isn't read back, or graphd
prints anything, crashes or hangs. Run it against AddressSanitizer
with `-t`:

    make BUILDDIR=out-asan \
        CFLAGS="-g -O1 -fsanitize=address -fno-omit-frame-pointer -w \
                -DGRAPHD_PATH_CHECK_MEMORY" \
        LDFLAGS=-fsanitize=address out-asan/bin/graphd
    test/stress/stress.py --graphd out-asan/bin/graphd --trace

In that build, a few dozen reads a minute run long enough to be suspended,
mostly path searches and large sorts. `--seed` repeats a run's choices
(though not its timing); `--keep` keeps graphd's directory.
