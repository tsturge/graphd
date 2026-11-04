# Path search in graphd: a proposal

Status: the syntax parses (branch `path-search`); evaluation is not
implemented yet, and a well-formed path query fails with `SEMANTICS
path search (<-path) is not implemented yet`.

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
- **Both ends.** When the `<-path` constraint has a single `guid=` and
  the step can be reversed (by flipping each linkage in the template:
  graphd can do this mechanically), graphd searches from both ends at
  once. That changes only the cost, not the answer.
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
- A step cannot contain another `<-path`.
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

`test/path/make-tests.py` generates them from a model of each graph and
a breadth-first search that follows this document, and checks the model
against graphd as it is today: the guid of every write; every step of
every expected path, read with its ends bound (that reply is also its
`via`); and every distance, by nesting the step template that many
times (it must reach the match) and one fewer (it must not). They are
listed in `test/unit/pending`, which `make test` reports separately,
until evaluation is implemented.

## Implementation plan

1. Parser (done): the lexer's `*` token; `<-path`, `step=`, `*`,
   `steps` and `pathlimit` in libgdp; the new constraint clauses, fields
   (`con_path`, `con_step`, `con_step_end`, `con_steps`,
   `con_pathlimit`) and pattern types (`GRAPHD_PATTERN_PATH`,
   `GRAPHD_PATTERN_STEPS`, `GRAPHD_PATTERN_VIA`,
   `GRAPHD_PATTERN_PATH_COUNT`) in graphd; the checks above;
   `test/unit/path-parse.sh`. Tests for evaluation (done, pending).
   Evaluation will also need graphd's two-level limit on result lists
   (`parenthesize_pattern`) to allow a list of paths.
2. Evaluation, one end: a read-set for `<-path` constraints that keeps a
   frontier and a visited set, evaluates the step template with its root
   bound to the frontier, and yields matches level by level, with each
   node's predecessor, for `path`, `via` and `steps`.
3. Cost and paging: charge the search to the request's cost allowance;
   cursors that resume a search.
4. Both ends: reverse a step template, and search from both ends when
   the match is a single guid.
5. MQL: expose paths, perhaps as
   `"/type/reflect/path": {"via": P, "to": Q, "target": {...}}`,
   compiled to the template above. A separate design pass.
