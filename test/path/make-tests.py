#!/usr/bin/env python3
"""Generates graphd's path search tests: test/unit/path-*.sh and .out.exp.

Path search (doc/path-proposal.md) is tested on graphs too large to work
out by hand, so this script builds each graph, its path queries and
their expected replies. It keeps a model of the graph: every primitive
it writes and the guid graphd will give it, which links are live, and
which were versioned or deleted when. Expected replies come from a
breadth-first search over the model that follows the proposal: matches
in order of steps, then guid; each match's shortest paths in order of
the guids of the nodes before it, back to the start, the first of them
its "path"; the start never matches.

Everything the model claims is checked against graphd as it is today,
without path search:

  - graphd must give each write the guid the model expects;
  - each step of each expected path must match the step template, read
    with its root bound to the step's start and "*" to its end; that
    read's reply list is the step's element of the "via" result;
  - each expected distance d must be right: the template nested d times
    reaches the match, and nested d-1 times does not;
  - unreachable targets must be unreachable within a few nestings.

Run it after changing the tests, from the top of the graphd tree, with
a built graphd:

  test/path/make-tests.py [--graphd out/bin/graphd]

It writes the tests: test/unit/path-tree, path-coauthor, path-grid and
path-semantics.
"""

import argparse
import itertools
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

PREFIX = '00000012400034568'
TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))
UNIT = os.path.join(TOP, 'test', 'unit')

# How deep to check distances by nesting templates. Templates with an
# "*" in two alternatives double in size with each level.
MAX_UNROLL = {1: 16, 2: 6}

# How many paths a list of paths holds without a pathlimit.
DEFAULT_PATHLIMIT = 1000


def guid(serial):
  return PREFIX + '%015x' % serial


def q(s):
  return '"%s"' % s


class Model(object):
  """A graph, as the writes that make it and the state they leave."""

  def __init__(self):
    self.requests = []
    self.serial = 0
    self.nodes = {}      # name -> {'guid', 'serial', 'value'}
    self.by_guid = {}    # guid -> name
    self.links = []      # {'name', 'type', 'left', 'right', 'serial', 'end'}

  def _write(self, text):
    self.requests.append(text)
    self.serial += 1
    return self.serial - 1

  def node(self, name, value=None):
    attrs = 'name=%s' % q(name)
    if value is not None:
      attrs += ' value=%s' % q(value)
    n = self._write('write (%s)' % attrs)
    self.nodes[name] = {'guid': guid(n), 'serial': n, 'value': value}
    self.by_guid[guid(n)] = name

  def g(self, name):
    return self.nodes[name]['guid']

  def _link_text(self, name, typ, a, b):
    return 'name=%s typeguid=%s left=%s right=%s' % (
        q(name), self.g(typ), self.g(a), self.g(b))

  def link(self, name, typ, a, b):
    n = self._write('write (%s)' % self._link_text(name, typ, a, b))
    self.links.append({'name': name, 'type': typ, 'left': a, 'right': b,
                       'serial': n, 'end': None})

  def _current(self, name):
    [l] = [l for l in self.links if l['name'] == name and l['end'] is None]
    return l

  def relink(self, name, b):
    """A new version of link <name>, pointing at <b>."""
    old = self._current(name)
    n = self._write('write (guid~=%s %s)' % (
        guid(old['serial']),
        self._link_text(name, old['type'], old['left'], b)))
    old['end'] = n
    self.links.append(dict(old, right=b, serial=n, end=None))

  def delete(self, name):
    old = self._current(name)
    n = self._write('write (guid~=%s live=false %s)' % (
        guid(old['serial']),
        self._link_text(name, old['type'], old['left'], old['right'])))
    old['end'] = n

  def now(self):
    """The serial of the last primitive written, for asof."""
    return self.serial - 1

  def live(self, asof=None):
    limit = self.serial if asof is None else asof
    return [l for l in self.links
            if l['serial'] <= limit and (l['end'] is None or l['end'] > limit)]


class Step(object):
  """A step template, and what it does to the model.

  body: the template's constraints, with "(*)" where the step ends.
  neighbors(model, links, x): the nodes one step from node x.
  """

  def __init__(self, body, neighbors):
    self.body = body
    self.neighbors = neighbors
    self.stars = body.count('(*)')

  def unroll(self, k, final):
    """The template nested k times, ending in constraints <final>."""
    text = final
    for _ in range(k):
      text = self.body.replace('(*)', '(%s)' % text)
    return text


def out_links(typ):
  return lambda m, links, x: {l['right'] for l in links
                              if l['type'] == typ and l['left'] == x}


def in_links(typ):
  return lambda m, links, x: {l['left'] for l in links
                              if l['type'] == typ and l['right'] == x}


def both_links(typ):
  out, into = out_links(typ), in_links(typ)
  return lambda m, links, x: out(m, links, x) | into(m, links, x)


def through(typ):
  """x <-typ- M -typ-> y: authors of the same paper."""
  def neighbors(m, links, x):
    middles = {l['left'] for l in links if l['type'] == typ and l['right'] == x}
    return {l['right'] for l in links
            if l['type'] == typ and l['left'] in middles}
  return neighbors


def only_from(neighbors, ok):
  """Steps start only from nodes for which ok(model, x)."""
  return lambda m, links, x: neighbors(m, links, x) if ok(m, x) else set()


def by_guid(m, n):
  """Matches named by guid=, which graphd searches for from both ends."""
  return ('guid=%s' % m.g(n), lambda m, y: y == n)


class Query(object):
  """A path query, from each start, by a step.

  match: extra constraints on matches, as graphd text, and the same as
  a predicate on node names.  steps: [(op, n)].  result: a list of atoms
  for result=((...)), or 'count', or ('count', atoms) for
  result=(count (...)); among the atoms, a tuple is a list of paths,
  ((... (path via))).  pathlimit: a number, 'all', or None.
  """

  def __init__(self, starts, step, match=('', None), steps=(), pagesize=None,
               optional=False, result=('name', 'path', 'via', 'steps'),
               asof=None, pathlimit=None):
    self.starts = starts
    self.step = step
    self.match_text, self.match = match
    self.steps = list(steps)
    self.pagesize = pagesize
    self.optional = optional
    self.result = result
    self.asof = asof
    self.pathlimit = pathlimit

  def text(self, m):
    parts = ['step=(%s)' % self.step.body]
    parts += ['steps%s%d' % (op, n) for op, n in self.steps]
    if self.match_text:
      parts.append(self.match_text)
    if self.pagesize is not None:
      parts.append('pagesize=%d' % self.pagesize)
    if self.pathlimit is not None:
      parts.append('pathlimit=%s' % self.pathlimit)
    if self.optional:
      parts.append('optional')
    atoms = lambda xs: ' '.join(
        '(%s)' % ' '.join(x) if isinstance(x, tuple) else x for x in xs)
    if self.result == 'count':
      parts.append('result=(count)')
    elif isinstance(self.result, tuple) and self.result[0] == 'count':
      parts.append('result=(count (%s))' % atoms(self.result[1]))
    else:
      parts.append('result=((%s))' % atoms(self.result))
    starts = [m.g(s) for s in self.starts]
    start = ('guid=%s' % starts[0] if len(starts) == 1
             else 'guid=(%s)' % ' '.join(starts))
    asof = '' if self.asof is None else 'asof=%s ' % guid(self.asof)
    return 'read %s(%s result=((name contents)) (<-path %s))' % (
        asof, start, ' '.join(parts))


class Graphd(object):
  """Runs requests through a fresh graphd, one reply line each."""

  def __init__(self, binary):
    self.binary = binary

  def run(self, requests):
    tmp = tempfile.mkdtemp(prefix='path-tests-')
    try:
      p = subprocess.run(
          [self.binary, '-voperator-error', '-S', '-x', self.binary,
           '-d', os.path.join(tmp, 'db'), '-bty'],
          input=''.join(r + '\n' for r in requests), capture_output=True,
          text=True, cwd=tmp, timeout=600)
    finally:
      shutil.rmtree(tmp, ignore_errors=True)
    replies = p.stdout.splitlines()
    if len(replies) != len(requests):
      sys.exit('graphd gave %d replies to %d requests:\n%s' % (
          len(replies), len(requests), p.stdout[-2000:] + p.stderr[-2000:]))
    return replies


class Search(object):
  """Distances and shortest paths from start, as the proposal defines."""

  def __init__(self, m, start, step, asof):
    self.m = m
    self.start = start
    links = m.live(asof)
    self.dist = {start: 0}
    self.preds = {}  # node -> its predecessors on shortest paths, by guid
    frontier, d = [start], 0
    while frontier:
      d += 1
      found = {}
      for x in sorted(frontier, key=m.g):
        for y in step.neighbors(m, links, x):
          if y not in self.dist:
            found.setdefault(y, []).append(x)
      for y, xs in found.items():
        self.dist[y], self.preds[y] = d, xs
      frontier = list(found)
    self._counts = {start: 1}

  def paths(self, y):
    """y's shortest paths, in order: by the node before y, then the one
    before that, and so on back, lowest guid first."""
    if y == self.start:
      yield [y]
      return
    for x in self.preds[y]:
      for p in self.paths(x):
        yield p + [y]

  def path(self, y):
    """y's path: the first of its shortest paths."""
    return next(self.paths(y))

  def count(self, y):
    """How many shortest paths y has."""
    if y not in self._counts:
      self._counts[y] = sum(self.count(x) for x in self.preds[y])
    return self._counts[y]


def in_bounds(d, steps):
  ops = {'<': d.__lt__, '<=': d.__le__, '=': d.__eq__, '>=': d.__ge__,
         '>': d.__gt__}
  return all(ops[op](n) for op, n in steps)


class Expect(object):
  """Works out a query's reply, and the checks that support it."""

  def __init__(self, m, query):
    self.m = m
    self.query = query
    self.checks = []  # (request, 'ok' | 'empty', why, reply index for via)
    self.via_slots = []

  def plan(self):
    """The reply, with {via:N} where check N's reply goes."""
    m, qy = self.m, self.query
    rows = []
    for start in sorted(qy.starts, key=m.g):
      found = Search(m, start, qy.step, qy.asof)
      dist = found.dist
      matches = sorted(
          (y for y, d in dist.items()
           if d > 0 and in_bounds(d, qy.steps) and
           (qy.match is None or qy.match(m, y))),
          key=lambda y: (dist[y], m.g(y)))
      self._check_unreachable(start, dist)
      for y in matches:
        self._check_distance(start, y, dist[y])
      if qy.pagesize is not None:
        matches = matches[:qy.pagesize]
      if not matches and not qy.optional:
        continue
      rows.append('(%s %s)' % (q(start), self._sub(found, matches)))
    if not rows:
      return 'error EMPTY "not found"'
    return 'ok (%s)' % ' '.join(rows)

  def _read(self, constraints):
    asof = '' if self.query.asof is None else 'asof=%s ' % guid(
        self.query.asof)
    return 'read %s(%s)' % (asof, constraints)

  def _check_distance(self, start, y, d):
    step = self.query.step
    if d > MAX_UNROLL[step.stars]:
      return
    gs, gy = self.m.g(start), 'guid=%s' % self.m.g(y)
    self.checks.append((self._read('guid=%s %s' % (gs, step.unroll(d, gy))),
                        'ok', '%s reaches %s in %d steps' % (start, y, d)))
    if d > 1:
      self.checks.append((self._read('guid=%s %s' % (
          gs, step.unroll(d - 1, gy))), 'empty',
                          '%s does not reach %s in %d steps' % (start, y,
                                                                 d - 1)))

  def _check_unreachable(self, start, dist):
    # Nodes the model says are out of reach must be, a few steps out.
    step = self.query.step
    gs = self.m.g(start)
    for y in sorted(self.m.nodes, key=self.m.g):
      if y in dist:
        continue
      for k in range(1, min(4, MAX_UNROLL[step.stars]) + 1):
        self.checks.append((self._read('guid=%s %s' % (
            gs, step.unroll(k, 'guid=%s' % self.m.g(y)))), 'empty',
                            '%s does not reach %s in %d steps' % (start, y,
                                                                   k)))

  def _via(self, x, y):
    """A slot for the step template's reply with its ends bound."""
    body = self.query.step.body.replace('(*)', '(guid=%s)' % self.m.g(y))
    self.checks.append((self._read('guid=%s %s' % (self.m.g(x), body)),
                        'via', 'a step from %s to %s' % (x, y)))
    return '{via:%d}' % (len(self.checks) - 1)

  def _path_field(self, a, path):
    """One of path, via and steps, for one path."""
    if a == 'path':
      return '(%s)' % ' '.join(self.m.g(n) for n in path)
    if a == 'via':
      return '(%s)' % ' '.join(self._via(x, y) for x, y in zip(path,
                                                               path[1:]))
    if a == 'steps':
      return str(len(path) - 1)
    raise ValueError(a)

  def _fields(self, atoms, found, y):
    out = []
    for a in atoms:
      if isinstance(a, tuple):
        # A list of paths, up to the pathlimit.
        limit = self.query.pathlimit
        if limit is None:
          limit = DEFAULT_PATHLIMIT
        paths = list(itertools.islice(
            found.paths(y), None if limit == 'all' else limit))
        # Counting and listing paths are done separately; they agree.
        assert len(paths) == min(found.count(y), len(paths)), y
        if limit == 'all' or len(paths) < limit:
          assert len(paths) == found.count(y), y
        out.append('(%s)' % ' '.join(
            '(%s)' % ' '.join(self._path_field(b, p) for b in a)
            for p in paths))
      elif a == 'name':
        out.append(q(y))
      elif a == 'guid':
        out.append(self.m.g(y))
      elif a == 'value':
        v = self.m.nodes[y]['value']
        out.append('null' if v is None else q(v))
      elif a == 'path-count':
        out.append(str(found.count(y)))
      else:
        out.append(self._path_field(a, found.path(y)))
    return '(%s)' % ' '.join(out)

  def _sub(self, found, matches):
    r = self.query.result
    if r == 'count':
      return '(%d)' % len(matches)
    if isinstance(r, tuple) and r[0] == 'count':
      return '(%s)' % ' '.join(
          [str(len(matches))] +
          [self._fields(r[1], found, y) for y in matches])
    return '(%s)' % ' '.join(self._fields(r, found, y) for y in matches)


def is_list(text):
  """Whether text is a single parenthesized list."""
  if not text.startswith('('):
    return False
  depth = 0
  for i, ch in enumerate(re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)):
    depth += {'(': 1, ')': -1}.get(ch, 0)
    if depth == 0:
      return i == len(re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)) - 1
  return False


def generate(name, describe, m, queries, gd):
  """Checks <queries> on <m> against graphd, then writes the test."""
  expects = [Expect(m, qy) for qy in queries]
  plans = [e.plan() for e in expects]
  checks = [c for e in expects for c in e.checks]
  replies = gd.run(m.requests + [c[0] for c in checks])

  for i, r in enumerate(replies[:len(m.requests)]):
    if r != 'ok (%s)' % guid(i):
      sys.exit('%s: write %d got %s, not guid %s' % (name, i, r, guid(i)))

  check_replies = replies[len(m.requests):]
  base = 0
  expected = []
  for e, plan in zip(expects, plans):
    for i, (req, kind, why) in enumerate(e.checks):
      reply = check_replies[base + i]
      if kind == 'ok' and not reply.startswith('ok '):
        sys.exit('%s: expected %s:\n  %s\n  %s' % (name, why, req, reply))
      if kind == 'empty' and reply != 'error EMPTY "not found"':
        sys.exit('%s: expected %s:\n  %s\n  %s' % (name, why, req, reply))
      if kind == 'via':
        # A step's via is the reply's list: the step template's root
        # results, with its ends bound.
        if not is_list(reply[len('ok '):]):
          sys.exit('%s: expected %s:\n  %s\n  %s' % (name, why, req, reply))
        plan = plan.replace('{via:%d}' % i, reply[len('ok '):])
    base += len(e.checks)
    expected.append(plan)

  lines = m.requests + [qy.text(m) for qy in queries]
  with open(os.path.join(UNIT, name + '.sh'), 'w') as f:
    f.write('#!/bin/bash\n#\n')
    for line in describe.strip().splitlines():
      f.write(('# ' + line).rstrip() + '\n')
    f.write('#\n# Generated by test/path/make-tests.py; edit that, not this.\n')
    f.write('\nB=`basename $0 .sh`\ncd `dirname $0`\nsource ./rungraphd\n\n')
    f.write('rm -rf $D\nrungraphd -d${D} -bty <<-\'EOF\'\n')
    for line in lines:
      f.write('\t%s\n' % line)
    f.write('\tEOF\nrm -rf $D\n')
  os.chmod(os.path.join(UNIT, name + '.sh'), 0o755)
  with open(os.path.join(UNIT, name + '.out.exp'), 'w') as f:
    for i in range(len(m.requests)):
      f.write('ok (%s)\n' % guid(i))
    for e in expected:
      f.write(e + '\n')
  return len(queries), len(checks)


# ---------------------------------------------------------------- tests


def tree_tests():
  """A binary tree, five levels deep: n0 at the root, n1 and n2 its
  children, and so on, with "child" links from parent to child.

  Down, up, and either way; neighborhoods by depth; paging; nodes with
  nothing to reach.
  """
  m = Model()
  m.node('child')
  for i in range(31):
    m.node('n%d' % i)
  for i in range(1, 31):
    m.link('c%d' % i, 'child', 'n%d' % ((i - 1) // 2), 'n%d' % i)
  c = m.g('child')
  down = Step('result=(contents) (<-left typeguid=%s result=((name)) right->(*))' % c,
              out_links('child'))
  up = Step('result=(contents) (<-right typeguid=%s result=((name)) left->(*))' % c,
            in_links('child'))
  either = Step('result=(contents) {(<-left typeguid=%s result=((name)) right->(*))} | '
                '{(<-right typeguid=%s result=((name)) left->(*))}' % (c, c),
                both_links('child'))
  name = lambda n: ('name=%s' % q(n), lambda m, y: y == n)
  qs = [
      Query(['n0'], down, name('n27')),
      Query(['n27'], up, name('n0')),
      Query(['n15'], either, name('n30')),
      Query(['n15'], either, name('n16')),
      Query(['n0'], down, steps=[('=', 1)]),
      Query(['n0'], down, steps=[('=', 2)], result=('name', 'steps')),
      Query(['n0'], down, steps=[('<=', 2)], result='count'),
      Query(['n0'], down, steps=[('>=', 4)], result='count'),
      Query(['n0'], down, steps=[('>', 1), ('<', 3)], result=('name',)),
      Query(['n0'], down, result='count'),
      Query(['n0'], down, pagesize=5, result=('name', 'steps')),
      Query(['n0'], down, pagesize=3, result=('count', ('name',))),
      Query(['n5'], down, result=('name', 'steps', 'path')),
      Query(['n30'], down),
      Query(['n30'], down, optional=True),
      Query(['n0'], up),
      Query(['n0'], down, name('n0')),
      Query(['n12'], either, steps=[('<=', 2)], result=('name', 'steps')),
      Query(['n0'], down, steps=[('<=', 10), ('>=', 11)]),
      Query(['n0'], down, steps=[('=', 0)]),
      Query(['n3', 'n4'], down, steps=[('=', 1)], result=('name', 'path')),
      Query(['n3', 'n30'], down, steps=[('=', 1)], result=('name',)),
      Query(['n3', 'n30'], down, steps=[('=', 1)], optional=True,
            result=('name',)),
      # The same, named by guid=: searched for from both ends.
      Query(['n0'], down, by_guid(m, 'n27')),
      Query(['n27'], up, by_guid(m, 'n0')),
      Query(['n30'], up, by_guid(m, 'n0'),
            result=('name', 'steps', 'path-count', ('path', 'via'))),
      Query(['n30'], down, by_guid(m, 'n0')),
      Query(['n0'], down, by_guid(m, 'n27'), steps=[('<=', 3)]),
      Query(['n0'], down, by_guid(m, 'n27'), steps=[('<=', 4)],
            result=('name', 'steps')),
      Query(['n0'], down, by_guid(m, 'n0')),
      # A tree has one path to each node.
      Query(['n0'], down, name('n27'),
            result=('name', 'path-count', ('path', 'steps'))),
      Query(['n15'], either, steps=[('=', 2)], pathlimit='all',
            result=('name', 'path-count', ('path',))),
  ]
  return generate('path-tree', tree_tests.__doc__, m, qs, Graphd(ARGS.graphd))


def coauthor_tests():
  """Authors and papers: 40 authors, 60 papers with 1 to 4 authors each,
  chosen at random (seed 7), with "author" links from paper to author,
  and two more papers by a0 and a11. Authors a35 to a39 write only with
  each other.

  Shortest co-author paths, through the papers in between; each step's
  "via" lists every paper the two share.
  """
  rnd = random.Random(7)
  m = Model()
  m.node('author')
  for i in range(40):
    m.node('a%d' % i)
  for p in range(60):
    pool = range(35, 40) if p >= 54 else range(35)
    authors = rnd.sample(list(pool), rnd.choice([1, 1, 2, 2, 2, 3, 4]))
    m.node('p%d' % p)
    for a in authors:
      m.link('p%d-a%d' % (p, a), 'author', 'p%d' % p, 'a%d' % a)
  # Two more papers by a0 and a11: a step with several papers between.
  for p in [60, 61]:
    m.node('p%d' % p)
    for a in [0, 11]:
      m.link('p%d-a%d' % (p, a), 'author', 'p%d' % p, 'a%d' % a)
  t = m.g('author')
  co = Step('result=(contents) (<-right typeguid=%s result=((contents)) '
            'left->(result=((name)) (<-left typeguid=%s result=none '
            'right->(*))))' % (t, t), through('author'))
  name = lambda n: ('name=%s' % q(n), lambda m, y: y == n)
  # Pairs at each distance the graph has, nearest and farthest first.
  dist0 = Search(m, 'a0', co, None).dist
  far = max(dist0.values())
  farthest = sorted((y for y in dist0 if dist0[y] == far), key=m.g)
  qs = [Query(['a0'], co, name(farthest[0]))]
  for a, b in [('a1', 'a2'), ('a3', 'a17'), ('a8', 'a30'), ('a12', 'a33'),
               ('a20', 'a5'), ('a35', 'a39')]:
    qs.append(Query([a], co, name(b)))
    qs.append(Query([a], co, by_guid(m, b)))
  qs += [
      Query(['a0'], co, name('a11')),
      Query(['a0'], co, name('a36')),
      Query(['a36'], co, result=('name', 'steps')),
      Query(['a0'], co, steps=[('=', 1)], result=('name',)),
      Query(['a0'], co, steps=[('=', 2)], result='count'),
      Query(['a0'], co, result=('name', 'steps')),
      Query(['a0'], co, pagesize=1, steps=[('>=', 3)],
            result=('name', 'path', 'via', 'steps')),
      Query(['a4', 'a9', 'a36'], co, name('a21'), optional=True),
      Query(['a4', 'a9', 'a36'], co, name('a21')),
      # Named by guid=, from both ends: every shortest path, and via.
      Query(['a0'], co, by_guid(m, farthest[0]),
            result=('name', 'steps', 'path-count', ('path', 'via'))),
      Query(['a0'], co, by_guid(m, farthest[-1]), pathlimit=1,
            result=('name', 'path-count', 'path', ('path',))),
      Query(['a0'], co, by_guid(m, 'a11'),
            result=('name', 'path-count', ('path', 'via'))),
      Query(['a0'], co, by_guid(m, 'a36')),
      Query(['a4', 'a9', 'a36'], co, by_guid(m, 'a21'), optional=True,
            result=('name', 'path-count', ('path',))),
      # Several shortest paths.
      Query(['a0'], co, name(farthest[0]),
            result=('name', 'path-count', ('path', 'via'))),
      Query(['a0'], co, name(farthest[0]), pathlimit=2,
            result=('name', 'path-count', ('path', 'via', 'steps'))),
      Query(['a0'], co, steps=[('=', 3)], pagesize=4, pathlimit='all',
            result=('count', ('name', 'path-count', ('path',)))),
  ]
  return generate('path-coauthor', coauthor_tests.__doc__, m, qs,
                  Graphd(ARGS.graphd))


def grid_tests():
  """An 8 by 8 grid, g0-0 to g7-7, with "east" and "south" links.

  Undirected, as an "or" of two linkages, and directed. Many paths are
  equally short; ties go to the lowest-guid predecessor, so the path
  from one corner to the other is fixed. The number of nodes at each
  distance from a corner is known in closed form.
  """
  m = Model()
  m.node('east')
  m.node('south')
  for r in range(8):
    for c in range(8):
      m.node('g%d-%d' % (r, c))
  for r in range(8):
    for c in range(8):
      if c < 7:
        m.link('e%d-%d' % (r, c), 'east', 'g%d-%d' % (r, c),
               'g%d-%d' % (r, c + 1))
      if r < 7:
        m.link('s%d-%d' % (r, c), 'south', 'g%d-%d' % (r, c),
               'g%d-%d' % (r + 1, c))
  e, s = m.g('east'), m.g('south')
  out_any = lambda m_, links, x: (out_links('east')(m_, links, x) |
                                  out_links('south')(m_, links, x))
  in_any = lambda m_, links, x: (in_links('east')(m_, links, x) |
                                 in_links('south')(m_, links, x))
  both = Step('result=(contents) {(<-left typeguid=(%s %s) result=((name)) right->(*))} | '
              '{(<-right typeguid=(%s %s) result=((name)) left->(*))}' % (
                  e, s, e, s),
              lambda m_, links, x: out_any(m_, links, x) | in_any(m_, links, x))
  forward = Step('result=(contents) (<-left typeguid=(%s %s) result=((name)) right->(*))' % (e, s),
                 out_any)
  backward = Step('result=(contents) (<-right typeguid=(%s %s) result=((name)) left->(*))' % (
      e, s), in_any)
  east = Step('result=(contents) (<-left typeguid=%s result=((name)) right->(*))' % e,
              out_links('east'))
  name = lambda n: ('name=%s' % q(n), lambda m, y: y == n)
  qs = [
      Query(['g0-0'], forward, name('g7-7')),
      Query(['g7-7'], backward, name('g0-0')),
      Query(['g7-7'], both, name('g0-0'), result=('steps', 'path')),
      Query(['g7-7'], forward),
      Query(['g0-7'], forward, name('g7-0')),
      Query(['g0-7'], both, name('g7-0'), result=('steps', 'path')),
      Query(['g3-3'], east, result=('name', 'steps')),
      Query(['g3-3'], both, steps=[('<=', 1)], result=('name',)),
  ]
  for d in range(1, 15):
    qs.append(Query(['g0-0'], forward, steps=[('=', d)], result='count'))
  qs += [
      Query(['g0-0'], both, steps=[('>=', 13)], result=('name', 'steps')),
      Query(['g0-0'], both, result='count'),
      Query(['g4-4'], both, steps=[('=', 3)], result='count'),
      Query(['g4-4'], both, pagesize=6, result=('name', 'steps')),
      # Corner to corner by guid=, from both ends.
      Query(['g0-0'], forward, by_guid(m, 'g7-7'),
            result=('steps', 'path-count', 'path')),
      Query(['g0-0'], forward, by_guid(m, 'g7-7'), pathlimit=3,
            result=('steps', 'path-count', ('path', 'via'))),
      Query(['g7-7'], backward, by_guid(m, 'g0-0'), pathlimit=2,
            result=('path-count', ('path',))),
      Query(['g0-0'], forward, by_guid(m, 'g2-3'), pathlimit='all',
            result=('path-count', ('path',))),
      Query(['g7-7'], forward, by_guid(m, 'g0-0')),
      Query(['g0-0'], forward, by_guid(m, 'g7-7'), steps=[('<', 14)]),
      # A match named by guid=, among others as near.
      Query(['g0-0'], both, ('guid=%s' % m.g('g1-1'), lambda m, y: y == 'g1-1'),
            result=('name', 'steps', 'path-count')),
      # Many equally short paths: C(r + c, r) from one corner.
      Query(['g0-0'], forward, name('g2-2'), pathlimit='all',
            result=('name', 'path-count', ('path',))),
      Query(['g0-0'], forward, name('g2-2'), pathlimit=3,
            result=('name', 'path-count', ('path', 'via'))),
      Query(['g0-0'], forward, name('g7-7'), result=('path-count',)),
      Query(['g0-0'], forward, name('g7-7'), pathlimit=2,
            result=('steps', 'path-count', ('path',))),
      Query(['g0-0'], forward, steps=[('=', 3)],
            result=('name', 'path-count', 'path', ('path',))),
      Query(['g4-4'], both, steps=[('=', 2)], pagesize=3,
            result=('name', 'path-count', ('path', 'via', 'steps'))),
  ]
  return generate('path-grid', grid_tests.__doc__, m, qs, Graphd(ARGS.graphd))


def semantics_tests():
  """Small graphs for the finer points.

  - A cycle: the start is never a match, though a step leads back to it.
  - Constraints on the <-path constraint pick matches; they don't limit
    the nodes a path goes through.
  - Constraints on a step's root say which nodes a step can start from:
    a node with a value ("closed") can be reached, not passed through.
  - Ties: of two equally short paths, the one through the lower guid,
    whatever the names.
  - Versioned and deleted links: only live links are steps; asof sees
    the graph as it was.
  - "steps" bounds the shortest distance, not the length of any walk.
  - Several starts, each with its own matches; optional.
  """
  m = Model()
  m.node('to')
  for n in ['a', 'b', 'c', 'd']:
    m.node(n)
  m.link('ab', 'to', 'a', 'b')
  m.link('bc', 'to', 'b', 'c')
  m.link('ca', 'to', 'c', 'a')
  m.link('cd', 'to', 'c', 'd')
  # Ties: s reaches t through z (written first: the lower guid) and
  # through y.
  for n in ['s', 'z', 'y', 't']:
    m.node(n)
  m.link('sy', 'to', 's', 'y')
  m.link('sz', 'to', 's', 'z')
  m.link('yt', 'to', 'y', 't')
  m.link('zt', 'to', 'z', 't')
  # Closed nodes.
  m.node('o1')
  m.node('x', value='closed')
  m.node('o2')
  m.node('o3')
  m.link('o1x', 'to', 'o1', 'x')
  m.link('xo2', 'to', 'x', 'o2')
  m.link('o1o3', 'to', 'o1', 'o3')
  m.link('o3o2', 'to', 'o3', 'o2')
  m.link('o2o3', 'to', 'o2', 'o3')
  # History: h1 -> h2 -> h3, then h2's link moves to h4, then h1's link
  # is deleted.
  for n in ['h1', 'h2', 'h3', 'h4']:
    m.node(n)
  m.link('h12', 'to', 'h1', 'h2')
  m.link('h23', 'to', 'h2', 'h3')
  before = m.now()
  m.relink('h23', 'h4')
  moved = m.now()
  m.delete('h12')

  t = m.g('to')
  fwd = Step('result=(contents) (<-left typeguid=%s result=((name)) right->(*))' % t,
             out_links('to'))
  open_fwd = Step('value=null result=(contents) (<-left typeguid=%s result=((name)) '
                  'right->(*))' % t,
                  only_from(out_links('to'),
                            lambda m, x: m.nodes[x]['value'] is None))
  name = lambda n: ('name=%s' % q(n), lambda m, y: y == n)
  qs = [
      Query(['a'], fwd, result=('name', 'steps', 'path')),
      Query(['a'], fwd, name('a')),
      Query(['a'], fwd, name('d')),
      Query(['a'], fwd, ('(<-left typeguid=%s)' % t,
                         lambda m, y: any(l['left'] == y and l['type'] == 'to'
                                          for l in m.live())),
            result=('name', 'steps')),
      Query(['s'], fwd, name('t')),
      Query(['o1'], fwd, result=('name', 'steps', 'path')),
      Query(['o1'], open_fwd, result=('name', 'steps', 'path')),
      Query(['x'], open_fwd),
      Query(['h1'], fwd, result=('name', 'path')),
      Query(['h2'], fwd, result=('name', 'path')),
      Query(['h1'], fwd, result=('name', 'path'), asof=before),
      Query(['h1'], fwd, result=('name', 'path'), asof=moved),
      Query(['a'], fwd, steps=[('>=', 2)], result=('name', 'steps')),
      Query(['a'], fwd, steps=[('=', 3)]),
      Query(['a', 's', 'h3'], fwd, steps=[('=', 1)], result=('name',)),
      Query(['a', 's', 'h3'], fwd, steps=[('=', 1)], optional=True,
            result=('name',)),
      # Matches named by guid=: graphd checks those through the
      # constraint's iterator, not one by one.
      Query(['a'], fwd, ('guid=%s' % m.g('c'), lambda m, y: y == 'c'),
            result=('name', 'steps')),
      Query(['a'], fwd, ('guid=(%s %s)' % (m.g('b'), m.g('d')),
                         lambda m, y: y in ('b', 'd')),
            result=('name', 'steps')),
      # The same questions, named by guid=, from both ends.
      Query(['a'], fwd, by_guid(m, 'd')),
      Query(['a'], fwd, by_guid(m, 'a')),
      Query(['s'], fwd, by_guid(m, 't'),
            result=('name', 'path', 'path-count', ('path', 'via'))),
      Query(['o1'], fwd, by_guid(m, 'o2'), result=('name', 'path')),
      Query(['o1'], open_fwd, by_guid(m, 'o2'), result=('name', 'path')),
      Query(['o1'], open_fwd, by_guid(m, 'x'), result=('name', 'path')),
      Query(['x'], open_fwd, by_guid(m, 'o2')),
      Query(['h1'], fwd, by_guid(m, 'h3'), result=('name', 'path')),
      Query(['h1'], fwd, by_guid(m, 'h3'), result=('name', 'path'),
            asof=before),
      Query(['h1'], fwd, by_guid(m, 'h4'), result=('name', 'path'),
            asof=moved),
      Query(['h1'], fwd, by_guid(m, 'h3'), result=('name', 'path'),
            asof=moved),
      Query(['a'], fwd, by_guid(m, 'd'), steps=[('>=', 4)]),
      Query(['a'], fwd, by_guid(m, 'd'), steps=[('=', 3)],
            result=('name', 'steps')),
      Query(['a', 's'], fwd, by_guid(m, 't'), optional=True,
            result=('name', 'path')),
      # Both of s's paths to t, z's first; path is the first of them.
      Query(['s'], fwd, name('t'),
            result=('name', 'path', 'path-count', ('path', 'via'))),
      Query(['s'], fwd, name('t'), pathlimit=1,
            result=('name', 'path-count', ('path', 'via'))),
      Query(['s'], fwd, name('t'), pathlimit='all',
            result=('name', 'path-count', ('steps', 'path'))),
  ]
  return generate('path-semantics', semantics_tests.__doc__, m, qs,
                  Graphd(ARGS.graphd))


def main():
  global ARGS
  parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  parser.add_argument('--graphd', default=os.path.join(TOP, 'out', 'bin',
                                                       'graphd'))
  ARGS = parser.parse_args()
  for make in [tree_tests, coauthor_tests, grid_tests, semantics_tests]:
    name = make.__name__.replace('_tests', '').replace('_', '-')
    n, checks = make()
    print('path-%s: %d queries, %d checks against graphd' % (name, n, checks))


if __name__ == '__main__':
  main()
