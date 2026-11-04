/*
Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
#include "graphd/graphd.h"
#include "graphd/graphd-read.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*  Path search: "<-path" constraints (doc/path-proposal.md).
 *
 *  A <-path constraint matches the nodes its parent reaches by one or
 *  more steps, each step a template rooted at the node it starts from,
 *  with "*" where it ends.  Evaluating one, for one parent, is a
 *  breadth-first search from the parent:
 *
 *  1.  Walk: one level at a time, evaluate the step template with its
 *      root bound to the frontier, and record, for each node reached
 *      for the first time, its distance and its predecessors (the
 *      nodes of the previous level that reach it).  The template used
 *      is con_step_walk, a second copy of the step, rewritten to
 *      return the guids reached at "*" and to page through everything.
 *
 *  2.  Match: evaluate the <-path constraint itself as an ordinary
 *      subconstraint, with the nodes reached as its candidates.  Its
 *      own constraints pick the matches; sort, paging and counting work
 *      as anywhere.  The path, steps, path-count and via results, and
 *      lists of paths, come from the search's state (con_path_run).
 *
 *  3.  via: a step's via is con_step, the query's own copy of the
 *      template, evaluated with its root bound to the step's start and
 *      its "*" to its end.  graphd_read_path_via_push() computes those
 *      for a match before graphd_read_one() delivers its results.
 */

/*  Frontier nodes per evaluation of the walking template.
 */
#define GRAPHD_PATH_CHUNK 1000

/*  Most results a template constraint pages through: graphd's limit
 *  on an explicit page size.
 */
#define GRAPHD_PATH_PAGESIZE (64 * 1024)

/*  Without pathlimit, a list of paths holds this many.
 */
#define GRAPHD_PATH_LIMIT_DEFAULT 1000

/*  Most "or"s in a step: walking a step evaluates it once for each
 *  combination of their alternatives.
 */
#define GRAPHD_PATH_ORS_MAX 8

/*  A node the search has reached.
 */
typedef struct graphd_path_node {
  unsigned long pn_dist;

  /*  The nodes one step closer to the start that reach this one,
   *  lowest id first.
   */
  pdb_id *pn_preds;
  size_t pn_preds_n;
  size_t pn_preds_m;

  /*  The number of shortest paths here, if counted, or 0.
   */
  unsigned long long pn_count;

} graphd_path_node;

/*  The state of one search: from one parent, for one <-path constraint.
 */
struct graphd_path_run {
  pdb_id pr_start;

  /*  pdb_id -> graphd_path_node
   */
  cm_hashtable pr_nodes;

  /*  (pdb_id, pdb_id) -> graphd_value: each step's via.
   */
  cm_hashtable pr_via;
};

typedef struct graphd_read_path_context {
  graphd_stack_context grp_sc;
  graphd_read_base *grp_base;

  graphd_constraint *grp_con;
  pdb_id grp_parent_id;
  pdb_primitive const *grp_parent_pr;

  graphd_read_set_callback *grp_callback;
  void *grp_callback_data;

  graphd_path_run *grp_run;

  /*  The current level, and the nodes reached last.
   */
  unsigned long grp_level;
  pdb_id *grp_frontier;
  size_t grp_frontier_n;
  size_t grp_frontier_m;
  size_t grp_chunk;
  size_t grp_chunk_n;

  /*  Which combination of the step's alternatives is next.
   */
  unsigned long grp_combo;

  /*  Nodes first reached in this level.
   */
  pdb_id *grp_next;
  size_t grp_next_n;
  size_t grp_next_m;

  /*  The constraint's state before this search, restored after.
   */
  graphd_path_run *grp_saved_run;
  pdb_iterator *grp_saved_it;

  int grp_err;

} graphd_read_path_context;

/* ---------------------------------------------------------------- */
/*  Semantics                                                        */

static graphd_pattern *list_of(graphd_request *greq, int a, int b) {
  graphd_pattern *outer, *inner;

  if ((outer = graphd_pattern_alloc(greq, NULL, GRAPHD_PATTERN_LIST)) == NULL ||
      (inner = graphd_pattern_alloc(greq, outer, GRAPHD_PATTERN_LIST)) ==
          NULL ||
      graphd_pattern_alloc(greq, inner, a) == NULL ||
      (b != GRAPHD_PATTERN_UNSPECIFIED &&
       graphd_pattern_alloc(greq, inner, b) == NULL))
    return NULL;
  return outer;
}

/*  Apply <f> to every constraint of the template rooted at <con>.
 */
static int template_each(graphd_request *greq, graphd_constraint *root,
                         graphd_constraint *con,
                         int (*f)(graphd_request *, graphd_constraint *,
                                  graphd_constraint *)) {
  graphd_constraint *sub;
  graphd_constraint_or *cor;
  int err;

  if ((err = (*f)(greq, root, con)) != 0) return err;
  for (sub = con->con_head; sub != NULL; sub = sub->con_next)
    if ((err = template_each(greq, root, sub, f)) != 0) return err;
  for (cor = con->con_or_head; cor != NULL; cor = cor->or_next) {
    if ((err = template_each(greq, root, &cor->or_head, f)) != 0) return err;
    if (cor->or_tail != NULL &&
        (err = template_each(greq, root, cor->or_tail, f)) != 0)
      return err;
  }
  return 0;
}

/*  The walking template: the root returns its guid and contents, "*"
 *  its guid, everything else its contents; everything is paged through.
 */
static int walk_rewrite(graphd_request *greq, graphd_constraint *root,
                        graphd_constraint *con) {
  con->con_no_defer = true;

  /*  The branches of an "or" have no results of their own; their
   *  subconstraints belong to the prototype, and are rewritten there.
   */
  if (con->con_or != NULL) return 0;

  if (con->con_step_end)
    con->con_result = list_of(greq, GRAPHD_PATTERN_GUID,
                              GRAPHD_PATTERN_UNSPECIFIED);
  else if (con == root)
    con->con_result =
        list_of(greq, GRAPHD_PATTERN_GUID, GRAPHD_PATTERN_CONTENTS);
  else
    con->con_result = list_of(greq, GRAPHD_PATTERN_CONTENTS,
                              GRAPHD_PATTERN_UNSPECIFIED);
  if (con->con_result == NULL) return ENOMEM;

  con->con_sort = NULL;
  con->con_assignment_head = NULL;
  con->con_assignment_tail = &con->con_assignment_head;
  con->con_assignment_n = 0;
  con->con_pagesize_valid = true;
  con->con_pagesize = GRAPHD_PATH_PAGESIZE;
  con->con_resultpagesize_parsed_valid = false;
  con->con_countlimit_valid = false;
  return 0;
}

/*  Remember the "or"s of the walking template.
 */
static int collect_ors(graphd_request *greq, graphd_constraint *root,
                       graphd_constraint *con) {
  graphd_constraint *path = root->con_parent;
  graphd_constraint_or *cor;

  for (cor = con->con_or_head; cor != NULL; cor = cor->or_next) {
    if (path->con_path_ors_n >= GRAPHD_PATH_ORS_MAX) {
      graphd_request_errprintf(greq, false,
                               "SEMANTICS a step=(...) can have at most %d "
                               "\"or\"s",
                               GRAPHD_PATH_ORS_MAX);
      return GRAPHD_ERR_SEMANTICS;
    }
    path->con_path_ors[path->con_path_ors_n++] = cor;
  }
  return 0;
}

static int no_defer(graphd_request *greq, graphd_constraint *root,
                    graphd_constraint *con) {
  con->con_no_defer = true;
  return 0;
}

/*  Prepare a template for evaluation, as graphd prepares a request's
 *  constraints.
 */
static int template_complete(graphd_request *greq, graphd_constraint *root) {
  graphd_constraint *top = greq->greq_constraint;

  graphd_constraint_set_parent(root, NULL);
  greq->greq_constraint = root;
  graphd_semantic_constraint_complete_template(greq, root);
  greq->greq_constraint = top;

  return graphd_request_has_error(greq) ? GRAPHD_ERR_SEMANTICS : 0;
}

/*  In the result, turn a third level of list (a list of paths) into an
 *  atom of its own, so that the rest of graphd sees two levels.
 */
static void path_list_mark(graphd_constraint *con, graphd_pattern *pat,
                           int depth) {
  graphd_pattern *sub;

  if (pat == NULL) return;
  if (pat->pat_type == GRAPHD_PATTERN_LIST) {
    if (++depth == 3) {
      pat->pat_type = GRAPHD_PATTERN_PATH_LIST;
      con->con_path_list = pat;
      return;
    }
    for (sub = pat->pat_list_head; sub != NULL; sub = sub->pat_next)
      path_list_mark(con, sub, depth);
  } else if (pat->pat_type == GRAPHD_PATTERN_PICK)
    for (sub = pat->pat_list_head; sub != NULL; sub = sub->pat_next)
      path_list_mark(con, sub, depth);
}

static bool pattern_has_via(graphd_pattern const *pat) {
  graphd_pattern const *sub;

  if (pat == NULL) return false;
  if (pat->pat_type == GRAPHD_PATTERN_VIA) return true;
  if (pat->pat_type == GRAPHD_PATTERN_LIST ||
      pat->pat_type == GRAPHD_PATTERN_PICK ||
      pat->pat_type == GRAPHD_PATTERN_PATH_LIST)
    for (sub = pat->pat_list_head; sub != NULL; sub = sub->pat_next)
      if (pattern_has_via(sub)) return true;
  return false;
}

/**
 * @brief Prepare the <-path constraints below <con> for evaluation.
 *
 *  Called once their use has been checked, before variable analysis.
 */
int graphd_read_path_semantic(graphd_request *greq, graphd_constraint *con) {
  graphd_constraint *sub;
  graphd_assignment const *a;
  int err;

  for (sub = con->con_head; sub != NULL; sub = sub->con_next)
    if ((err = graphd_read_path_semantic(greq, sub)) != 0) return err;
  if (!con->con_path) return 0;

  con->con_no_defer = true;
  path_list_mark(con, con->con_result, 0);

  con->con_path_via = pattern_has_via(con->con_result) ||
                      pattern_has_via(con->con_sort);
  for (a = con->con_assignment_head; a != NULL; a = a->a_next)
    if (pattern_has_via(a->a_result)) con->con_path_via = true;

  /*  Matches come in order of distance, then guid.
   */
  if (con->con_sort == NULL) {
    con->con_sort = graphd_pattern_alloc(greq, NULL, GRAPHD_PATTERN_LIST);
    if (con->con_sort == NULL ||
        graphd_pattern_alloc(greq, con->con_sort, GRAPHD_PATTERN_STEPS) ==
            NULL ||
        graphd_pattern_alloc(greq, con->con_sort, GRAPHD_PATTERN_GUID) == NULL)
      return ENOMEM;
    con->con_sort_valid = true;
  }

  if (con->con_step_walk == NULL) {
    graphd_request_errprintf(greq, false,
                             "SEMANTICS a step=(...) cannot be nested");
    return GRAPHD_ERR_SEMANTICS;
  }
  con->con_path_ors = cm_malloc(greq->greq_req.req_cm,
                               GRAPHD_PATH_ORS_MAX * sizeof(*con->con_path_ors));
  if (con->con_path_ors == NULL) return ENOMEM;
  con->con_path_ors_n = 0;

  if ((err = template_each(greq, con->con_step_walk, con->con_step_walk,
                           collect_ors)) != 0 ||
      (err = template_each(greq, con->con_step_walk, con->con_step_walk,
                           walk_rewrite)) != 0 ||
      (err = template_each(greq, con->con_step, con->con_step, no_defer)) !=
          0 ||
      (err = template_complete(greq, con->con_step_walk)) != 0 ||
      (err = template_complete(greq, con->con_step)) != 0)
    return err;
  return 0;
}

/* ---------------------------------------------------------------- */
/*  The search's state                                               */

static graphd_path_run *run_create(graphd_request *greq, pdb_id start) {
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_path_run *run;
  graphd_path_node *pn;

  if ((run = cm_zalloc(cm, sizeof(*run))) == NULL) return NULL;
  run->pr_start = start;
  if (cm_hashinit(cm, &run->pr_nodes, sizeof(graphd_path_node), 10) != 0 ||
      cm_hashinit(cm, &run->pr_via, sizeof(graphd_value), 6) != 0)
    return NULL;
  pn = cm_hnew(&run->pr_nodes, graphd_path_node, &start, sizeof start);
  if (pn == NULL) return NULL;
  pn->pn_dist = 0;
  pn->pn_count = 1;
  return run;
}

static void run_destroy(graphd_request *greq, graphd_path_run *run) {
  cm_handle *cm = greq->greq_req.req_cm;
  cl_handle *cl = graphd_request_cl(greq);
  graphd_path_node *pn = NULL;
  graphd_value *val = NULL;

  if (run == NULL) return;
  while ((pn = cm_hnext(&run->pr_nodes, graphd_path_node, pn)) != NULL)
    if (pn->pn_preds != NULL) cm_free(cm, pn->pn_preds);
  while ((val = cm_hnext(&run->pr_via, graphd_value, val)) != NULL)
    graphd_value_finish(cl, val);
  cm_hashfinish(&run->pr_nodes);
  cm_hashfinish(&run->pr_via);
  cm_free(cm, run);
}

static graphd_path_node *run_node(graphd_path_run *run, pdb_id id) {
  return cm_haccess(&run->pr_nodes, graphd_path_node, &id, sizeof id);
}

static int ids_append(cm_handle *cm, pdb_id **ids, size_t *n, size_t *m,
                      pdb_id id) {
  if (*n >= *m) {
    size_t new_m = *m ? 2 * *m : 16;
    pdb_id *new_ids = cm_realloc(cm, *ids, new_m * sizeof(**ids));
    if (new_ids == NULL) return ENOMEM;
    *ids = new_ids;
    *m = new_m;
  }
  (*ids)[(*n)++] = id;
  return 0;
}

static int id_compare(void const *a, void const *b) {
  pdb_id const x = *(pdb_id const *)a, y = *(pdb_id const *)b;
  return x < y ? -1 : x > y;
}

/*  The number of shortest paths to <id>, at most ULLONG_MAX.
 */
static unsigned long long run_count(graphd_path_run *run, pdb_id id) {
  graphd_path_node *pn = run_node(run, id);
  unsigned long long n = 0, c;
  size_t i;

  if (pn == NULL) return 0;
  if (pn->pn_count != 0) return pn->pn_count;
  for (i = 0; i < pn->pn_preds_n; i++) {
    c = run_count(run, pn->pn_preds[i]);
    n = (c > ULLONG_MAX - n) ? ULLONG_MAX : n + c;
  }
  return pn->pn_count = n;
}

/*  Call <f> on <id>'s shortest paths, each from the start to <id>, in
 *  order (by the node before <id>, then the one before that, and so on
 *  back, lowest id first), until it has been called <limit> times or
 *  returns nonzero.
 */
typedef int path_callback(void *data, pdb_id const *path, size_t n);

static int run_paths_rec(graphd_path_run *run, pdb_id id, pdb_id *path,
                         size_t n, size_t pos, size_t *left, path_callback *f,
                         void *data) {
  graphd_path_node *pn = run_node(run, id);
  size_t i;
  int err;

  path[pos] = id;
  if (pos == 0) {
    (*left)--;
    return (*f)(data, path, n);
  }
  for (i = 0; pn != NULL && i < pn->pn_preds_n && *left > 0; i++)
    if ((err = run_paths_rec(run, pn->pn_preds[i], path, n, pos - 1, left, f,
                             data)) != 0)
      return err;
  return 0;
}

static int run_paths(graphd_request *greq, graphd_path_run *run, pdb_id id,
                     size_t limit, path_callback *f, void *data) {
  graphd_path_node *pn = run_node(run, id);
  pdb_id *path;
  size_t left = limit;
  int err;

  if (pn == NULL || limit == 0) return 0;
  path = cm_malloc(greq->greq_req.req_cm, (pn->pn_dist + 1) * sizeof(*path));
  if (path == NULL) return ENOMEM;
  err = run_paths_rec(run, id, path, pn->pn_dist + 1, pn->pn_dist, &left, f,
                      data);
  cm_free(greq->greq_req.req_cm, path);
  return err;
}

static size_t path_limit(graphd_constraint const *con) {
  if (!con->con_pathlimit_valid) return GRAPHD_PATH_LIMIT_DEFAULT;
  return con->con_pathlimit > (unsigned long long)SIZE_MAX
             ? SIZE_MAX
             : (size_t)con->con_pathlimit;
}

/* ---------------------------------------------------------------- */
/*  Binding templates                                                */

/*  Make <ids> the candidates of a template's root.
 */
static int bind_root(graphd_request *greq, graphd_constraint *root,
                     pdb_id const *ids, size_t n) {
  graphd_handle *g = graphd_request_graphd(greq);
  pdb_iterator *it;
  int err;

  err = graphd_iterator_fixed_create_array(g, ids, n, 0, PDB_ITERATOR_HIGH_ANY,
                                           true, &it);
  if (err != 0) return err;
  if (root->con_it != NULL) pdb_iterator_destroy(g->g_pdb, &root->con_it);
  root->con_it = it;
  return 0;
}

/*  Make the "*" of a template match only <id>.  (Constraints that
 *  rejected a node for another binding may have it in their bad cache.)
 */
static void bind_end(graphd_constraint *con, pdb_id id) {
  graphd_constraint *sub;
  graphd_constraint_or *cor;

  if (con->con_step_end) {
    con->con_step_bound_valid = true;
    con->con_step_bound = id;
  }
  graphd_bad_cache_initialize(&con->con_bad_cache);
  for (sub = con->con_head; sub != NULL; sub = sub->con_next)
    bind_end(sub, id);
  for (cor = con->con_or_head; cor != NULL; cor = cor->or_next) {
    bind_end(&cor->or_head, id);
    if (cor->or_tail != NULL) bind_end(cor->or_tail, id);
  }
}

/* ---------------------------------------------------------------- */
/*  The walk                                                         */

static int walk_reach(graphd_read_path_context *grp, pdb_id from,
                      graph_guid const *guid) {
  graphd_request *greq = grp->grp_base->grb_greq;
  graphd_handle *g = graphd_request_graphd(greq);
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_path_node *pn;
  pdb_id id;
  int err;

  if ((err = pdb_id_from_guid(g->g_pdb, &id, guid)) != 0) return err;

  if ((pn = run_node(grp->grp_run, id)) == NULL) {
    pn = cm_hnew(&grp->grp_run->pr_nodes, graphd_path_node, &id, sizeof id);
    if (pn == NULL) return ENOMEM;
    pn->pn_dist = grp->grp_level + 1;
    if ((err = ids_append(cm, &grp->grp_next, &grp->grp_next_n,
                          &grp->grp_next_m, id)) != 0)
      return err;
  } else if (pn->pn_dist != grp->grp_level + 1)
    return 0;

  /*  The frontier is evaluated in id order, so predecessors arrive in
   *  order; a node reached through several middles arrives repeatedly.
   */
  if (pn->pn_preds_n > 0 && pn->pn_preds[pn->pn_preds_n - 1] == from)
    return 0;
  return ids_append(cm, &pn->pn_preds, &pn->pn_preds_n, &pn->pn_preds_m, from);
}

/*  Every guid in <val> is a node one step from <from>.
 */
static int walk_collect(graphd_read_path_context *grp, pdb_id from,
                        graphd_value const *val) {
  size_t i, n;
  graphd_value const *v;
  int err;

  switch (val->val_type) {
    case GRAPHD_VALUE_GUID:
      return walk_reach(grp, from, &val->val_guid);
    case GRAPHD_VALUE_LIST:
      v = val->val_list_contents;
      n = val->val_list_n;
      for (i = 0; i < n; i++)
        if ((err = walk_collect(grp, from, v + i)) != 0) return err;
      return 0;
    case GRAPHD_VALUE_SEQUENCE:
      v = val->val_sequence_contents;
      n = val->val_sequence_n;
      for (i = 0; i < n; i++)
        if ((err = walk_collect(grp, from, v + i)) != 0) return err;
      return 0;
    default:
      return 0;
  }
}

/*  A row of the walking template's results: (guid contents), the
 *  guid of a frontier node and what one step from it reached.
 */
static bool walk_is_row(graphd_value const *v) {
  return v->val_type == GRAPHD_VALUE_LIST && v->val_list_n == 2 &&
         v->val_list_contents[0].val_type == GRAPHD_VALUE_GUID;
}

/*  Find the rows in <val>; every guid in a row's contents is a node one
 *  step from the row's node.
 */
static int walk_rows(graphd_read_path_context *grp, graphd_value const *val) {
  graphd_request *greq = grp->grp_base->grb_greq;
  graphd_handle *g = graphd_request_graphd(greq);
  pdb_id from;
  size_t i;
  int err;

  if (walk_is_row(val)) {
    if ((err = pdb_id_from_guid(g->g_pdb, &from,
                                &val->val_list_contents[0].val_guid)) != 0)
      return err;
    return walk_collect(grp, from, val->val_list_contents + 1);
  }
  if (val->val_type == GRAPHD_VALUE_LIST ||
      val->val_type == GRAPHD_VALUE_SEQUENCE)
    for (i = 0; i < val->val_array_n; i++)
      if ((err = walk_rows(grp, val->val_array_contents + i)) != 0) return err;
  return 0;
}

static void walk_deliver(void *data, int err, graphd_constraint const *con,
                         graphd_value *res) {
  graphd_read_path_context *grp = data;

  if (err == GRAPHD_ERR_NO) return; /* no step from this chunk */
  if (err == 0) err = walk_rows(grp, res + con->con_assignment_n);
  if (err != 0) grp->grp_err = err;
}

static int grp_match(graphd_stack *stack, graphd_stack_context *sc);
static int grp_done(graphd_stack *stack, graphd_stack_context *sc);

/*  Switch on the alternatives of combination <combo>: bit j chooses
 *  the tail of "or" j, or else its head.
 */
static void walk_choose(graphd_constraint *con, unsigned long combo) {
  graphd_constraint_or *cor;
  size_t j;

  for (j = 0; j < con->con_path_ors_n; j++) {
    cor = con->con_path_ors[j];
    cor->or_head.con_path_off = (combo >> j) & 1;
    if (cor->or_tail != NULL) cor->or_tail->con_path_off = !((combo >> j) & 1);
  }
}

static void clear_bad_cache(graphd_constraint *con) {
  graphd_constraint *sub;

  graphd_bad_cache_initialize(&con->con_bad_cache);
  for (sub = con->con_head; sub != NULL; sub = sub->con_next)
    clear_bad_cache(sub);
}

/*  A level's new nodes: their predecessors, found one combination of
 *  alternatives at a time, in order and once each.
 */
static void walk_sort_preds(graphd_read_path_context *grp) {
  graphd_path_node *pn;
  size_t i, j, k;

  for (i = 0; i < grp->grp_next_n; i++) {
    pn = run_node(grp->grp_run, grp->grp_next[i]);
    if (pn == NULL || pn->pn_preds_n < 2) continue;
    qsort(pn->pn_preds, pn->pn_preds_n, sizeof(*pn->pn_preds), id_compare);
    for (j = k = 1; j < pn->pn_preds_n; j++)
      if (pn->pn_preds[j] != pn->pn_preds[k - 1])
        pn->pn_preds[k++] = pn->pn_preds[j];
    pn->pn_preds_n = k;
  }
}

static int grp_walk(graphd_stack *stack, graphd_stack_context *sc) {
  graphd_read_path_context *grp = (void *)sc;
  graphd_request *greq = grp->grp_base->grb_greq;
  graphd_constraint *con = grp->grp_con;
  graphd_constraint *walk = con->con_step_walk;
  pdb_id *swap;
  size_t n;
  int err;

  if (grp->grp_err != 0) {
    graphd_stack_resume(stack, sc, grp_match);
    return 0;
  }

  /*  Another combination of alternatives for this chunk of the
   *  frontier, or another chunk?
   */
  if (grp->grp_chunk_n > 0 &&
      grp->grp_combo >= (1ul << con->con_path_ors_n)) {
    grp->grp_chunk += grp->grp_chunk_n;
    grp->grp_chunk_n = 0;
  }
  if (grp->grp_chunk_n == 0 && grp->grp_chunk < grp->grp_frontier_n) {
    n = grp->grp_frontier_n - grp->grp_chunk;
    if (n > GRAPHD_PATH_CHUNK) n = GRAPHD_PATH_CHUNK;
    grp->grp_chunk_n = n;
    grp->grp_combo = 0;
  }
  if (grp->grp_chunk_n > 0) {
    err = bind_root(greq, walk, grp->grp_frontier + grp->grp_chunk,
                    grp->grp_chunk_n);
    if (err != 0) {
      grp->grp_err = err;
      return 0;
    }
    walk_choose(con, grp->grp_combo++);
    clear_bad_cache(walk);
    graphd_read_set_push(grp->grp_base, walk, PDB_ID_NONE, NULL, walk_deliver,
                         grp);
    return 0;
  }

  /*  The level is done: the nodes it reached are the next frontier.
   */
  walk_sort_preds(grp);
  qsort(grp->grp_next, grp->grp_next_n, sizeof(*grp->grp_next), id_compare);
  swap = grp->grp_frontier;
  grp->grp_frontier = grp->grp_next;
  grp->grp_frontier_n = grp->grp_next_n;
  n = grp->grp_frontier_m;
  grp->grp_frontier_m = grp->grp_next_m;
  grp->grp_next = swap;
  grp->grp_next_n = 0;
  grp->grp_next_m = n;
  grp->grp_chunk = 0;
  grp->grp_level++;

  if (grp->grp_frontier_n == 0 ||
      (con->con_steps.countcon_max_valid &&
       grp->grp_level >= con->con_steps.countcon_max))
    graphd_stack_resume(stack, sc, grp_match);
  return 0;
}

/* ---------------------------------------------------------------- */
/*  The matches                                                      */

static void match_deliver(void *data, int err, graphd_constraint const *con,
                          graphd_value *res) {
  graphd_read_path_context *grp = data;
  (*grp->grp_callback)(grp->grp_callback_data, err, con, res);
  grp->grp_callback = NULL;
}

static int grp_match(graphd_stack *stack, graphd_stack_context *sc) {
  graphd_read_path_context *grp = (void *)sc;
  graphd_request *greq = grp->grp_base->grb_greq;
  graphd_handle *g = graphd_request_graphd(greq);
  graphd_constraint *con = grp->grp_con;
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_path_node *pn = NULL;
  pdb_id *ids = NULL, id;
  size_t n = 0, m = 0;
  int err = grp->grp_err;

  graphd_stack_resume(stack, sc, grp_done);
  if (err != 0) goto err;

  /*  The candidates: every node reached, within the bounds on steps.
   */
  while ((pn = cm_hnext(&grp->grp_run->pr_nodes, graphd_path_node, pn)) !=
         NULL) {
    if (pn->pn_dist == 0) continue;
    if (con->con_steps.countcon_min_valid &&
        pn->pn_dist < con->con_steps.countcon_min)
      continue;
    if (con->con_steps.countcon_max_valid &&
        pn->pn_dist > con->con_steps.countcon_max)
      continue;
    id = *(pdb_id const *)cm_hashmem(&grp->grp_run->pr_nodes, pn);
    if ((err = ids_append(cm, &ids, &n, &m, id)) != 0) goto err;
  }
  qsort(ids, n, sizeof(*ids), id_compare);

  if (con->con_path_it != NULL) pdb_iterator_destroy(g->g_pdb, &con->con_path_it);
  err = graphd_iterator_fixed_create_array(g, ids, n, 0, PDB_ITERATOR_HIGH_ANY,
                                           true, &con->con_path_it);
  if (ids != NULL) cm_free(cm, ids);
  if (err != 0) goto err;

  con->con_path_run = grp->grp_run;
  graphd_read_set_push(grp->grp_base, con, grp->grp_parent_id,
                       grp->grp_parent_pr, match_deliver, grp);
  return 0;

err:
  match_deliver(grp, err, con, NULL);
  return 0;
}

static int grp_done(graphd_stack *stack, graphd_stack_context *sc) {
  graphd_read_path_context *grp = (void *)sc;
  graphd_request *greq = grp->grp_base->grb_greq;

  if (grp->grp_callback != NULL)
    match_deliver(grp, grp->grp_err ? grp->grp_err : GRAPHD_ERR_NO,
                  grp->grp_con, NULL);
  graphd_stack_pop(&greq->greq_stack);
  return 0;
}

/* ---------------------------------------------------------------- */
/*  The stack frame                                                  */

static void grp_free(graphd_read_path_context *grp) {
  graphd_request *greq = grp->grp_base->grb_greq;
  graphd_handle *g = graphd_request_graphd(greq);
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_constraint *con = grp->grp_con;

  if (con->con_path_it != NULL)
    pdb_iterator_destroy(g->g_pdb, &con->con_path_it);
  con->con_path_it = grp->grp_saved_it;
  con->con_path_run = grp->grp_saved_run;

  run_destroy(greq, grp->grp_run);
  if (grp->grp_frontier != NULL) cm_free(cm, grp->grp_frontier);
  if (grp->grp_next != NULL) cm_free(cm, grp->grp_next);
  cm_free(cm, grp);
}

static void grp_resource_free(void *manager_data, void *resource_data) {
  grp_free(resource_data);
}

static void grp_resource_list(void *log_data, void *manager_data,
                              void *resource_data) {
  cl_handle *cl = log_data;
  graphd_read_path_context *grp = resource_data;

  cl_log(cl, CL_LEVEL_VERBOSE, "path search %llx: level %lu, %s",
         (unsigned long long)grp->grp_parent_id, grp->grp_level,
         graphd_constraint_to_string(grp->grp_con));
}

static cm_resource_type grp_resource_type = {
    "path search context", grp_resource_free, grp_resource_list};

static int grp_suspend(graphd_stack *stack, graphd_stack_context *sc) {
  return 0;
}

static int grp_unsuspend(graphd_stack *stack, graphd_stack_context *sc) {
  return 0;
}

static const graphd_stack_type grp_stack_type = {grp_walk, grp_suspend,
                                                 grp_unsuspend};

/**
 * @brief Push a path search: evaluate a <-path constraint for a parent.
 *
 *  Like graphd_read_set_push(), whose callback it shares.
 */
void graphd_read_path_push(graphd_read_base *grb, graphd_constraint *con,
                           pdb_id parent_id, pdb_primitive const *parent_pr,
                           graphd_read_set_callback *callback,
                           void *callback_data) {
  graphd_request *greq = grb->grb_greq;
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_read_path_context *grp;
  int err = ENOMEM;

  grp = cm_zalloc(cm, sizeof(*grp));
  if (grp == NULL) goto err;
  grp->grp_base = grb;
  grp->grp_con = con;
  grp->grp_parent_id = parent_id;
  grp->grp_parent_pr = parent_pr;
  grp->grp_callback = callback;
  grp->grp_callback_data = callback_data;
  grp->grp_saved_it = con->con_path_it;
  grp->grp_saved_run = con->con_path_run;
  con->con_path_it = NULL;

  if ((grp->grp_run = run_create(greq, parent_id)) == NULL ||
      (err = ids_append(cm, &grp->grp_frontier, &grp->grp_frontier_n,
                        &grp->grp_frontier_m, parent_id)) != 0) {
    grp_free(grp);
    goto err;
  }

  graphd_stack_push(&greq->greq_stack, &grp->grp_sc, &grp_resource_type,
                    &grp_stack_type);
  return;

err:
  (*callback)(callback_data, err, con, NULL);
}

/* ---------------------------------------------------------------- */
/*  via                                                              */

typedef struct graphd_read_path_via_context {
  graphd_stack_context grv_sc;
  graphd_read_base *grv_base;
  graphd_constraint *grv_con;
  pdb_id grv_id;
  int *grv_err_out;

  /*  The steps whose via is wanted: pairs of ids.
   */
  pdb_id *grv_pairs;
  size_t grv_pairs_n;
  size_t grv_pairs_m;
  size_t grv_i;

} graphd_read_path_via_context;

static int via_want(void *data, pdb_id const *path, size_t n) {
  graphd_read_path_via_context *grv = data;
  cm_handle *cm = grv->grv_base->grb_greq->greq_req.req_cm;
  size_t i;
  int err;

  for (i = 0; i + 1 < n; i++)
    if ((err = ids_append(cm, &grv->grv_pairs, &grv->grv_pairs_n,
                          &grv->grv_pairs_m, path[i])) != 0 ||
        (err = ids_append(cm, &grv->grv_pairs, &grv->grv_pairs_n,
                          &grv->grv_pairs_m, path[i + 1])) != 0)
      return err;
  return 0;
}

static void via_deliver(void *data, int err, graphd_constraint const *con,
                        graphd_value *res) {
  graphd_read_path_via_context *grv = data;
  graphd_request *greq = grv->grv_base->grb_greq;
  graphd_path_run *run = grv->grv_con->con_path_run;
  graphd_value *val;

  val = cm_hnew(&run->pr_via, graphd_value, grv->grv_pairs + grv->grv_i - 2,
                2 * sizeof(pdb_id));
  if (val == NULL) {
    *grv->grv_err_out = ENOMEM;
    return;
  }
  if (err == 0) {
    *val = res[con->con_assignment_n];
    graphd_value_initialize(res + con->con_assignment_n);
  } else if (err == GRAPHD_ERR_NO)
    graphd_value_null_set(val);
  else
    *grv->grv_err_out = err;
  (void)greq;
}

static int grv_run(graphd_stack *stack, graphd_stack_context *sc) {
  graphd_read_path_via_context *grv = (void *)sc;
  graphd_request *greq = grv->grv_base->grb_greq;
  graphd_constraint *con = grv->grv_con;
  graphd_path_run *run = con->con_path_run;
  pdb_id const *pair;
  int err;

  while (*grv->grv_err_out == 0 && grv->grv_i < grv->grv_pairs_n) {
    pair = grv->grv_pairs + grv->grv_i;
    grv->grv_i += 2;
    if (cm_haccess(&run->pr_via, graphd_value, pair, 2 * sizeof(pdb_id)) !=
        NULL)
      continue;

    /*  The query's template, from pair[0] to pair[1].
     */
    if ((err = bind_root(greq, con->con_step, pair, 1)) != 0) {
      *grv->grv_err_out = err;
      break;
    }
    bind_end(con->con_step, pair[1]);
    graphd_read_set_push(grv->grv_base, con->con_step, PDB_ID_NONE, NULL,
                         via_deliver, grv);
    return 0;
  }
  graphd_stack_pop(&greq->greq_stack);
  return 0;
}

static void grv_resource_free(void *manager_data, void *resource_data) {
  graphd_read_path_via_context *grv = resource_data;
  cm_handle *cm = grv->grv_base->grb_greq->greq_req.req_cm;

  if (grv->grv_pairs != NULL) cm_free(cm, grv->grv_pairs);
  cm_free(cm, grv);
}

static void grv_resource_list(void *log_data, void *manager_data,
                              void *resource_data) {
  cl_handle *cl = log_data;
  graphd_read_path_via_context *grv = resource_data;

  cl_log(cl, CL_LEVEL_VERBOSE, "path via %llx: %zu of %zu",
         (unsigned long long)grv->grv_id, grv->grv_i / 2,
         grv->grv_pairs_n / 2);
}

static cm_resource_type grv_resource_type = {
    "path via context", grv_resource_free, grv_resource_list};

static const graphd_stack_type grv_stack_type = {grv_run, grp_suspend,
                                                 grp_unsuspend};

/**
 * @brief Compute the via of each step of <id>'s paths that its results
 *  show, before graphd_read_one() delivers them.
 */
void graphd_read_path_via_push(graphd_read_base *grb, graphd_constraint *con,
                               pdb_id id, int *err_out) {
  graphd_request *greq = grb->grb_greq;
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_read_path_via_context *grv;
  graphd_path_run *run = con->con_path_run;
  int err;

  if (!con->con_path_via || run == NULL) return;

  grv = cm_zalloc(cm, sizeof(*grv));
  if (grv == NULL) {
    *err_out = ENOMEM;
    return;
  }
  grv->grv_base = grb;
  grv->grv_con = con;
  grv->grv_id = id;
  grv->grv_err_out = err_out;

  /*  The first path, and those in the list of paths.
   */
  err = run_paths(greq, run, id, 1, via_want, grv);
  if (err == 0 && con->con_path_list != NULL &&
      pattern_has_via(con->con_path_list))
    err = run_paths(greq, run, id, path_limit(con), via_want, grv);
  if (err != 0) {
    *err_out = err;
    if (grv->grv_pairs != NULL) cm_free(cm, grv->grv_pairs);
    cm_free(cm, grv);
    return;
  }
  graphd_stack_push(&greq->greq_stack, &grv->grv_sc, &grv_resource_type,
                    &grv_stack_type);
}

/* ---------------------------------------------------------------- */
/*  Results                                                          */

typedef struct path_value_context {
  graphd_request *pv_greq;
  graphd_constraint const *pv_con;
  graphd_pattern const *pv_atoms; /* NULL: the path itself */
  graphd_value *pv_list;
  size_t pv_i;
} path_value_context;

static int path_guids(graphd_request *greq, pdb_id const *path, size_t n,
                      graphd_value *val) {
  graphd_handle *g = graphd_request_graphd(greq);
  cl_handle *cl = graphd_request_cl(greq);
  graph_guid guid;
  size_t i;
  int err;

  err = graphd_value_list_alloc(g, greq->greq_req.req_cm, cl, val, n);
  if (err != 0) return err;
  for (i = 0; i < n; i++) {
    if ((err = pdb_id_to_guid(g->g_pdb, path[i], &guid)) != 0) return err;
    graphd_value_guid_set(val->val_list_contents + i, &guid);
  }
  return 0;
}

static int path_via(graphd_request *greq, graphd_constraint const *con,
                    pdb_id const *path, size_t n, graphd_value *val) {
  graphd_handle *g = graphd_request_graphd(greq);
  cl_handle *cl = graphd_request_cl(greq);
  cm_handle *cm = greq->greq_req.req_cm;
  graphd_value *step;
  size_t i;
  int err;

  err = graphd_value_list_alloc(g, cm, cl, val, n - 1);
  if (err != 0) return err;
  for (i = 0; i + 1 < n; i++) {
    step = con->con_path_run == NULL
               ? NULL
               : cm_haccess(&con->con_path_run->pr_via, graphd_value, path + i,
                            2 * sizeof(pdb_id));
    if (step == NULL)
      graphd_value_null_set(val->val_list_contents + i);
    else if ((err = graphd_value_copy(g, cm, cl, val->val_list_contents + i,
                                      step)) != 0)
      return err;
  }
  return 0;
}

static int path_atom(graphd_request *greq, graphd_constraint const *con,
                     graphd_pattern const *pat, pdb_id const *path, size_t n,
                     graphd_value *val) {
  switch (pat->pat_type) {
    case GRAPHD_PATTERN_PATH:
      return path_guids(greq, path, n, val);
    case GRAPHD_PATTERN_VIA:
      return path_via(greq, con, path, n, val);
    case GRAPHD_PATTERN_STEPS:
      graphd_value_number_set(val, n - 1);
      return 0;
    default:
      graphd_value_null_set(val);
      return 0;
  }
}

/*  One path's values, as the list of paths says.
 */
static int path_value(void *data, pdb_id const *path, size_t n) {
  path_value_context *pv = data;
  graphd_request *greq = pv->pv_greq;
  graphd_handle *g = graphd_request_graphd(greq);
  cl_handle *cl = graphd_request_cl(greq);
  graphd_value *val = pv->pv_list->val_list_contents + pv->pv_i++;
  graphd_pattern const *sub;
  size_t i;
  int err;

  err = graphd_value_list_alloc(g, greq->greq_req.req_cm, cl, val,
                                pv->pv_atoms->pat_list_n);
  if (err != 0) return err;
  for (sub = pv->pv_atoms->pat_list_head, i = 0; sub != NULL;
       sub = sub->pat_next, i++)
    if ((err = path_atom(greq, pv->pv_con, sub, path, n,
                         val->val_list_contents + i)) != 0)
      return err;
  return 0;
}

static int first_path(void *data, pdb_id const *path, size_t n) {
  path_value_context *pv = data;
  return path_atom(pv->pv_greq, pv->pv_con, pv->pv_atoms, path, n,
                   pv->pv_list);
}

/**
 * @brief The value of a path result (path, steps, via, path-count, or
 *  a list of paths) for a match.
 *
 * @return GRAPHD_ERR_NO if there is none.
 */
int graphd_read_path_value(graphd_request *greq, graphd_constraint const *con,
                           graphd_pattern const *pat, pdb_primitive const *pr,
                           graphd_value *val) {
  graphd_handle *g = graphd_request_graphd(greq);
  cl_handle *cl = graphd_request_cl(greq);
  graphd_path_run *run = con->con_path_run;
  graphd_path_node *pn;
  path_value_context pv;
  unsigned long long count;
  graph_guid guid;
  pdb_id id;
  size_t n;
  int err;

  if (run == NULL) return GRAPHD_ERR_NO;
  pdb_primitive_guid_get(pr, guid);
  if ((err = pdb_id_from_guid(g->g_pdb, &id, &guid)) != 0) return err;
  if ((pn = run_node(run, id)) == NULL) return GRAPHD_ERR_NO;

  memset(&pv, 0, sizeof pv);
  pv.pv_greq = greq;
  pv.pv_con = con;

  switch (pat->pat_type) {
    case GRAPHD_PATTERN_STEPS:
      graphd_value_number_set(val, pn->pn_dist);
      return 0;

    case GRAPHD_PATTERN_PATH_COUNT:
      graphd_value_number_set(val, run_count(run, id));
      return 0;

    case GRAPHD_PATTERN_PATH:
    case GRAPHD_PATTERN_VIA:
      pv.pv_atoms = pat;
      pv.pv_list = val;
      return run_paths(greq, run, id, 1, first_path, &pv);

    case GRAPHD_PATTERN_PATH_LIST:
      count = run_count(run, id);
      n = path_limit(con);
      if (count < n) n = (size_t)count;
      err = graphd_value_list_alloc(g, greq->greq_req.req_cm, cl, val, n);
      if (err != 0) return err;
      pv.pv_atoms = pat;
      pv.pv_list = val;
      return run_paths(greq, run, id, n, path_value, &pv);

    default:
      return GRAPHD_ERR_NO;
  }
}
