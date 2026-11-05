# Path search in graphd: a proposal

Status: implemented (`graphd/graphd-read-path.c`), searching from both
ends when the step is a chain, or an "or" of chains, and only a few
nodes can match, and otherwise from one end, stopping early when a page of
the nearest matches is all the results need. Resuming a search with a
cursor is still to come; MQL syntax is proposed in pymql's
`doc/path-proposal.md`.

## Why

Questions like "how is this author connected to that one?" need a
shortest path through the graph. A client can find one by breadth-first
search, sending a read per level, but that moves hundreds of thousands
of guids per level from graphd to the client, and then either goes
through MQL (slow: most of the time is spent building nested results)
or around it to graphd directly (a layering violation). On dblp, one
two-hop co-author level from Michael Stonebraker reaches 50,000 people:
10 s through MQL, 2 s reading graphd directly. graphd already evaluates
each level well, with a guid-set constraint; what it lacks is a way to
repeat a constraint.

## The idea

graphd queries are templates: each parenthesized constraint matches
primitives, and nesting joins them through a linkage (`<-left`,
`<-right`, `left->(...)`, `right->(...)`, `(<- ...)`, `(-> ...)`). One
step of a path is already a template. From an author A to A's
co-authors, through papers:

```
(guid=A
   (<-right typeguid=AUTHOR            ; links pointing at A: A is an author of ...
      left->(                          ; ... the paper at their left
         (<-left typeguid=AUTHOR       ; the paper's author links
            right->( ... ) ))))        ; ... and the authors they point at
```

A path is that step, repeated. The proposal adds:

1. **`<-path`**, a new linkage: a constraint linked to its parent by
   `<-path` matches the nodes the parent reaches by one or more steps.
   It goes where `<-left` or `<-right` would.
2. **`step=( constraints )`**, on the `<-path` constraint: the template
   for one step. Its root is the node the step starts from, so its links
   are subconstraints, written as they would be under that node; inside
   it, **`*`** marks the node where the step ends and the next starts.
3. **`steps`** comparisons bounding the number of steps: `steps<=10`,
   `steps>=2`, `steps=3` (like `count` and `newest`).
4. Three **result atoms**: `path`, the guids of the nodes along the
   path, from the parent to the match; `steps`, their number (as the
   `steps` constraint counts them, just as the `count` atom and the
   `count` constraint agree); and `via`, one element per step: what the
   step template returns for that step (see Semantics).
5. **Lists of paths**, for a match's other shortest paths: a third level
   of list in the `<-path` constraint's result, `((name (path via)))`,
   holds the match's shortest paths, each as the list says; with
   **`pathlimit=N`** or **`pathlimit=all`** saying how many, and a
   `path-count` atom for how many there are.

Everything else is ordinary graphd: the `<-path` constraint's other
constraints (`guid=`, `type=`, `value=`, subconstraints, variables) say
which reachable nodes it matches, and `pagesize`, `cursor`, `countlimit`,
`count`, `sort`, `optional` and `$variables` work as they do on any
constraint.

## Grammar

```
ArrowConstraint   <-- `<-' [ `left' | `right' | `scope' | `typeguid' | `path' ]
StepConstraint    <-- `step' `=' `(' Constraints `)'
StepEndConstraint <-- `*'
StepsConstraint   <-- `steps' ( `<' | `<=' | `=' | `>=' | `>' ) NUM
PathLimitConstraint <-- `pathlimit' `=' ( NUM | `all' )
Pattern atoms     +=  `path' | `steps' | `via' | `path-count'
```

In a `<-path` constraint's result, a list may hold a third level of
list, a list of paths (see below); everywhere else, results nest lists
two levels deep at most, as before.

`*` is a new token. All of this is allowed only in read requests.

## Examples

The shortest co-author path from Stonebraker to Knuth, with a paper for
each step:

```
read (guid=STONEBRAKER result=((name contents))
   (<-path
      step=(result=(contents)
              (<-right typeguid=AUTHOR result=((contents))
                 left->(result=((name))
                    (<-left typeguid=AUTHOR result=none right->(*)))))
      steps<=10
      guid=KNUTH
      result=((path via steps))))
```
```
ok (("Michael Stonebraker"
     (((STONEBRAKER SUCIU PAPADIMITRIOU KNUTH)
       (((((("On XML and Databases"))) ((("The Seattle Report"))) ...)))
        ...)
       3))))
```

The step's `result=` patterns follow graphd's usual rules: a
constraint's results reach its parent only through `contents`, and
`((...))` returns one entry per match, so this step returns every paper
the two authors share.

Direct links either way, except types, keys, names and permissions,
which every object has; alternatives use graphd's `{ } | { }`:

```
step=( { (<-left typeguid!=(TYPE KEY NAME PERMISSION) value=null right->(*)) }
     | { (<-right typeguid!=(TYPE KEY NAME PERMISSION) left->(*)) } )
```

Everyone within two co-author steps, nearest first, a page at a time,
with their distance:

```
(guid=A (<-path step=(...) steps<=2 $d=steps pagesize=100 cursor=... result=((guid $d))))
```

The nearest author with an ACM award: the target is a pattern, not a
guid.

```
(guid=A (<-path step=(...) pagesize=1
           (<-left typeguid=AWARD value~="ACM*")
           result=((path))))
```

How many people are within three steps: `$n=count`, or `result=(count)`,
on the `<-path` constraint, as anywhere else.

Every shortest path from A to B, and how many there are:

```
read (guid=A result=((name contents))
   (<-path step=(...) guid=B pathlimit=all
      result=((path-count (path via)))))
```
```
ok (("A" ((3 (((A X B) (VIA1 VIA2)) ((A Y B) (VIA3 VIA4)) ((A Z B) (...)))))))
```

## Several paths per match

A match can have many shortest paths: in the 8 by 8 grid of the tests,
3,432 from one corner to the other. By default a match has one, its
first; for more, the `<-path` constraint's result holds a list of
paths.

- **A list of paths** is a third level of list in the result:
  `result=((name (path via)))` returns, for each match, its name and a
  list with an element for each of its shortest paths, here that path's
  nodes and its `via`. It holds only `path`, `via` and `steps`, and a
  result has at most one. Elsewhere in the result, `path`, `via` and
  `steps` are the match's first path, as without a list.
- **`pathlimit=N`** returns the first N of a match's shortest paths in
  the list; **`pathlimit=all`**, all of them; without it, the first
  1,000 (graphd's default page size). `pathlimit` needs a list of
  paths to limit.
- **`path-count`**, a result atom, is how many shortest paths a match
  has, whatever the `pathlimit`.
- **Order.** A match's shortest paths are in order of the guid of the
  node before the match, then of the node before that, and so on back
  to the start; the first is the one the Ties rule picks.
- **Only shortest paths.** Longer paths (the shortest few, or all those
  within some number of steps of the shortest) would be an extension:
  another constraint, like `slack=N`, could admit them to the list.
- **MQL.** MQL calls a limit `limit`; it would write the list of paths
  as a list in the query, with its own `"limit"`, compiled to
  `pathlimit`. `pathlimit` is graphd's name for it, as `countlimit` is
  graphd's for counting: graphd's `pagesize` on the `<-path` constraint
  already limits its matches, so the paths of each match need a name of
  their own.
## Semantics

- **Matches.** A node matches if it can be reached in one or more
  steps and meets the `<-path` constraint's own constraints. Those
  constraints pick matches; they don't restrict the nodes a path passes
  through. The start never matches, even when a cycle leads back to it.
- **Distance.** A match's `steps` is its shortest distance, and the
  `steps` constraint bounds that: with `steps>=2`, a neighbor isn't a
  match even if a longer walk also reaches it.
- **Order.** Matches come in order of distance, then guid. Paging
  (`pagesize`, `cursor`) is over that order, so the shortest path to one
  node is `guid=B pagesize=1`. `count` counts what is returned, as it
  does for any constraint with a `pagesize`.
- **Ties.** Each match has one path: of its shortest paths, the one
  whose node before the match has the lowest guid, and so on back to
  the start. (A breadth-first search that takes each level's nodes in
  guid order and keeps the first predecessor it finds does this.)
- **via.** A step's element of `via` is the reply graphd gives to the
  step template read with its root bound to the step's start and `*`
  bound to its end: everything after `ok`, a list of the root's
  results. So it shows every way the step can be taken between those
  two nodes (every shared paper), and for a step with alternatives,
  which alternative matched.
- **Where steps start.** Constraints on the step template's root say
  which nodes a step can start from, the start included. A node that
  fails them can still be reached and matched, but not passed through.
- **Several starts.** When the parent matches several nodes, each has
  its own matches, as with any subconstraint; a start with no matches
  drops out unless the `<-path` constraint is `optional`.
- **Both ends.** When the step is a chain, or an "or" of chains (below), and the `<-path`
  constraint can match only a few nodes - it names them with `guid=`,
  or graphd's own iterator for it holds at most 64, as with a name, an
  indexed value, or "the authors of this paper" - graphd searches from
  both ends at once, from the start and from each of those nodes in
  turn: a chain is followed backwards by flipping each linkage, and an
  "or" of chains by following each back. That changes only the cost,
  not the answer.
- **Stopping early.** Otherwise the search goes from one end. If the
  results are just a page of the nearest matches - an explicit
  `pagesize`, the default order, no cursor, and no subconstraints on
  the `<-path` constraint to rule matches out later - it stops at the
  end of the first level by which it has found a page. With none of
  these, a search with no `steps<=N` walks everything reachable from
  the start, which on a large graph can take minutes: give it a bound.
- **Limits.** The request's cost allowance (`cost="tu=..."`) bounds the
  search; past it, the read fails with `TOOHARD`, as any other read
  would. `steps<=N` bounds the depth.
- **Time.** `asof`, `live` and `newest` apply to the step as to any
  constraint: only live links are steps, a versioned link is followed
  to where its newest version points, and "the shortest path as of
  2015" needs nothing new.
- **Steps.** A step template is evaluated as a subconstraint is, with
  its root bound to the current frontier, a guid set: the cost of a
  level is the cost of today's join over that set.
- **Alternatives.** A step's `{ } | { }` alternatives are different
  ways to step, and a step goes every way that matches. (Elsewhere, an
  "or" is satisfied by its first alternative whose subconstraints
  match, and graphd doesn't look at the rest; so walking evaluates the
  step once for each combination of its alternatives, and a step can
  have at most 8 "or"s.) `via`, the step evaluated with both its ends
  bound, shows the first alternative that matches, as any read would.
- **Paths.** `path` begins with the start and ends with the match. A
  step back to a node already reached is ignored, so paths don't
  revisit nodes.

## Checks (implemented)

- `<-path` needs a parent, and a `step=(...)`; it is the constraint's
  linkage, so it can't be combined with `<-left` and the like.
- A step is rooted at the node it starts from: `step=(<-right ...)`, a
  step rooted at a link, is an error that suggests `step=((<-right ...))`.
- A step contains a `*`, and `*` appears only in a step. `*` stands
  alone: what the next node must be is said by the next step, or by the
  `<-path` constraint itself.
- A step cannot contain another `<-path`, and a `<-path` cannot be one
  of an "or"'s alternatives. (It can be a subconstraint of anything
  else, including another `<-path` constraint.)
- `step=`, `steps`, `pathlimit` and the `path`, `via`, `steps` and
  `path-count` results are only for `<-path` constraints.
- A list of paths holds only `path`, `via` and `steps`; a result has at
  most one; `pathlimit` is a number of at least 1, or `all`, and needs a
  list of paths in the result.

## Decisions taken (open to change)

- **`step=` goes on the `<-path` constraint**, where its linkage is.
- **One shortest path per match, unless the result asks for a list of
  them.** A list of paths is a third level of result list, rather than
  a result row per path, so each match stays one result with its paths
  inside it, and paging and counting stay over matches.
- **`path`, `via` and `steps` result atoms**, rather than per-step
  values collected through `$variables`; the step's own `result=`
  patterns already say what each step returns. (An earlier draft had
  `steps` return the per-step results; it is now the number, matching
  the `steps` constraint, and the per-step results are `via`.)
- **`*`** rather than a keyword: `next`, `start` and `end` already mean
  other things in graphd (the next version of a primitive, dump paging).

## Alternatives considered

- **A separate request, `path (from=A to=B step=(...))`.** Simpler to
  implement, but not composable: it couldn't be nested, filtered by
  pattern, paged or counted like other constraints, and MQL couldn't
  embed it in a larger query.
- **Repetition inline**, as in regular expressions or Cypher
  (`(<-right ... (*1..10 ...))`). More compact, but graphd has no such
  operators, and the extent of the repeated part is hard to see in a
  nested template.

## Tests

`test/unit/path-parse` covers the syntax and its errors. Four more test
evaluation, on graphs too large to work out by hand: a binary tree
(`path-tree`), random co-authorship (`path-coauthor`), an 8 by 8 grid
(`path-grid`) and small graphs for the finer points above, including
cycles, ties, closed nodes and link history (`path-semantics`); all four
include lists of paths, and the grid's path counts are checked against
their closed form.

`test/unit/path-cases` covers by hand what the generator doesn't:
`guid=` naming nothing, only the start, or the start among others;
`<-path` constraints side by side, below another subconstraint, and
inside another's match; "or"s; other sorts, variables and cursors;
counts.

`test/path/make-tests.py` generates the others from a model of each graph and
a breadth-first search that follows this document, and checks the model
against graphd as it is today: the guid of every write; every step of
every expected path, read with its ends bound (that reply is also its
`via`); and every distance, by nesting the step template that many
times (it must reach the match) and one fewer (it must not). They pass
since evaluation was implemented.

## Implementation

`graphd/graphd-read-path.c` has the overview. In short:

- **Two copies of each step.** graphd can't copy a constraint tree, so
  the parser records a step's tokens and parses them twice: `con_step`
  keeps the query's result patterns, for `via`; `con_step_walk` is
  rewritten to walk, its root returning its guid and contents, its `*`
  its guid, and nothing paged short.
- **Compiling.** `<-path` constraints aren't linked to their parents
  the way other subconstraints are, so `graphd_read_push()` compiles
  them, and their two templates, as trees of their own.
- **Searching.** When `graphd_read_one()` reaches a `<-path`
  subconstraint, it pushes a path search instead of a read-set. The
  search walks level by level and records each new node's distance and
  predecessors. If the step is a chain (from the root to `*`, each
  constraint linked to its one subconstraint, mandatory, with no "or"s),
  or an "or" of chains, the walk follows it directly: through the linkage indexes, or a
  primitive's own pointers, checking each primitive against its
  constraint's intrinsics (which include `live`, versions and `asof`),
  and remembering what each node part way along the chain reaches. On
  dblp, the 53,252 people within two co-author steps of one author take
  0.2 s this way, against 4.6 s through the read machinery.
  An "or" of chains is a step whose root has one `|` "or" (not `||`),
  nested for three or more alternatives, whose branches hold all of the
  root's subconstraints, each branch a chain; a branch's own
  constraints, and the root's, apply to the node the step starts from.
  The walk follows every branch from each node and takes the union, as
  the template does: `{(<-left typeguid=T right->(*))} |
  {(<-right typeguid=T left->(*))}`, either way along a link, is two
  one-link chains. On dblp, either way along author links between
  Stonebraker and Knuth takes 0.07 s warm, 2.8 s cold; before, the
  general walk had not finished after four and a half minutes.
  `test/unit/path-cost` checks that such steps take the chain walk.
  Other steps
  are walked by evaluating the walking template with its root bound to
  up to 1,000 frontier nodes at a time (a fixed iterator). It then
  evaluates the `<-path` constraint as an ordinary read-set over the
  nodes reached, with an implicit `sort=(steps guid)`, so its own
  constraints, paging, sort and count work as for any constraint.
- **Both ends.** With a chain and a few possible matches, the search
  takes each in turn: a search back from it and the (shared) search
  from the start grow whichever has the smaller frontier, a level at a
  time, until one reaches a node the other has. Then it puts together
  the shortest paths from the start to that match: a node within the
  forward search's radius has the predecessors that search found; one
  beyond it has those the backward search found one step further back,
  if they in turn reach the start at the right distance. A node on
  paths to several matches has the same predecessors in each. When all
  are done, the paths found replace the forward search's nodes, and the
  rest is as for one end.
- **Results.** `path`, `steps`, `path-count` and lists of paths are
  computed from the search's state (`graphd_read_path_value()`); a list
  of paths is turned into a single pattern atom, so the rest of graphd
  sees two levels of list. Before delivering a match, `graphd_read_one()`
  computes the `via` of its paths' steps: the query's template, its root
  bound to the step's start and its `*` to the step's end.
- **Limits.** A template constraint pages through at most 65,536
  results for each node, graphd's limit on an explicit page size, so a
  node with more links than that in one step is walked partly.

### Memory, errors and load

- A path search keeps no pointers into the primitive table across
  turns, so graphd can suspend it (to let a write through) and resume
  it; the `via` values it caches are copied when it is suspended. Its
  turns read at most 20,000 primitives, so that graphd, which checks a
  request's cost allowance between turns, can stop it.
- Built with `-DGRAPHD_PATH_CHECK_MEMORY`, graphd tracks every block
  and hash table the path module allocates on a request's heap, and
  aborts, naming where each came from, if any is left when the request
  ends. With AddressSanitizer too, the whole test suite passes:

      make BUILDDIR=out-check \
          CFLAGS="-g -O1 -fsanitize=address -fno-omit-frame-pointer -w \
                  -DGRAPHD_PATH_CHECK_MEMORY" \
          LDFLAGS=-fsanitize=address
      BUILDDIR=out-check test/run-tests.sh

- `test/stress/stress.py` (see `doc/memory-and-load-testing.md`) runs
  path searches, among every other kind of read, while other
  connections write, so that graphd suspends them part way, and fails
  if an answer changes or graphd reports anything.

## Implementation plan

1. Parser (done): the lexer's `*` token; `<-path`, `step=`, `*`,
   `steps` and `pathlimit` in libgdp; the new constraint clauses, fields
   (`con_path`, `con_step`, `con_step_end`, `con_steps`,
   `con_pathlimit`) and pattern types (`GRAPHD_PATTERN_PATH`,
   `GRAPHD_PATTERN_STEPS`, `GRAPHD_PATTERN_VIA`,
   `GRAPHD_PATTERN_PATH_COUNT`) in graphd; the checks above;
   `test/unit/path-parse.sh`. Tests for evaluation (done).
2. Evaluation, one end (done): see Implementation above; the four
   evaluation tests pass. Between dblp authors three or four co-author
   steps apart it takes 13 s to two minutes, mostly reading from disk:
   from one end, the search reaches most of the graph.
3. Cost and paging: charge the search to the request's cost allowance;
   cursors that resume a search.
4. Both ends (done, for chains and "or"s of chains): between the same dblp
   authors, 0.6 s with a cold cache, 0.1-0.2 s warm (Stonebraker to
   Knuth: 133 s from one end; 158 thousand primitives read instead of
   74 million). To all eight authors of one paper, from authors two to
   four steps away, 0.9-2.6 s; to the nearest of them, 0.2-0.3 s; the
   three nearest people, a page stopped early, 0.01 s.
5. MQL: proposed, not implemented, in pymql's `doc/path-proposal.md`:
   a `"reach"` directive, with steps written as properties (`"P"`,
   `"!P"`, `"step|=": [...]` for alternatives) and targets as ordinary
   subqueries, compiled to `<-path`.
