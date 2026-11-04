#!/usr/bin/env python3
"""Path search under load: suspension, and answers that stay the same.

graphd suspends a long read when a write is waiting behind it ("pushes
it back"), and resumes it later, as of the time it started. A path
search can be suspended in the middle of a walk or while it computes
via, and must come back to the same answer.

This starts a graphd on a free port, loads a 100 by 100 grid, and runs
path queries - one end and both, chains and alternatives, via and lists
of paths - several times over, while other connections write. It fails
if an answer changes, or graphd dies. Run it against a build with
AddressSanitizer and the path module's memory check:

  make BUILDDIR=out-check \\
      CFLAGS="-g -O1 -fsanitize=address -fno-omit-frame-pointer -w \\
              -DGRAPHD_PATH_CHECK_MEMORY" \\
      LDFLAGS=-fsanitize=address out-check/bin/graphd
  test/path/stress.py --graphd out-check/bin/graphd

It is not part of "make test": it takes a while, and needs several
connections at once.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

N = 100


def free_port():
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  return port


def connect(port, timeout=600):
  for _ in range(100):
    try:
      s = socket.create_connection(('127.0.0.1', port))
      s.settimeout(timeout)
      return s, s.makefile('rb')
    except OSError:
      time.sleep(0.1)
  sys.exit('stress: cannot connect to graphd')


def run(s, f, requests):
  """Sends requests in batches; returns the guid each write made."""
  out = []
  for i in range(0, len(requests), 500):
    batch = requests[i:i + 500]
    s.sendall(''.join(r + '\n' for r in batch).encode())
    for _ in batch:
      line = f.readline().decode()
      if not line.startswith('ok'):
        sys.exit('stress: write failed: %r' % line)
      out.append(line.split('(')[1].split(')')[0])
  return out


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  parser.add_argument('--graphd', required=True)
  parser.add_argument('--rounds', type=int, default=3)
  parser.add_argument('--writers', type=int, default=3)
  args = parser.parse_args()

  tmp = tempfile.mkdtemp(prefix='path-stress-')
  port = free_port()
  env = dict(os.environ, ASAN_OPTIONS='abort_on_error=1')
  log = open(os.path.join(tmp, 'graphd.err'), 'w')
  graphd = subprocess.Popen(
      [os.path.abspath(args.graphd), '-n', '-d', os.path.join(tmp, 'db'),
       '-p', os.path.join(tmp, 'graphd.pid'),
       '-i', 'tcp://127.0.0.1:%d' % port],
      cwd=tmp, stdout=subprocess.DEVNULL, stderr=log, env=env)
  failed = False
  try:
    s, f = connect(port)
    nodes = ['write (name="east")', 'write (name="south")'] + [
        'write (name="g%d-%d")' % (r, c) for r in range(N) for c in range(N)]
    G = run(s, f, nodes)
    E, S = G[0], G[1]
    g = lambda r, c: G[2 + r * N + c]
    links = []
    for r in range(N):
      for c in range(N):
        if c < N - 1:
          links.append('write (typeguid=%s left=%s right=%s)' %
                       (E, g(r, c), g(r, c + 1)))
        if r < N - 1:
          links.append('write (typeguid=%s left=%s right=%s)' %
                       (S, g(r, c), g(r + 1, c)))
    run(s, f, links)

    both = ('step=(result=(contents) '
            '{(<-left typeguid=(%s %s) result=((name)) right->(*))} | '
            '{(<-right typeguid=(%s %s) result=((name)) left->(*))})' %
            (E, S, E, S))
    fwd = ('step=(result=(contents) (<-left typeguid=(%s %s) '
           'result=((name)) right->(*)))' % (E, S))
    queries = [
        '(guid=%s result=((contents)) (<-path %s steps<=12 result=(count)))'
        % (g(50, 50), both),
        '(guid=%s result=((contents)) (<-path %s result=(count)))' %
        (g(0, 0), fwd),
        '(guid=%s result=((contents)) (<-path %s guid=%s pathlimit=3 '
        'result=((steps path-count (path via)))))' % (g(0, 0), fwd,
                                                     g(99, 99)),
        '(guid=%s result=((contents)) (<-path %s guid=%s pathlimit=2 '
        'result=((steps path-count (path via)))))' % (g(10, 10), both,
                                                     g(13, 12)),
        '(guid=%s result=((contents)) (<-path %s guid=%s '
        'result=((steps (via)))))' % (g(0, 0), fwd, g(60, 60)),
        '(guid=%s result=((contents)) (<-path %s sort=-steps pagesize=50 '
        'result=((steps path))))' % (g(50, 50), both),
    ]

    stop = threading.Event()
    writes = [0] * args.writers

    def writer(k):
      ws, wf = connect(port)
      while not stop.is_set():
        ws.sendall(('write (name="junk-%d-%d")\n' % (k, writes[k])).encode())
        wf.readline()
        writes[k] += 1

    threads = [threading.Thread(target=writer, args=(k,))
               for k in range(args.writers)]
    for t in threads:
      t.start()
    answers = {}
    for rnd in range(args.rounds):
      for i, q in enumerate(queries):
        t0 = time.time()
        s.sendall(('read cost="" %s\n' % q).encode())
        reply = f.readline().decode().strip()
        if not reply.startswith('ok'):
          print('query %d failed: %s' % (i, reply[:200]))
          failed = True
        reply = reply.split('" ', 1)[-1]  # without the cost
        if i in answers and answers[i] != reply:
          print('query %d: a different answer in round %d' % (i, rnd))
          failed = True
        answers[i] = reply
        print('round %d, query %d: %.2fs, %d bytes' %
              (rnd, i, time.time() - t0, len(reply)))
    stop.set()
    for t in threads:
      t.join()
    print('%d writes alongside' % sum(writes))
  except (OSError, ConnectionError) as e:
    print('stress: lost graphd: %s' % e)
    failed = True
  finally:
    graphd.terminate()
    graphd.wait()
    log.close()
    errors = open(os.path.join(tmp, 'graphd.err')).read().strip()
    if errors:
      print('graphd stderr:\n' + errors[-3000:])
      failed = True
    shutil.rmtree(tmp, ignore_errors=True)
  sys.exit(1 if failed else 0)


if __name__ == '__main__':
  main()
