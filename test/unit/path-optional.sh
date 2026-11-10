#!/bin/bash
#
# Path search: optional subconstraints in a step don't change the answer.
#
# Optional subconstraints (as MQL reads each node's fields) don't stop a
# step being walked as a chain, or a search stopping at a page of the
# nearest matches. Each query here runs twice, with a subconstraint on
# the step's root that rules nothing out (no link has its type): once
# optional, so graphd may skip it, and once count=0, which isn't
# optional, so graphd must use its general walk. The two replies must be the same; each pair prints as
# "same" and the reply, or "DIFFER" and both.
#
# The graph: twelve people and ten papers, with author links from each
# paper to its authors, and a label on each person and paper but p5:
#
#   q0: p0 p1      q3: p0 p5      q6: p2 p7         q9: p4 p10
#   q1: p1 p2 p3   q4: p5 p6      q7: p7 p8 p9      (p11: none)
#   q2: p3 p4      q5: p6 p4      q8: p9 p10
#
# The steps: co-author (person, paper, person) with optional labels read
# on the root, on the paper, on both; with an optional subconstraint that
# has its own subconstraints; either way along an author link with labels
# beside the "or"; and an optional link to "*", which can't be skipped.

B=`basename $0 .sh`
cd `dirname $0`
source ./rungraphd

P=00000012400034568
g() { printf '%s%015x' $P $1; }
A=$(g 0)     # author links: left=paper right=person
L=$(g 1)     # labels
NONE=$(g 2)  # no link has this type
person() { g $(( 3 + $1 )); }
paper() { g $(( 15 + $1 )); }
PAPERS=("0 1" "1 2 3" "3 4" "0 5" "5 6" "6 4" "2 7" "7 8 9" "9 10" "4 10")

LABEL="(<-left typeguid=$L optional result=((value)))"
# The same subconstraint, optional (skippable) or count=0 (not).
SKIP="(<-left typeguid=$NONE optional result=none)"
FORCE="(<-left typeguid=$NONE count=0 result=none)"
PAPERS_OF="(<-right typeguid=$A optional result=((contents)) left->(result=((guid)) $LABEL))"

co() {  # co <root extras> <paper extras>
	echo "step=(result=((contents)) $1 (<-right typeguid=$A result=((contents)) left->(result=((guid contents)) $2 (<-left typeguid=$A result=() right->(*)))))"
}
either() {  # either <root extras>
	echo "step=(result=((contents)) $1 {(<-left typeguid=$A result=((guid)) right->(*))} | {(<-right typeguid=$A result=((guid)) left->(*))})"
}
optstar() {  # optstar <root extras>
	echo "step=(result=((contents)) $1 $LABEL (<-right typeguid=$A optional result=((contents)) left->(*)))"
}

# Each step as written, and forced onto the general walk.
STEPS=(
	"$(co "$LABEL $SKIP" "")" "$(co "$LABEL $FORCE" "")"
	"$(co "$SKIP" "$LABEL")" "$(co "$FORCE" "$LABEL")"
	"$(co "$LABEL $SKIP" "$LABEL")" "$(co "$LABEL $FORCE" "$LABEL")"
	"$(co "$PAPERS_OF $SKIP" "")" "$(co "$PAPERS_OF $FORCE" "")"
	"$(either "$LABEL $SKIP")" "$(either "$LABEL $FORCE")"
	"$(optstar "$SKIP")" "$(optstar "$FORCE")"
)

queries() {  # queries <step>
	echo "read (guid=$(person 0) result=((contents)) (<-path $1 result=((guid steps))))"
	echo "read (guid=$(person 0) result=((contents)) (<-path $1 guid=$(person 10) result=((steps path via))))"
	echo "read (guid=$(person 0) result=((contents)) (<-path $1 guid=$(person 4) pathlimit=all result=((path-count (path via)))))"
	echo "read (guid=$(person 0) result=((contents)) (<-path $1 pagesize=3 result=((guid steps contents)) $LABEL))"
	echo "read (guid=$(person 0) result=((contents)) (<-path $1 steps<=2 result=(count)))"
	echo "read (guid=$(person 11) result=((contents)) (<-path $1 optional result=(count)))"
}
NQ=6

rm -rf $D
{
	echo 'write (name="author")'
	echo 'write (name="label")'
	echo 'write (name="none")'
	for (( i = 0; i < 12; i++ )); do echo "write (name=\"p$i\")"; done
	for (( i = 0; i < 10; i++ )); do echo "write (name=\"q$i\")"; done
	for (( i = 0; i < 10; i++ )); do
		for a in ${PAPERS[$i]}; do
			echo "write (typeguid=$A left=$(paper $i) right=$(person $a))"
		done
	done
	for (( i = 0; i < 12; i++ )); do
		(( i == 5 )) || echo "write (typeguid=$L left=$(person $i) value=\"P$i\")"
	done
	for (( i = 0; i < 10; i++ )); do
		echo "write (typeguid=$L left=$(paper $i) value=\"Q$i\")"
	done
	for (( s = 0; s < ${#STEPS[@]}; s += 2 )); do
		queries "${STEPS[$s]}"
		queries "${STEPS[$s + 1]}"
	done
} | rungraphd -d${D} -bty | tail -$(( ${#STEPS[@]} * NQ )) | awk -v nq=$NQ '
	{ reply[NR] = $0 }
	END {
		for (base = 0; base < NR; base += 2 * nq) {
			print "-- step " base / (2 * nq) + 1
			for (i = 1; i <= nq; i++) {
				fast = reply[base + i]; slow = reply[base + nq + i]
				if (fast == slow) print "same " fast
				else { print "DIFFER " fast; print "   general walk: " slow }
			}
		}
	}'
rm -rf $D
