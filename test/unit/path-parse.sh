#!/bin/bash
#
# Path search syntax: "<-path", "step=(...)", "*", "steps", "pathlimit",
# the "path", "via", "steps" and "path-count" results, and lists of
# paths, a third level of result list. Paths parse but are not evaluated
# yet, so the well-formed ones fail with "not implemented yet".

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

rm -rf $D
rungraphd -d${D} -bty <<-'EOF'
	write (name="a")
	read (guid=00000012400034568000000000000000 (<-path step=((<-right type="author" left->((<-left type="author" right->(*))))) steps<=10 result=((path steps))))
	read (guid=00000012400034568000000000000000 (<-path step=({(<-left value=null right->(*))} | {(<-right left->(*))}) guid=00000012400034568000000000000001 pagesize=1))
	read (any (<-path step=((<- (*))) steps>=2 steps<=3 $d=steps result=((guid $d))))
	read (any (<-path step=((<- (*))) result=((name path-count (path via)))))
	read (any (<-path step=((<- (*))) pathlimit=5 result=((name (path via steps)))))
	read (any (<-path step=((<- (*))) pathlimit=all result=(count (name (path)))))
	read (any (<-path step=(node (<- (*)))))
	read (any (<-path))
	read (any (<-path step=((<-left right->()))))
	read (any (<-path step=(<-right left->(*))))
	read (any (<-path step=(<- (*))))
	read (any step=((<-left right->(*))))
	read (any steps<=3)
	read (any result=((path)))
	read (any result=((via)))
	read (guid=00000012400034568000000000000000 (<-path step=((<- (*))) result=((path via steps))))
	read (any $s=steps result=(($s)))
	read (any result=((path-count)))
	read (any pathlimit=2)
	read (any result=((name (value))))
	read (any (<-path step=((<- (*))) pathlimit=5 result=((name path))))
	read (any (<-path step=((<- (*))) result=((name (path name)))))
	read (any (<-path step=((<- (*))) result=((name (path ((via)))))))
	read (any (<-path step=((<- (*))) result=((name (path) (via)))))
	read (any (<-path step=((<- (*))) pathlimit=0 result=((name (path)))))
	read (any (<-path step=((<- (*))) pathlimit=x result=((name (path)))))
	read (any (<-path step=((<- (*))) pathlimit=2 pathlimit=3 result=((name (path)))))
	read (any (<-left right->(*)))
	read (any (<-path step=((<-left right->(* value="x")))))
	read (any (<-path step=((<-left right->(* (<-left))))))
	read (any (<-path step=((<-left right->((<-path step=((<- (*)))))))))
	read (any (<-path <-left step=((<- (*)))))
	read (any (<-left <-path step=((<- (*)))))
	read (any (<-path step=((<- (*))) step=((<- (*)))))
	read (any (<-path step=((<- (*))) steps~=3))
	write (name="b" (<-path step=((<- (*)))))
	write (name="b" (<-left right->(*)))
	read (any (<-left))
	EOF
rm -rf $D
