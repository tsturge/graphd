#!/bin/bash
#
# Restore a version "2" dump (no txstart column) and read it back.
#
# parse_record() used to leave r_v6_txstart uninitialized for
# versions before 6; the stack garbage leaked into the primitive's
# flag byte, so restored records were bigger than their allocation
# ("attempt to write internally inconsistent record") and unreadable.
#
# Covers: live/archival flags, name, value, links, prev (versioning),
# and txstart=false in the version 6 dump of the restored records.

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

rm -rf $D
rungraphd -d${D} -bty <<-'EOF'
	restore ("2" 0 4 (113a9a8400001c06a301d5e800000000 null null null null 0 true true 2006-04-25T22:45:53.0000 0 0 0) (113a9a8400041c06a301d5e800000001 null "name1" string "hello" 0 true false 2006-04-25T22:45:53.0001 0 0 0) (113a9a8400081c06a301d5e800000002 113a9a8400001c06a301d5e800000000 null string "world" 0 false true 2006-04-25T22:45:53.0002 113a9a8400041c06a301d5e800000001 113a9a8400001c06a301d5e800000000 0) (113a9a84000c1c06a301d5e800000003 null null string "hello2" 0 true true 2006-04-25T22:45:53.0003 0 0 113a9a8400041c06a301d5e800000001))
	read (any result=((guid name value datatype live archival timestamp)))
	read (any newest>=0 result=((guid name value live archival)))
	read (any value="world" live=false result=((guid typeguid left right live archival)))
	read (any value="hello2" result=((guid previous value)))
	dump (start=0 end=4)
	EOF
rm -rf $D
