#!/bin/bash
#
# Path search: steps with alternatives take the chain walk.
#
# On a 20 by 20 grid, with east and south links, searches whose step
# has alternatives - either way along a link, or three one-link chains -
# must allocate fewer than 1000 values (the cost's va=), where the
# general walk, which evaluates the step template once per combination
# of alternatives, allocates tens of thousands. A search with an "or"
# beside a subconstraint every step needs takes the general walk.
#
# Optional subconstraints (as MQL reads a node's fields) don't change
# where a step goes: steps with them beside the "or", along a chain, or
# on the <-path constraint itself still take the chain walk, or stop at
# a page of the nearest matches. A count=0 subconstraint rules nodes
# out, so its step takes the general walk.
#
# Each reply prints as whether va stayed under 1000, then the answer
# without the cost, whose timings vary.

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

P=00000012400034568
g() { printf '%s%015x' $P $1; }
N=20
E=$(g 0)
S=$(g 1)
node() { g $(( 2 + $1 * N + $2 )); }
BOTH="step=(result=(contents) {(<-left typeguid=($E $S) right->(*))} | {(<-right typeguid=($E $S) left->(*))})"
THREE="step=(result=(contents) {(<-left typeguid=$E right->(*))} | {(<-right typeguid=$E left->(*))} | {(<-left typeguid=$S right->(*))})"
NEEDS="step=(result=(contents) (<-left) {(<-left typeguid=($E $S) right->(*))} | {(<-right typeguid=($E $S) left->(*))})"
NAME="(<-left optional typeguid=$S result=((guid)))"
OPTOR="step=(result=(contents) $NAME {(<-left typeguid=($E $S) result=((contents)) right->(*))} | {(<-right typeguid=($E $S) result=((contents)) left->(*))})"
CHAIN="step=(result=(contents) $NAME (<-left typeguid=$E result=((contents)) $NAME right->(*)))"
FORBID="step=(result=(contents) (<-left typeguid=$S count=0) {(<-left typeguid=($E $S) right->(*))} | {(<-right typeguid=($E $S) left->(*))})"

rm -rf $D
{
	echo 'write (name="east")'
	echo 'write (name="south")'
	for (( r = 0; r < N; r++ )); do
		for (( c = 0; c < N; c++ )); do
			echo "write (name=\"g$r-$c\")"
		done
	done
	for (( r = 0; r < N; r++ )); do
		for (( c = 0; c < N; c++ )); do
			(( c < N - 1 )) && echo "write (typeguid=$E left=$(node $r $c) right=$(node $r $(( c + 1 ))))"
			(( r < N - 1 )) && echo "write (typeguid=$S left=$(node $r $c) right=$(node $(( r + 1 )) $c))"
		done
	done
	echo "read cost=\"\" (guid=$(node 0 0) result=((name contents)) (<-path $BOTH guid=$(node 19 19) result=((steps))))"
	echo "read cost=\"\" (guid=$(node 10 10) result=((name contents)) (<-path $BOTH result=(count)))"
	echo "read cost=\"\" (guid=$(node 10 10) result=((name contents)) (<-path $BOTH steps<=3 result=(count)))"
	echo "read cost=\"\" (guid=$(node 0 19) result=((name contents)) (<-path $THREE guid=$(node 19 0) result=((steps))))"
	echo "read cost=\"\" (guid=$(node 10 10) result=((name contents)) (<-path $NEEDS result=(count)))"
	echo "read cost=\"\" (guid=$(node 10 10) result=((name contents)) (<-path $OPTOR result=(count)))"
	echo "read cost=\"\" (guid=$(node 0 0) result=((name contents)) (<-path $OPTOR guid=$(node 19 19) result=((steps))))"
	echo "read cost=\"\" (guid=$(node 0 0) result=((name contents)) (<-path $CHAIN guid=$(node 0 19) result=((steps))))"
	echo "read cost=\"\" (guid=$(node 10 10) result=((name contents)) (<-path $BOTH pagesize=3 result=((name contents)) $NAME))"
	echo "read cost=\"\" (guid=$(node 19 10) result=((name contents)) (<-path $FORBID result=(count)))"
} | rungraphd -d${D} -bty | tail -10 | awk '{
	va = $0; sub(/.*va=/, "", va); sub(/".*/, "", va)
	answer = $0; sub(/cost="[^"]*" /, "", answer)
	print (va + 0 < 1000 ? "va<1000 " : "va>=1000 ") answer
}' 
rm -rf $D
