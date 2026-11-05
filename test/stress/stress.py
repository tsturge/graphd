#!/usr/bin/env python3
"""graphd under concurrent load: answers that stay the same.

graphd serves many connections at once. A long read is suspended when
a write is waiting behind it ("pushed back"), and resumed later, as of
the time it started; reads and writes from different connections
interleave between turns. All of that must leave every answer as it
would have been with one connection.

This starts a graphd on a free port and loads a fixed graph: a grid of
nodes with values, links of two types between them, and some history (a
link replaced, then deleted). Then, at the same time:

- readers run every kind of read - lookups, value ranges and prefixes,
  sorts, counts, "or"s, nested subconstraints, history and asof,
  cursors paged across requests, and path searches of every kind - in
  their own orders, and check that each answer is the one it gave
  before any writing started;
- writers add nodes of their own (of types no read above looks at),
  each with a link, give them new versions, and check after each write
  that they read back what they wrote: the node and its link, the
  newest version by an older version's guid, and how many they have
  made.

It fails if an answer changes, a write isn't read back, or graphd
prints anything on stderr, crashes or stops answering. Run it against a
build with AddressSanitizer, and with -t (graphd's own tracing
allocator, which lists memory not freed at exit):

  make BUILDDIR=out-asan \\
      CFLAGS="-g -O1 -fsanitize=address -fno-omit-frame-pointer -w \\
              -DGRAPHD_PATH_CHECK_MEMORY" \\
      LDFLAGS=-fsanitize=address out-asan/bin/graphd
  test/stress/stress.py --graphd out-asan/bin/graphd --trace

It is not part of "make test": it takes a while, and needs several
connections at once.
"""

import argparse
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


def free_port():
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  return port


class Connection(object):

  def __init__(self, port, timeout):
    for _ in range(200):
      try:
        self.s = socket.create_connection(('127.0.0.1', port))
        break
      except OSError:
        time.sleep(0.1)
    else:
      raise OSError('cannot connect to graphd on port %d' % port)
    self.s.settimeout(timeout)
    self.f = self.s.makefile('rb')

  def ask(self, request):
    self.s.sendall((request + '\n').encode())
    line = self.f.readline()
    if not line:
      raise OSError('graphd closed the connection')
    return line.decode().rstrip('\n')

  def batch(self, requests):
    """Sends requests 500 at a time; returns the replies."""
    out = []
    for i in range(0, len(requests), 500):
      chunk = requests[i:i + 500]
      self.s.sendall(''.join(r + '\n' for r in chunk).encode())
      for _ in chunk:
        line = self.f.readline()
        if not line:
          raise OSError('graphd closed the connection')
        out.append(line.decode().rstrip('\n'))
    return out

  def close(self):
    self.s.close()


def written(reply):
  """The guid an "ok (guid)" reply to a write names."""
  if not reply.startswith('ok ('):
    raise ValueError(reply)
  return reply[4:].split(')')[0].split()[0]


def without_cursors(reply):
  """A cursor names the database's size, which writes change."""
  return re.sub(r'"sort:[^"]*"', '"<cursor>"', reply)


def load(conn, n):
  """Writes the graph; returns what the reads need to know about it."""
  nodes = ['write (name="east")', 'write (name="south")',
           'write (name="junk")']
  for r in range(n):
    for c in range(n):
      value = 'special' if (r, c) in ((n - 1, 0), (0, n - 1)) else (
          'v%05d' % (r * n + c))
      nodes.append('write (name="g%d-%d" value="%s")' % (r, c, value))
  G = [written(x) for x in conn.batch(nodes)]
  E, S, J = G[:3]
  g = lambda r, c: G[3 + r * n + c]
  links = []
  for r in range(n):
    for c in range(n):
      if c < n - 1:
        links.append('write (typeguid=%s left=%s right=%s)' %
                     (E, g(r, c), g(r, c + 1)))
      if r < n - 1:
        links.append('write (typeguid=%s left=%s right=%s)' %
                     (S, g(r, c), g(r + 1, c)))
  L = [written(x) for x in conn.batch(links)]
  # History: the first east link is replaced, then deleted.
  before = L[-1]
  moved = written(conn.ask('write (guid~=%s typeguid=%s left=%s right=%s)' %
                           (L[0], E, g(0, 0), g(1, 1))))
  written(conn.ask('write (guid~=%s live=false typeguid=%s left=%s '
                   'right=%s)' % (moved, E, g(0, 0), g(1, 1))))
  return dict(E=E, S=S, J=J, g=g, before=before, first=L[0])


def reads(n, k):
  """The reads, each a request that should always get the same answer."""
  E, S, g, last = k['E'], k['S'], k['g'], n - 1
  mid = n // 2
  fwd = ('step=(result=(contents) (<-left typeguid=(%s %s) result=((name)) '
         'right->(*)))' % (E, S))
  both = ('step=(result=(contents) '
          '{(<-left typeguid=(%s %s) result=((name)) right->(*))} | '
          '{(<-right typeguid=(%s %s) result=((name)) left->(*))})' %
          (E, S, E, S))
  return [
      # Lookups and values.
      'read (name="g1-1" result=((guid name value)))',
      'read (any value>="v00010" value<"v00200" sort=value result=((name)))',
      'read (any value~="v00*" result=(count))',
      'read (any value="special" sort=name result=((name)))',
      # Sorts, counts and pages of everything.
      'read (any typeguid=null name!=null sort=-name pagesize=7 '
      'result=(cursor (name)))',
      'read (any typeguid=null result=(count))',
      'read (any typeguid=null value!=null sort=value pagesize=500 '
      'result=((value)))',
      'read (any typeguid=%s result=(count))' % S,
      'read (any typeguid=(%s %s) result=(count))' % (E, S),
      # "or"s.
      'read (any {name="g0-0"} | {name="g1-1"} | {value="special"} '
      'sort=name result=((name)))',
      'read (any typeguid=null {value<"v00005"} | {value>"v%05d"} '
      'sort=value result=((value)))' % (n * n - 5),
      # Subconstraints.
      'read (guid=%s result=((name contents)) (<-left result=((name)) '
      'right->(result=((name)) (<-left right->(result=((name)))))))' %
      g(0, 0),
      'read (any typeguid=null value>="v%05d" value<"v%05d" sort=value '
      'result=((value contents)) (<-left typeguid=%s result=(count) '
      'right->(value>"v00000")))' % (n, n + 40, S),
      'read (any typeguid=%s result=(count) left->(value<"v00050") '
      'right->(value<"v00050"))' % E,
      # History.
      'read (any typeguid=%s newest>=0 live=dontcare result=(count))' % E,
      'read (guid=%s newest>=0 live=dontcare result=((guid live left right)))'
      % k['first'],
      'read asof=%s (guid=%s (<-left typeguid=%s result=((right))))' % (
          k['before'], g(0, 0), E),
      # Paths.
      'read (guid=%s result=((name contents)) (<-path %s steps<=10 '
      'result=(count)))' % (g(mid, mid), both),
      'read (guid=%s result=((name contents)) (<-path %s result=(count)))' %
      (g(0, 0), fwd),
      'read (guid=%s result=((name contents)) (<-path %s guid=%s pathlimit=3 '
      'result=((steps path-count (path via)))))' % (g(0, 0), fwd,
                                                   g(last, last)),
      'read (guid=%s result=((name contents)) (<-path %s guid=%s pathlimit=2 '
      'result=((steps path-count (path)))))' % (g(1, 1), both, g(4, 3)),
      'read (guid=%s result=((name contents)) (<-path %s guid=%s '
      'result=((steps (via)))))' % (g(0, 0), fwd, g(mid, mid)),
      'read (guid=%s result=((name contents)) (<-path %s value="special" '
      'result=((name steps path-count))))' % (g(1, 1), both),
      'read (guid=%s result=((name contents)) (<-path %s sort=-steps '
      'pagesize=40 result=((steps path))))' % (g(mid, mid), both),
  ]


def page(conn, request):
  """All the pages of a cursor read, as one answer."""
  pages, cursor = [], None
  while True:
    r = request if cursor is None else request.replace(
        'pagesize=', 'cursor="%s" pagesize=' % cursor, 1)
    reply = conn.ask(r)
    if not reply.startswith('ok ("'):
      return [reply]
    cursor = reply[len('ok ("'):].split('"', 1)[0]
    pages.append(reply.split('" ', 1)[1])
    if cursor == 'null:' or len(pages) > 1000:
      return pages


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  parser.add_argument('--graphd', required=True)
  parser.add_argument('--size', type=int, default=60,
                      help='the grid is size by size (default 60)')
  parser.add_argument('--seconds', type=float, default=60)
  parser.add_argument('--readers', type=int, default=4)
  parser.add_argument('--pagers', type=int, default=2)
  parser.add_argument('--writers', type=int, default=3)
  parser.add_argument('--seed', type=int, default=None)
  parser.add_argument('--trace', action='store_true',
                      help="run graphd with -t, which lists memory it "
                      "didn't free when it exits")
  parser.add_argument('--keep', action='store_true',
                      help="keep graphd's directory, and say where")
  args = parser.parse_args()
  if args.size < 6:
    parser.error('--size must be at least 6')
  seed = args.seed if args.seed is not None else random.randrange(1 << 30)
  print('seed %d' % seed)

  tmp = tempfile.mkdtemp(prefix='graphd-stress-')
  port = free_port()
  cmd = [os.path.abspath(args.graphd), '-n', '-voperator-error', '-d',
         os.path.join(tmp, 'db'), '-p', os.path.join(tmp, 'graphd.pid'),
         '-i', 'tcp://127.0.0.1:%d' % port]
  if args.trace:
    cmd.append('-t')
  log = open(os.path.join(tmp, 'graphd.err'), 'w')
  graphd = subprocess.Popen(
      cmd, cwd=tmp, stdout=subprocess.DEVNULL, stderr=log,
      env=dict(os.environ, ASAN_OPTIONS='abort_on_error=1'))

  problems = []
  lock = threading.Lock()

  def problem(text):
    with lock:
      if len(problems) < 50:
        problems.append(text)
      print('PROBLEM: ' + text[:300], flush=True)

  try:
    main_conn = Connection(port, 900)
    t0 = time.time()
    known = load(main_conn, args.size)
    queries = reads(args.size, known)
    expect = [without_cursors(a) for a in main_conn.batch(queries)]
    for q, a in zip(queries, expect):
      if not a.startswith('ok'):
        problem('a read failed before any load: %s -> %s' % (q, a))
    paged = [q for q in queries if 'cursor' in q]
    page_expect = [page(main_conn, q) for q in paged]
    print('loaded a %dx%d grid and %d reads in %.1fs' %
          (args.size, args.size, len(queries), time.time() - t0), flush=True)

    stop = threading.Event()
    counts = {'reads': 0, 'pages': 0, 'writes': 0}

    def count(what, n=1):
      with lock:
        counts[what] += n

    def guarded(body):
      def run(*a):
        try:
          body(*a)
        except Exception as e:  # pylint: disable=broad-except
          problem('%s: %s: %s' % (body.__name__, type(e).__name__, e))
          stop.set()
      return run

    @guarded
    def reader(rng):
      conn = Connection(port, 900)
      order = list(range(len(queries)))
      while not stop.is_set():
        rng.shuffle(order)
        # Some one at a time, some sent together.
        if rng.random() < 0.5:
          for i in order:
            if stop.is_set():
              break
            got = without_cursors(conn.ask(queries[i]))
            if got != expect[i]:
              problem('read %d changed: %s\n  was %s\n  now %s' %
                      (i, queries[i], expect[i][:200], got[:200]))
            count('reads')
        else:
          got = conn.batch([queries[i] for i in order])
          for i, a in zip(order, got):
            a = without_cursors(a)
            if a != expect[i]:
              problem('read %d changed (batched): %s\n  was %s\n  now %s' %
                      (i, queries[i], expect[i][:200], a[:200]))
          count('reads', len(order))
      conn.close()

    @guarded
    def pager(rng):
      conn = Connection(port, 900)
      while not stop.is_set():
        i = rng.randrange(len(paged))
        got = page(conn, paged[i])
        if got != page_expect[i]:
          was = page_expect[i]
          at = next((j for j in range(min(len(got), len(was)))
                     if got[j] != was[j]), min(len(got), len(was)))
          problem('paging changed: %s\n  %d pages, was %d; page %d was %s'
                  '\n  now %s' % (paged[i], len(got), len(was), at,
                                  was[at][:200] if at < len(was) else '-',
                                  got[at][:200] if at < len(got) else '-'))
        count('pages')
      conn.close()

    @guarded
    def writer(w, rng):
      conn = Connection(port, 900)
      tag, mine = 'w%d' % w, []
      # Our nodes' type, a node of type junk: no read above sees either.
      J = written(conn.ask('write (typeguid=%s name="%s")' %
                           (known['J'], tag)))
      while not stop.is_set():
        if mine and rng.random() < 0.3:
          # A new version of one of ours.
          i = rng.randrange(len(mine))
          old, name, v = mine[i]
          new = written(conn.ask(
              'write (guid~=%s typeguid=%s name="%s" value="%s" '
              'datatype=integer)' % (old, J, name, v + 1)))
          mine[i] = (new, name, v + 1)
          got = conn.ask('read (guid~=%s result=((name value)))' % old)
          if got != 'ok (("%s" "%d"))' % (name, v + 1):
            problem('%s: after writing %s as a version of %s, %s reads %s;'
                    ' its versions: %s' % (
                        tag, new, old, name, got, conn.ask(
                            'read (typeguid=%s name="%s" newest>=0 '
                            'live=dontcare result=((guid live value)))' %
                            (J, name))))
        else:
          name = '%s-%d' % (tag, len(mine))
          new = written(conn.ask(
              'write (typeguid=%s name="%s" value="0" datatype=integer '
              '(<-left typeguid=%s right=%s))' %
              (J, name, known['J'], known['g'](rng.randrange(args.size),
                                      rng.randrange(args.size)))))
          mine.append((new, name, 0))
          got = conn.ask('read (guid=%s result=((name contents)) '
                         '(<-left result=(count)))' % new)
          if got != 'ok (("%s" (1)))' % name:
            problem('%s: %s reads %s' % (tag, name, got))
        count('writes')
        if rng.random() < 0.05:
          got = conn.ask('read (typeguid=%s result=(count))' % J)
          if got != 'ok (%d)' % len(mine):
            problem('%s: made %d, counted %s' % (tag, len(mine), got))
      conn.close()

    rng = random.Random(seed)
    threads = (
        [threading.Thread(target=reader, args=(random.Random(rng.random()),))
         for _ in range(args.readers)] +
        [threading.Thread(target=pager, args=(random.Random(rng.random()),))
         for _ in range(args.pagers)] +
        [threading.Thread(target=writer, args=(w, random.Random(rng.random())))
         for w in range(args.writers)])
    for t in threads:
      t.start()
    end = time.time() + args.seconds
    while time.time() < end and not stop.is_set():
      time.sleep(min(10, max(0, end - time.time())))
      if graphd.poll() is not None:
        problem('graphd exited with status %s' % graphd.returncode)
        break
      print('%4.0fs: %d reads, %d paged reads, %d writes' %
            (args.seconds - (end - time.time()), counts['reads'],
             counts['pages'], counts['writes']), flush=True)
    stop.set()
    for t in threads:
      t.join(900)
      if t.is_alive():
        problem('a connection is stuck')
    # Afterwards, still the same?
    for q, a, got in zip(queries, expect, main_conn.batch(queries)):
      if without_cursors(got) != a:
        problem('after the load, changed: %s' % q)
    main_conn.close()
  except (OSError, ValueError) as e:
    problem('%s: %s' % (type(e).__name__, e))
  finally:
    if graphd.poll() is None:
      graphd.send_signal(signal.SIGTERM)
      try:
        graphd.wait(300)
      except subprocess.TimeoutExpired:
        problem("graphd didn't exit")
        graphd.kill()
        graphd.wait()
    log.close()
    errors = open(os.path.join(tmp, 'graphd.err')).read().strip()
    if errors:
      problem('graphd stderr:\n' + errors[-4000:])
    if args.keep:
      print('graphd ran in %s' % tmp)
    else:
      shutil.rmtree(tmp, ignore_errors=True)

  if problems:
    print('FAILED, %d problems (seed %d)' % (len(problems), seed))
    sys.exit(1)
  print('ok')


if __name__ == '__main__':
  main()
