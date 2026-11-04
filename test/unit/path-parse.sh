#!/bin/bash
#
# Path search syntax: "<-path", "step=(...)", "*", "steps", and the
# "path" and "steps" results. Paths parse but are not evaluated yet, so
# the well-formed ones fail with "not implemented yet".

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

rm -rf $D
rungraphd -d${D} -bty <<-'EOF'
	write (name="a")
	read (guid=00000012400034568000000000000000 (<-path step=((<-right type="author" left->((<-left type="author" right->(*))))) steps<=10 result=((path steps))))
	read (guid=00000012400034568000000000000000 (<-path step=({(<-left value=null right->(*))} | {(<-right left->(*))}) guid=00000012400034568000000000000001 pagesize=1))
	read (any (<-path step=((<- (*))) steps>=2 steps<=3 $d=steps result=((guid $d))))
	read (any (<-path step=(node (<- (*)))))
	read (any (<-path))
	read (any (<-path step=((<-left right->()))))
	read (any (<-path step=(<-right left->(*))))
	read (any (<-path step=(<- (*))))
	read (any step=((<-left right->(*))))
	read (any steps<=3)
	read (any result=((path)))
	read (any $s=steps result=(($s)))
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
