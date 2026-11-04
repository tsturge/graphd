#!/bin/bash
#
# Path search: the cases make-tests.py doesn't generate. On a chain
# x -> a -> b -> c -> d -> e:
#
# - guid= naming nothing that exists, only the start, or the start and
#   another node;
# - two <-path constraints side by side; a <-path below another
#   subconstraint; a <-path inside a <-path's match;
# - a <-path as an "or"'s alternative, which is an error;
# - sort= other than the default, a variable assigned from steps, and
#   paging with a cursor;
# - counting, with and without matches.

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

rm -rf $D
rungraphd -d${D} -bty <<-'EOF'
	write (name="to")
	write (name="a")
	write (name="b")
	write (name="c")
	write (name="d")
	write (name="e")
	write (name="x")
	write (name="ab" typeguid=00000012400034568000000000000000 left=00000012400034568000000000000001 right=00000012400034568000000000000002)
	write (name="bc" typeguid=00000012400034568000000000000000 left=00000012400034568000000000000002 right=00000012400034568000000000000003)
	write (name="cd" typeguid=00000012400034568000000000000000 left=00000012400034568000000000000003 right=00000012400034568000000000000004)
	write (name="de" typeguid=00000012400034568000000000000000 left=00000012400034568000000000000004 right=00000012400034568000000000000005)
	write (name="xa" typeguid=00000012400034568000000000000000 left=00000012400034568000000000000006 right=00000012400034568000000000000001)
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) guid=00000012400034568000000000000999 result=((name))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) guid=00000012400034568000000000000001 result=((name))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) guid=(00000012400034568000000000000001 00000012400034568000000000000003) result=((name steps))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) guid=00000012400034568000000000000999 optional result=((name))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) steps<=2 result=((name steps))) (<-path step=(result=(contents) (<-right typeguid=00000012400034568000000000000000 result=((name)) left->(*))) result=((name steps))))
	read (guid=00000012400034568000000000000006 result=((name contents)) (<-left typeguid=00000012400034568000000000000000 result=((contents)) right->(result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) steps<=2 result=((name steps))))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) steps<=2 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) guid=00000012400034568000000000000005 result=((name steps)))))
	read (guid=00000012400034568000000000000001 result=((name contents)) {(<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) guid=00000012400034568000000000000004 result=((name)))} | {name="zzz"})
	read (guid=00000012400034568000000000000001 result=((name contents)) {name="zzz"} | {(<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) result=((name)))})
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) sort=-steps pagesize=2 result=((name steps))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) sort=name result=((name steps))))
	read (guid=00000012400034568000000000000001 result=((name $far)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) sort=-steps pagesize=1 $far=steps))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) pagesize=2 result=(cursor (name steps))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) pagesize=2 cursor="sort:[o:2][n:12]#2.g00000012400034568000000000000003" result=(cursor (name steps))))
	read (guid=00000012400034568000000000000001 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) result=(count)))
	read (guid=00000012400034568000000000000005 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) optional result=(count)))
	read (guid=00000012400034568000000000000005 result=((name contents)) (<-path step=(result=(contents) (<-left typeguid=00000012400034568000000000000000 result=((name)) right->(*))) result=(count)))
	EOF
rm -rf $D
