# Building graphd

graphd can be built two ways: with Bazel, as described in the
[README](README.md), or with the plain `Makefile` in the top-level
directory. The Makefile is useful where the Bazel setup in `WORKSPACE`
is too old to run, for example on Apple Silicon Macs, for which the
2019-era Bazel releases are not available.

graphd has no external dependencies beyond a C compiler and the system
C library; all of its libraries (`libcl`, `libcm`, `libes`, `libgraph`,
`libaddb`, `libpdb`, `libsrv`, `libgdp`) live in this repository.

## Building with make

Requirements: a C compiler (`clang` or `gcc`) and `make`. On macOS,
the Xcode Command Line Tools (`xcode-select --install`) provide both.

    make -j8

This builds the graphd binary at `out/bin/graphd`. Object files are
placed under `out/`, mirroring the source tree.

The output directory is `out/` rather than `build/` because on
case-insensitive filesystems (the macOS default) a `build/` directory
would collide with the Bazel `BUILD` files.

Useful variables:

| Variable   | Default        | Meaning                        |
|------------|----------------|--------------------------------|
| `CC`       | `cc`           | C compiler                     |
| `CFLAGS`   | `-g -O2 -w`    | Compiler flags                 |
| `BUILDDIR` | `out`          | Where objects and binary go    |

For example, an unoptimized debug build with warnings enabled:

    make -j8 CFLAGS="-g -O0 -Wall"

To remove build output:

    make clean

The source lists in the Makefile mirror the `srcs` of the
`cc_library` and `cc_binary` targets in each directory's `BUILD` file.
If you add or remove a source file, update both.

Tested on macOS (Apple Silicon, Apple clang 21).

## Testing

    make -j8 test

This builds graphd plus the `gld` and `gpush` clients (`make tools`
builds just those two), runs the `libcm` unit test, and runs every
case in `test/unit`. A full run takes a minute or two with the
default of 8 parallel jobs; set `TEST_JOBS` to change that. To run
only some cases, name them:

    make test TESTS="anchor restore-v2"

Each case `X` is a shell script `test/unit/X.sh` that feeds requests to
graphd, with its expected stdout in `X.out.exp` (and stderr, if any,
in `X.err.exp`). The cases run in a scratch copy of `test/unit` under
`out/test-unit/`, which is kept so you can look at the `X.out`, `X.err`
and `X.log` files of a failed case. A few cases start a graphd server
on a fixed TCP port (8102, 8111, 12345, 47276, 54321); those run one at
a time after the rest, and the ports need to be free.

To add a case, write `X.sh` following an existing one, run it with
`make test TESTS=X`, check `out/test-unit/test/unit/X.out` by hand, and
copy it to `test/unit/X.out.exp`.

## Running graphd

Interactive mode reads requests from stdin and writes responses to
stdout, using a private database directory:

    out/bin/graphd -y -d /tmp/db
    graphd> write(name = "HAS_KEY")
    ok (0100007f40000fbd8000000000000000)
    graphd> read()
    ok ((0100007f40000fbd8000000000000000 null "HAS_KEY" null null null true true 2026-10-28T18:49:11.0000Z))

To run as a server listening on a TCP port, in the foreground:

    out/bin/graphd -n -d /tmp/db -p /tmp/graphd.pid -i tcp://127.0.0.1:8100

Without `-p`, graphd tries to write its pid file to `/var/run/graphd.pid`,
which usually isn't writable by ordinary users. graphd also writes
`graphd-debug.log.<pid>` files to its current directory.

See [graphd(8)](doc/graphd.8.md) for all command-line options and
[graphd.conf(5)](doc/graphd.conf.5.md) for the configuration file.
