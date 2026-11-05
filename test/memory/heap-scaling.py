#!/usr/bin/env python3
"""Does what graphd leaves to request heaps grow with the data?

graphd allocates much of a request's memory on the request's heap and
leaves it there, to be freed with the heap when the request ends: the
parsed constraints, patterns, the output buffer. That is by design. A
leak is memory left behind per row or per step of work: it grows with
the data a request reads, and a long request on a large graph can run
out of memory before the heap is freed.

This runs the same queries - lookups, sorts, cursors, counts, "or"s,
nested subconstraints, versions, asof, and path searches of every kind -
on a small graph and a large one, with a graphd built with
-DCM_HEAP_CHECK, and compares, query by query, how many fragments each
allocation site leaves on the request heap. It fails if any site leaves
more on the larger graph.

  make BUILDDIR=out-heap CFLAGS="-g -O1 -w -DCM_HEAP_CHECK" \\
      out-heap/bin/graphd
  test/memory/heap-scaling.py --graphd out-heap/bin/graphd
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

PREFIX = '00000012400034568'


def guid(n):
  return PREFIX + '%015x' % n


def workload(size):
  """The writes that make a size x size grid, and the queries."""
  reqs = ['write (name="east")', 'write (name="south")']
  node = lambda r, c: 2 + r * size + c
  for r in range(size):
    for c in range(size):
      value = 'special' if (r, c) in ((size - 1, 0), (0, size - 1)) else (
          'v%04d' % (r * size + c))
      reqs.append('write (name="g%d-%d" value="%s")' % (r, c, value))
  links = {}
  for r in range(size):
    for c in range(size):
      if c < size - 1:
        links[(r, c, 'e')] = len(reqs)
        reqs.append('write (typeguid=%s left=%s right=%s)' %
                    (guid(0), guid(node(r, c)), guid(node(r, c + 1))))
      if r < size - 1:
        reqs.append('write (typeguid=%s left=%s right=%s)' %
                    (guid(1), guid(node(r, c)), guid(node(r + 1, c))))
  # History: a link moved, then deleted.
  before = len(reqs) - 1
  first = links[(0, 0, 'e')]
  reqs.append('write (guid~=%s typeguid=%s left=%s right=%s)' %
              (guid(first), guid(0), guid(node(0, 0)), guid(node(1, 1))))
  reqs.append('write (guid~=%s live=false typeguid=%s left=%s right=%s)' %
              (guid(len(reqs) - 1), guid(0), guid(node(0, 0)),
               guid(node(1, 1))))
  writes = len(reqs)

  g = lambda r, c: guid(node(r, c))
  last = size - 1
  E, S = guid(0), guid(1)
  fwd = ('step=(result=(contents) (<-left typeguid=(%s %s) result=((name)) '
         'right->(*)))' % (E, S))
  both = ('step=(result=(contents) '
          '{(<-left typeguid=(%s %s) result=((name)) right->(*))} | '
          '{(<-right typeguid=(%s %s) result=((name)) left->(*))})' %
          (E, S, E, S))
  queries = [
      'read (name="g1-1" result=((guid value)))',
      'read (any value>="v0010" value<"v0020" sort=value result=((name)))',
      'read (any value~="v00*" result=(count))',
      'read (any typeguid=null name!=null sort=-name pagesize=3 '
      'result=(cursor (name)))',
      'read (any typeguid=null result=(count))',
      'read (any {name="g0-0"} | {name="g1-1"} result=((name)))',
      'read (guid=%s result=((name contents)) (<-left result=((name)) '
      'right->(result=((name)) (<-left right->(result=((name)))))))' %
      g(0, 0),
      'read (any typeguid=%s result=(count (left right)))' % E,
      'read (any typeguid=%s newest>=0 live=dontcare result=(count))' % E,
      'read asof=%s (guid=%s (<-left typeguid=%s result=((right))))' % (
          guid(before), g(0, 0), E),
      'read (guid=%s result=((name contents)) (<-path %s result=(count)))' %
      (g(0, 0), fwd),
      'read (guid=%s result=((name contents)) (<-path %s guid=%s pathlimit=3 '
      'result=((steps path-count (path via)))))' % (g(0, 0), fwd,
                                                   g(last, last)),
      'read (guid=%s result=((name contents)) (<-path %s steps<=3 '
      'pathlimit=2 result=((name steps (path)))))' % (g(1, 1), both),
      'read (guid=%s result=((name contents)) (<-path %s pagesize=2 '
      'result=((name steps path))))' % (g(0, 0), fwd),
      'read (guid=%s result=((name contents)) (<-path %s value="special" '
      'result=((name steps path-count))))' % (g(1, 1), both),
      'read (guid=%s result=((name contents)) (<-path %s sort=-steps '
      'pagesize=3 result=((name steps via))))' % (g(0, 0), both),
  ]
  return reqs, writes, queries


def run(binary, reqs):
  """Replies and heap reports, one per request."""
  tmp = tempfile.mkdtemp(prefix='heap-scaling-')
  report = os.path.join(tmp, 'report')
  try:
    p = subprocess.run(
        [binary, '-voperator-error', '-S', '-x', binary, '-d',
         os.path.join(tmp, 'db'), '-bty'],
        input=''.join(r + '\n' for r in reqs), capture_output=True,
        text=True, cwd=tmp, env=dict(os.environ, CM_HEAP_REPORT=report),
        timeout=900)
    replies = p.stdout.splitlines()
    blocks, cur = [], {}
    for line in open(report):
      if line.startswith('-'):
        blocks.append(cur)
        cur = {}
      else:
        n, site = line.split()
        cur[site] = int(n)
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  if len(replies) != len(reqs) or len(blocks) < len(reqs):
    sys.exit('heap-scaling: %d replies and %d heap reports for %d requests'
             % (len(replies), len(blocks), len(reqs)))
  return replies, blocks[:len(reqs)]


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  parser.add_argument('--graphd', required=True)
  parser.add_argument('--small', type=int, default=8)
  parser.add_argument('--large', type=int, default=24)
  args = parser.parse_args()
  binary = os.path.abspath(args.graphd)

  results = {}
  for size in (args.small, args.large):
    reqs, writes, queries = workload(size)
    replies, blocks = run(binary, reqs + queries)
    for i, r in enumerate(replies[writes:]):
      if not r.startswith('ok'):
        sys.exit('heap-scaling: query %d failed on %dx%d: %s' %
                 (i, size, size, r[:200]))
    results[size] = (queries, replies[writes:], blocks[writes:])

  failed = False
  queries = results[args.small][0]
  for i, q in enumerate(queries):
    small = results[args.small][2][i]
    large = results[args.large][2][i]
    grew = sorted(site for site in large if large[site] > small.get(site, 0))
    status = 'GROWS' if grew else 'ok'
    print('%-5s query %2d: %d -> %d fragments left  %s' % (
        status, i, sum(small.values()), sum(large.values()), q[:70]))
    for site in grew:
      print('        %s: %d -> %d' % (site, small.get(site, 0), large[site]))
    failed = failed or bool(grew)
  sys.exit(1 if failed else 0)


if __name__ == '__main__':
  main()
