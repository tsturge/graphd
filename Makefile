# Non-Bazel build for graphd (e.g. macOS without Bazel).
# Source lists mirror the cc_library/cc_binary targets in the BUILD files.

CC ?= cc
CFLAGS ?= -g -O2 -w
CPPFLAGS += -I.
LDLIBS += -lm -pthread
BUILDDIR ?= out

LIBCL_SRCS = \
	libcl/cl-abort-c.c \
	libcl/cl-build-version.c \
	libcl/cl-cover.c \
	libcl/cl-create.c \
	libcl/cl-destroy.c \
	libcl/cl-diary.c \
	libcl/cl-dup2.c \
	libcl/cl-file.c \
	libcl/cl-flush.c \
	libcl/cl-hard-error.c \
	libcl/cl-log.c \
	libcl/cl-log-errno.c \
	libcl/cl-loglevel.c \
	libcl/cl-netlog.c \
	libcl/cl-netlog3.c \
	libcl/cl-notreached.c \
	libcl/cl-set-abort.c \
	libcl/cl-set-coverage.c \
	libcl/cl-set-hard-error.c \
	libcl/cl-set-stacktrace.c \
	libcl/cl-set-strerror.c \
	libcl/cl-set-write.c \
	libcl/cl-siphon.c \
	libcl/cl-stack.c \
	libcl/cl-strerror.c \
	libcl/cl-syslog.c \
	libcl/cl-vlog.c \
	libcl/cl-wnotreached.c \
	libcl/cl-write-file.c \
	libcl/cl-write-netlog.c \
	libcl/cl-write-netlog3.c \
	libcl/cl-write-stderr.c \
	libcl/cl-write-syslog.c

LIBCM_SRCS = \
	libcm/cm-argv.c \
	libcm/cm-buffer.c \
	libcm/cm-build-version.c \
	libcm/cm-c.c \
	libcm/cm-error.c \
	libcm/cm-hash.c \
	libcm/cm-heap.c \
	libcm/cm-list.c \
	libcm/cm-malcpy.c \
	libcm/cm-prefix.c \
	libcm/cm-resource.c \
	libcm/cm-runtime-statistics.c \
	libcm/cm-sprintf.c \
	libcm/cm-substr.c \
	libcm/cm-trace.c \
	libcm/cm-zalloc.c

LIBES_SRCS = \
	libes/es-application-event.c \
	libes/es-build-version.c \
	libes/es-close.c \
	libes/es-create.c \
	libes/es-demon.c \
	libes/es-destroy.c \
	libes/es-idle.c \
	libes/es-loop.c \
	libes/es-now.c \
	libes/es-open.c \
	libes/es-set-post-dispatch.c \
	libes/es-set-pre-dispatch.c \
	libes/es-subscribe.c \
	libes/es-timeout.c \
	libes/es-unsubscribe.c

LIBGRAPH_SRCS = \
	libgraph/graph-build-version.c \
	libgraph/graph-create.c \
	libgraph/graph-datatype.c \
	libgraph/graph-dateline.c \
	libgraph/graph-destroy.c \
	libgraph/graph-fuzzy-compare.c \
	libgraph/graph-grmap.c \
	libgraph/graph-grmap-dateline.c \
	libgraph/graph-grmap-equal.c \
	libgraph/graph-grmap-next.c \
	libgraph/graph-grmap-range.c \
	libgraph/graph-grmap-read.c \
	libgraph/graph-grmap-table.c \
	libgraph/graph-grmap-write.c \
	libgraph/graph-guid.c \
	libgraph/graph-hullset.c \
	libgraph/graph-idset-tile.c \
	libgraph/graph-strerror.c \
	libgraph/graph-timestamp.c

LIBADDB_SRCS = \
	libaddb/addb-backup.c \
	libaddb/addb-bmap.c \
	libaddb/addb-build-version.c \
	libaddb/addb-clock.c \
	libaddb/addb-create.c \
	libaddb/addb-destroy.c \
	libaddb/addb-file.c \
	libaddb/addb-flat-close.c \
	libaddb/addb-flat-open.c \
	libaddb/addb-flat-read.c \
	libaddb/addb-flat-remove.c \
	libaddb/addb-flat-write.c \
	libaddb/addb-gmap-access.c \
	libaddb/addb-gmap-add.c \
	libaddb/addb-gmap-array.c \
	libaddb/addb-gmap-backup.c \
	libaddb/addb-gmap-bmap.c \
	libaddb/addb-gmap-checkpoint.c \
	libaddb/addb-gmap-close.c \
	libaddb/addb-gmap-configure.c \
	libaddb/addb-gmap-freelist.c \
	libaddb/addb-gmap-horizon.c \
	libaddb/addb-gmap-iterator.c \
	libaddb/addb-gmap-open.c \
	libaddb/addb-gmap-partition.c \
	libaddb/addb-gmap-refresh.c \
	libaddb/addb-gmap-remove.c \
	libaddb/addb-gmap-status.c \
	libaddb/addb-hmap.c \
	libaddb/addb-hmap-checkpoint.c \
	libaddb/addb-hmap-iterator.c \
	libaddb/addb-idarray.c \
	libaddb/addb-idarray-intersect.c \
	libaddb/addb-istore-alloc.c \
	libaddb/addb-istore-checkpoint.c \
	libaddb/addb-istore-close.c \
	libaddb/addb-istore-configure.c \
	libaddb/addb-istore-free.c \
	libaddb/addb-istore-horizon.c \
	libaddb/addb-istore-index.c \
	libaddb/addb-istore-marker.c \
	libaddb/addb-istore-next-id.c \
	libaddb/addb-istore-open.c \
	libaddb/addb-istore-partition.c \
	libaddb/addb-istore-read.c \
	libaddb/addb-istore-reference.c \
	libaddb/addb-istore-refresh.c \
	libaddb/addb-istore-remove.c \
	libaddb/addb-istore-status.c \
	libaddb/addb-istore-write.c \
	libaddb/addb-largefile.c \
	libaddb/addb-mem.c \
	libaddb/addb-status.c \
	libaddb/addb-strerror.c \
	libaddb/addb-tiled.c

LIBPDB_SRCS = \
	libpdb/pdb-bins.c \
	libpdb/pdb-bins-numtable.c \
	libpdb/pdb-bins-strtable.c \
	libpdb/pdb-build-version.c \
	libpdb/pdb-checkpoint.c \
	libpdb/pdb-concentric.c \
	libpdb/pdb-configure.c \
	libpdb/pdb-count.c \
	libpdb/pdb-create.c \
	libpdb/pdb-database-id.c \
	libpdb/pdb-database-path.c \
	libpdb/pdb-destroy.c \
	libpdb/pdb-disk.c \
	libpdb/pdb-generation.c \
	libpdb/pdb-hash.c \
	libpdb/pdb-id.c \
	libpdb/pdb-index.c \
	libpdb/pdb-index-bmap.c \
	libpdb/pdb-index-gmap.c \
	libpdb/pdb-index-hmap.c \
	libpdb/pdb-initialize.c \
	libpdb/pdb-is-remote-mounted.c \
	libpdb/pdb-iterator.c \
	libpdb/pdb-iterator-all.c \
	libpdb/pdb-iterator-bgmap.c \
	libpdb/pdb-iterator-by-name.c \
	libpdb/pdb-iterator-gmap.c \
	libpdb/pdb-iterator-hmap.c \
	libpdb/pdb-iterator-null.c \
	libpdb/pdb-iterator-smap.c \
	libpdb/pdb-iterator-suspend.c \
	libpdb/pdb-iterator-util.c \
	libpdb/pdb-linkage.c \
	libpdb/pdb-local-ip.c \
	libpdb/pdb-lockfile.c \
	libpdb/pdb-log.c \
	libpdb/pdb-mem.c \
	libpdb/pdb-msclock.c \
	libpdb/pdb-prefix.c \
	libpdb/pdb-primitive-alloc.c \
	libpdb/pdb-primitive-alloc-subscription.c \
	libpdb/pdb-primitive-compress.c \
	libpdb/pdb-primitive-dump.c \
	libpdb/pdb-primitive-finish.c \
	libpdb/pdb-primitive-initialize.c \
	libpdb/pdb-primitive-n.c \
	libpdb/pdb-primitive-previous.c \
	libpdb/pdb-primitive-read.c \
	libpdb/pdb-primitive-reference.c \
	libpdb/pdb-primitive-summary.c \
	libpdb/pdb-refresh.c \
	libpdb/pdb-restore.c \
	libpdb/pdb-runtime-statistics.c \
	libpdb/pdb-rxs.c \
	libpdb/pdb-set-database-id.c \
	libpdb/pdb-set-database-name.c \
	libpdb/pdb-set-mode.c \
	libpdb/pdb-set-path.c \
	libpdb/pdb-set-predictable.c \
	libpdb/pdb-set-sync.c \
	libpdb/pdb-status.c \
	libpdb/pdb-strerror.c \
	libpdb/pdb-sync.c \
	libpdb/pdb-truncate.c \
	libpdb/pdb-util.c \
	libpdb/pdb-verify.c \
	libpdb/pdb-versioned.c \
	libpdb/pdb-vip.c \
	libpdb/pdb-word.c

LIBSRV_SRCS = \
	libsrv/srv-address.c \
	libsrv/srv-buffer.c \
	libsrv/srv-buffer-pool.c \
	libsrv/srv-buffered-connection.c \
	libsrv/srv-build-version.c \
	libsrv/srv-config.c \
	libsrv/srv-delay.c \
	libsrv/srv-epitaph.c \
	libsrv/srv-idle.c \
	libsrv/srv-interface.c \
	libsrv/srv-interface-socket.c \
	libsrv/srv-interface-tcp.c \
	libsrv/srv-interface-tty.c \
	libsrv/srv-interface-unix.c \
	libsrv/srv-main.c \
	libsrv/srv-memory-list.c \
	libsrv/srv-msclock.c \
	libsrv/srv-pidfile.c \
	libsrv/srv-request.c \
	libsrv/srv-session.c \
	libsrv/srv-settle.c \
	libsrv/srv-shared.c \
	libsrv/srv-sleep.c \
	libsrv/srv-stat.c \
	libsrv/srv-timeout.c \
	libsrv/srv-unixid.c

LIBGDP_SRCS = \
	libgdp/gdp.c \
	libgdp/gdp-input.c \
	libgdp/gdp-lexer.c \
	libgdp/gdp-micro.c \
	libgdp/gdp-parse.c \
	libgdp/gdp-token.c

GRAPHD_SRCS = \
	graphd/graphd.c \
	graphd/graphd-access.c \
	graphd/graphd-assignment.c \
	graphd/graphd-ast.c \
	graphd/graphd-ast-debug.c \
	graphd/graphd-bad-cache.c \
	graphd/graphd-build-version.c \
	graphd/graphd-checkcache.c \
	graphd/graphd-checkpoint.c \
	graphd/graphd-client-replica.c \
	graphd/graphd-comparator.c \
	graphd/graphd-comparator-case.c \
	graphd/graphd-comparator-datetime.c \
	graphd/graphd-comparator-default.c \
	graphd/graphd-comparator-number.c \
	graphd/graphd-comparator-octet.c \
	graphd/graphd-constraint.c \
	graphd/graphd-constraint-clause.c \
	graphd/graphd-constraint-cursor.c \
	graphd/graphd-constraint-iterator.c \
	graphd/graphd-constraint-or.c \
	graphd/graphd-constraint-path.c \
	graphd/graphd-constraint-setsize.c \
	graphd/graphd-constraint-signature.c \
	graphd/graphd-constraint-to-string.c \
	graphd/graphd-cost.c \
	graphd/graphd-cost-parse.c \
	graphd/graphd-database.c \
	graphd/graphd-dateline.c \
	graphd/graphd-dump.c \
	graphd/graphd-guid-constraint.c \
	graphd/graphd-guid-set.c \
	graphd/graphd-idle.c \
	graphd/graphd-instance-id.c \
	graphd/graphd-interface-id.c \
	graphd/graphd-islink.c \
	graphd/graphd-islink-group.c \
	graphd/graphd-islink-intersect.c \
	graphd/graphd-islink-job.c \
	graphd/graphd-islink-key.c \
	graphd/graphd-islink-side.c \
	graphd/graphd-islink-status.c \
	graphd/graphd-islink-type.c \
	graphd/graphd-iterate.c \
	graphd/graphd-iterator.c \
	graphd/graphd-iterator-and.c \
	graphd/graphd-iterator-and-check.c \
	graphd/graphd-iterator-and-freeze.c \
	graphd/graphd-iterator-and-optimize.c \
	graphd/graphd-iterator-and-process-state.c \
	graphd/graphd-iterator-and-run.c \
	graphd/graphd-iterator-and-statistics.c \
	graphd/graphd-iterator-cache.c \
	graphd/graphd-iterator-dump.c \
	graphd/graphd-iterator-fixed.c \
	graphd/graphd-iterator-idset.c \
	graphd/graphd-iterator-isa.c \
	graphd/graphd-iterator-isa-storable.c \
	graphd/graphd-iterator-islink.c \
	graphd/graphd-iterator-linksto.c \
	graphd/graphd-iterator-or.c \
	graphd/graphd-iterator-prefix.c \
	graphd/graphd-iterator-resource.c \
	graphd/graphd-iterator-sort.c \
	graphd/graphd-iterator-state.c \
	graphd/graphd-iterator-vip.c \
	graphd/graphd-iterator-vrange.c \
	graphd/graphd-iterator-without.c \
	graphd/graphd-key.c \
	graphd/graphd-match.c \
	graphd/graphd-pattern.c \
	graphd/graphd-pattern-frame.c \
	graphd/graphd-predictable.c \
	graphd/graphd-property.c \
	graphd/graphd-read.c \
	graphd/graphd-read-base.c \
	graphd/graphd-read-base-evaluate.c \
	graphd/graphd-read-one.c \
	graphd/graphd-read-or.c \
	graphd/graphd-read-set.c \
	graphd/graphd-read-set-count.c \
	graphd/graphd-read-set-cursor.c \
	graphd/graphd-read-set-defer.c \
	graphd/graphd-read-set-estimate.c \
	graphd/graphd-replica.c \
	graphd/graphd-request.c \
	graphd/graphd-request-input.c \
	graphd/graphd-request-output.c \
	graphd/graphd-request-run.c \
	graphd/graphd-request-size-max.c \
	graphd/graphd-request-timer.c \
	graphd/graphd-request-xstate.c \
	graphd/graphd-restore.c \
	graphd/graphd-runtime-statistics.c \
	graphd/graphd-sabotage.c \
	graphd/graphd-semantic.c \
	graphd/graphd-session.c \
	graphd/graphd-set.c \
	graphd/graphd-shutdown.c \
	graphd/graphd-sleep.c \
	graphd/graphd-smp.c \
	graphd/graphd-smp-config.c \
	graphd/graphd-smp-forward.c \
	graphd/graphd-smp-passthrough.c \
	graphd/graphd-smp-startup.c \
	graphd/graphd-snapshot.c \
	graphd/graphd-sort.c \
	graphd/graphd-sort-compile.c \
	graphd/graphd-sort-root.c \
	graphd/graphd-stack.c \
	graphd/graphd-startup.c \
	graphd/graphd-status.c \
	graphd/graphd-strerror.c \
	graphd/graphd-string-constraint.c \
	graphd/graphd-suspend-a-read.c \
	graphd/graphd-sync.c \
	graphd/graphd-text-compare.c \
	graphd/graphd-timestamp.c \
	graphd/graphd-token.c \
	graphd/graphd-type.c \
	graphd/graphd-unique.c \
	graphd/graphd-value.c \
	graphd/graphd-variable.c \
	graphd/graphd-variable-analysis.c \
	graphd/graphd-variable-declaration.c \
	graphd/graphd-verify.c \
	graphd/graphd-write.c \
	graphd/graphd-write-annotate-anchor.c \
	graphd/graphd-write-annotate-keyed.c \
	graphd/graphd-write-annotate-pointed.c \
	graphd/graphd-write-check-unique.c \
	graphd/graphd-writethrough.c \
	graphd/graphd-xstate.c

LIBGRAPHDB_SRCS = \
	libgraphdb/graphdb-address.c \
	libgraphdb/graphdb-args.c \
	libgraphdb/graphdb-buffer-alloc.c \
	libgraphdb/graphdb-buffer-dup.c \
	libgraphdb/graphdb-buffer-format.c \
	libgraphdb/graphdb-buffer-free.c \
	libgraphdb/graphdb-build-version.c \
	libgraphdb/graphdb-connect.c \
	libgraphdb/graphdb-connection-drop.c \
	libgraphdb/graphdb-create.c \
	libgraphdb/graphdb-descriptor.c \
	libgraphdb/graphdb-destroy.c \
	libgraphdb/graphdb-heap.c \
	libgraphdb/graphdb-initialize.c \
	libgraphdb/graphdb-iterator.c \
	libgraphdb/graphdb-log.c \
	libgraphdb/graphdb-query.c \
	libgraphdb/graphdb-reconnect-async.c \
	libgraphdb/graphdb-request-alloc.c \
	libgraphdb/graphdb-request-cancel.c \
	libgraphdb/graphdb-request-chain.c \
	libgraphdb/graphdb-request-free.c \
	libgraphdb/graphdb-request-io.c \
	libgraphdb/graphdb-request-lookup.c \
	libgraphdb/graphdb-request-send.c \
	libgraphdb/graphdb-request-wait.c \
	libgraphdb/graphdb-server-error.c \
	libgraphdb/graphdb-server-name.c \
	libgraphdb/graphdb-set-logging.c \
	libgraphdb/graphdb-set-loglevel.c \
	libgraphdb/graphdb-set-memory.c \
	libgraphdb/graphdb-set-reply-callback.c \
	libgraphdb/graphdb-strerror.c \
	libgraphdb/graphdb-time.c \
	libgraphdb/graphdb-token.c \
	libgraphdb/graphdb-transaction-id.c

SRCS = $(LIBCL_SRCS) $(LIBCM_SRCS) $(LIBES_SRCS) $(LIBGRAPH_SRCS) $(LIBADDB_SRCS) $(LIBPDB_SRCS) $(LIBSRV_SRCS) $(LIBGDP_SRCS) $(GRAPHD_SRCS)
OBJS = $(SRCS:%.c=$(BUILDDIR)/%.o)

# gld and gpush are graphd clients used by some of the tests.
CLIENT_LIB_SRCS = $(LIBCL_SRCS) $(LIBCM_SRCS) $(LIBGRAPH_SRCS) $(LIBGRAPHDB_SRCS)
CLIENT_LIB_OBJS = $(CLIENT_LIB_SRCS:%.c=$(BUILDDIR)/%.o)
GLD_OBJS = $(addprefix $(BUILDDIR)/gld/,gld.o gld-primitive.o gld-request.o gld-var.o)
GPUSH_OBJS = $(BUILDDIR)/util/gpush.o
CM_TEST_OBJS = $(BUILDDIR)/libcm/cm-test.o $(LIBCM_SRCS:%.c=$(BUILDDIR)/%.o)

ALL_OBJS = $(sort $(OBJS) $(CLIENT_LIB_OBJS) $(GLD_OBJS) $(GPUSH_OBJS) $(CM_TEST_OBJS))
DEPS = $(ALL_OBJS:.o=.d)

# Parallel jobs for "make test".
TEST_JOBS ?= 8

.PHONY: all tools test clean
all: $(BUILDDIR)/bin/graphd

tools: $(BUILDDIR)/bin/gld $(BUILDDIR)/bin/gpush

$(BUILDDIR)/bin/graphd: $(OBJS)
	@mkdir -p $(dir $@)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILDDIR)/bin/gld: $(GLD_OBJS) $(CLIENT_LIB_OBJS)
	@mkdir -p $(dir $@)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILDDIR)/bin/gpush: $(GPUSH_OBJS) $(CLIENT_LIB_OBJS)
	@mkdir -p $(dir $@)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILDDIR)/bin/cm-test: $(CM_TEST_OBJS)
	@mkdir -p $(dir $@)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILDDIR)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c -o $@ $<

# Run the libcm unit test and the graphd test/unit suite.
# TESTS="anchor sort" runs only those cases.
test: $(BUILDDIR)/bin/graphd $(BUILDDIR)/bin/gld $(BUILDDIR)/bin/gpush $(BUILDDIR)/bin/cm-test
	$(BUILDDIR)/bin/cm-test
	BUILDDIR=$(abspath $(BUILDDIR)) JOBS=$(TEST_JOBS) test/run-tests.sh $(TESTS)

clean:
	rm -rf $(BUILDDIR)

-include $(DEPS)
