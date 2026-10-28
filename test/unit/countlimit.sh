#!/bin/bash
#
# countlimit caps the count graphd reports, not the count it
# matches against: count>4 still sees all five links.

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

rm -rf $D
rungraphd -d${D} -bty <<-'EOF'
	write (value="root" (<-left type="has" value="a") (<-left type="has" value="b") (<-left type="has" value="c") (<-left type="has" value="d") (<-left type="has" value="e"))
	read (value="root" (<-left type="has" result=(count)))
	read (value="root" (<-left type="has" countlimit=3 result=(count)))
	read (value="root" (<-left type="has" countlimit=3 pagesize=2 result=(count (value))))
	read (value="root" (<-left type="has" countlimit=10 result=(count)))
	read (value="root" (<-left type="has" countlimit=0 result=(count)))
	read (value="root" (<-left type="has" countlimit=3 count>4 result=(count)))
	read (value="root" (<-left type="has" countlimit=3 count>5 result=(count)))
	read (any type="has" countlimit=2 result=(count))
	read (any type="has" countlimit=-1 result=(count))
	EOF
rm -rf $D
