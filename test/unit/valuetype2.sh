#!/bin/bash
#
# Value types: symbolic and numeric names, storage, selection by
# type, numeric comparison and sorting, and out-of-range types.

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

rm -rf $D
rungraphd -d${D} -bty <<-'EOF'
	write (valuetype=integer value="10")
	write (valuetype=integer value="9")
	write (valuetype=float value="2.5")
	write (valuetype=float value="-1e3")
	write (valuetype=boolean value="true")
	write (valuetype=boolean value="false")
	write (datatype=timestamp value="2006-04-25T22:45:53.0000Z")
	write (valuetype=url value="http://example.com/")
	write (valuetype=200 value="custom")
	write (valuetype=null)
	read (any result=((valuetype datatype value)))
	read (any valuetype=integer result=((value)))
	read (any valuetype=3 result=((value)))
	read (any valuetype=boolean value="true" result=((guid)))
	read (any valuetype=200 result=((datatype value)))
	read (any valuetype=integer comparator="number" value>"9" result=((value)))
	read (any valuetype=integer comparator="number" sort=value result=((value)))
	read (any valuetype=float comparator="number" value<"0" result=((value)))
	read (any valuetype=float comparator="number" sort=-value result=((value)))
	read (any valuetype=null result=((guid value)))
	write (valuetype=256 value="x")
	write (valuetype=0 value="x")
	write (valuetype=bogus value="x")
	EOF
rm -rf $D
