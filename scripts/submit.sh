#!/usr/bin/env bash
# Prepare the three things for DL: the archive of the lab tag, the PDF report and the tag link.
#
#   scripts/submit.sh <web|dpc> <N>        e.g. scripts/submit.sh web 2
#
# Checks first: the working tree is clean, the tag <course>-lrN exists and is pushed to origin,
# its page on GitHub answers, and the PDF report has exactly this tag link under "Код програми".
# Writes submit/<course>-lrN/: parking-system-<course>-lrN.zip, Звіт_<Курс>_ЛРN.pdf, link.txt.
# Needs: git, curl, uv (reads the PDF text with pymupdf). submit/ is in .gitignore.
set -euo pipefail

die() { echo "submit: $*" >&2; exit 1; }

[ $# -eq 2 ] || die "usage: scripts/submit.sh <web|dpc> <N>"
COURSE=$1; N=$2
case "$COURSE" in
  web) TITLE=Web ;;
  dpc) TITLE=DPC ;;
  *) die "course must be web or dpc, not '$COURSE'" ;;
esac
[[ "$N" =~ ^[1-6]$ ]] || die "lab number must be 1..6, not '$N'"

cd "$(dirname "$0")/.."
TAG="$COURSE-lr$N"
OUT="submit/$TAG"
PDF="docs/reports/out/Звіт_${TITLE}_ЛР${N}.pdf"

# 1. clean tree, tag exists locally and on origin
[ -z "$(git status --porcelain)" ] || die "the working tree is not clean (git status)"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || die "no tag $TAG (git tag -a $TAG -m \"$TITLE ЛР$N\")"
LOCAL=$(git rev-parse "refs/tags/$TAG^{commit}")
REMOTE=$(git ls-remote --tags origin | awk -v r="refs/tags/$TAG^{}" '$2 == r {print $1}')
[ -n "$REMOTE" ] || die "tag $TAG is not on origin (git push origin main --follow-tags)"
[ "$REMOTE" = "$LOCAL" ] || die "tag $TAG on origin points to ${REMOTE:0:7}, locally to ${LOCAL:0:7}"

REPO=$(git remote get-url origin | sed -E 's#^git@([^:]+):#https://\1/#; s#\.git$##; s#/$##')
URL="$REPO/tree/$TAG"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -L --max-time 20 "$URL" || true)
[ "$CODE" = "200" ] || die "the tag page $URL answers $CODE, not 200"

[ -f "$PDF" ] || die "no $PDF (uv run scripts/build_report.py docs/reports/$TAG.md --pdf)"

# 5. the link in the PDF must be the tag link (checked before anything is written)
LINKS=$(PYTHONIOENCODING=utf-8 uv run -q --no-project --python 3.13 --with pymupdf python -c '
import re, sys, pymupdf
text = "".join(p.get_text() for p in pymupdf.open(sys.argv[1]))
print("\n".join(re.findall(r"Повний код роботи:\s*(\S+)", text)))
' "$PDF")
[ "$LINKS" = "$URL" ] || die "the PDF links to '${LINKS:-nothing}', expected $URL (rebuild the report)"

# 2-4. archive, PDF, link
rm -rf "$OUT"
mkdir -p "$OUT"
ZIP="$OUT/parking-system-$TAG.zip"
git archive --format=zip --prefix=parking-system/ -o "$ZIP" "$TAG"
cp "$PDF" "$OUT/"
printf '%s\n' "$URL" > "$OUT/link.txt"

# 6. what goes to DL
echo "Для DL ($TITLE ЛР$N, тег $TAG, коміт ${LOCAL:0:7}):"
echo "  1. архів:     $ZIP ($(du -h "$ZIP" | cut -f1))"
echo "  2. звіт PDF:  $OUT/$(basename "$PDF")"
echo "  3. посилання: $URL"
