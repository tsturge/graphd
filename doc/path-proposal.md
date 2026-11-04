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
4. Two **result atoms**: `path`, the guids of the nodes along the path,
   from the parent to the match; and `steps`, one element per step,
   each what the step template's own `result=` patterns return.

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
Pattern atoms     +=  `path' | `steps'
```

`*` is a new token. All of this is allowed only in read requests.

## Examples

The shortest co-author path from Stonebraker to Knuth, with a paper for
each step:

```
read (guid=STONEBRAKER
   (<-path
      step=((<-right typeguid=AUTHOR result=none
                left->(result=guid
                   (<-left typeguid=AUTHOR result=none right->(*)))))
      steps<=10
      guid=KNUTH
      result=((path steps))))
```
```
ok ((((STONEBRAKER SUCIU PAPADIMITRIOU KNUTH) ((PAPER1) (PAPER2) (PAPER3))))))
```

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

## Semantics

- **Order.** Matches come in order of their number of steps, as a
  breadth-first search finds them, and each match has one shortest path.
  Paging (`pagesize`, `cursor`, `countlimit`) is over that order, so the
  shortest path to one node is `guid=B pagesize=1`.
- **Both ends.** When the `<-path` constraint has a single `guid=` and
  the step can be reversed (by flipping each linkage in the template:
  graphd can do this mechanically), graphd searches from both ends at
  once. That changes only the cost, not the answer.
- **Limits.** The request's cost allowance (`cost="tu=..."`) bounds the
  search; past it, the read fails with `TOOHARD`, as any other read
  would. `steps<=N` bounds the depth.
- **Time.** `asof`, `live` and `newest` apply to the step as to any
  constraint: "the shortest path as of 2015" needs nothing new.
- **Steps.** A step template is evaluated as a subconstraint is, with
  its root bound to the current frontier, a guid set: the cost of a
  level is the cost of today's join over that set.
- **The parent.** The search starts from each node the parent matches;
  `path` begins with that node. A step back to a node already reached is
  ignored, so paths don't revisit nodes.

## Checks (implemented)

- `<-path` needs a parent, and a `step=(...)`; it is the constraint's
  linkage, so it can't be combined with `<-left` and the like.
- A step is rooted at the node it starts from: `step=(<-right ...)`, a
  step rooted at a link, is an error that suggests `step=((<-right ...))`.
- A step contains a `*`, and `*` appears only in a step. `*` stands
  alone: what the next node must be is said by the next step, or by the
  `<-path` constraint itself.
- A step cannot contain another `<-path`.
- `step=`, `steps` and the `path` and `steps` results are only for
  `<-path` constraints.

## Decisions taken (open to change)

- **`step=` goes on the `<-path` constraint**, where its linkage is.
- **One shortest path per match.** Returning all shortest paths (dblp
  often has many) could be a later `result=` atom or a flag.
- **`path` and `steps` result atoms**, rather than per-step values
  collected through `$variables`; the step's own `result=` patterns
  already say what each step returns.
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

## Implementation plan

1. Parser (done): the lexer's `*` token; `<-path`, `step=`, `*` and
   `steps` in libgdp; the new constraint clauses, fields
   (`con_path`, `con_step`, `con_step_end`, `con_steps`) and pattern
   types (`GRAPHD_PATTERN_PATH`, `GRAPHD_PATTERN_STEPS`) in graphd; the
   checks above; tests in `test/unit/path-parse.sh`.
2. Evaluation, one end: a read-set for `<-path` constraints that keeps a
   frontier and a visited set, evaluates the step template with its root
   bound to the frontier, and yields matches level by level, with each
   node's predecessor and step result for `path` and `steps`.
3. Cost and paging: charge the search to the request's cost allowance;
   cursors that resume a search.
4. Both ends: reverse a step template, and search from both ends when
   the match is a single guid.
5. MQL: expose paths, perhaps as
   `"/type/reflect/path": {"via": P, "to": Q, "target": {...}}`,
   compiled to the template above. A separate design pass.
