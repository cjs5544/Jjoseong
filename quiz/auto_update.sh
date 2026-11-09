#!/bin/bash
# 매일 cron: 볼트 문제집 HTML이 바뀌었을 때만 다시 암호화해 W&B 저장소에 push.
# (암호화 결과는 매번 달라지므로 원본 해시로 변경을 판단)
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin HOME="${HOME:-/home/js-linux}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="/mnt/ssd/obsidian/10_Research/02_헬기_시스템/00_일반/문제집"
STAMP="$HOME/.cache/aw139_quiz_src.sha"
log(){ echo "[$(date '+%F %T')] $*"; }
mkdir -p "$(dirname "$STAMP")"
NEW="$(cd "$SRC" && sha256sum 검증문제은행/검증문제은행.html AW139_기종전환_평가_인터랙티브_CBT.html AW139_종합평가_인터랙티브_CBT.html AW139_교관_시험비행_평가_인터랙티브_CBT.html)"
[ -f "$STAMP" ] && [ "$NEW" = "$(cat "$STAMP")" ] && { log "변경 없음"; exit 0; }
cd "$REPO"
git pull -q --ff-only origin main
bash quiz/build.sh >/dev/null
git add quiz/bank.html quiz/transition.html quiz/general.html quiz/instructor.html quiz/.staticrypt.json
git commit -q -m "문제집 자동 갱신(볼트 문제집 HTML 변경)" -- quiz/bank.html quiz/transition.html quiz/general.html quiz/instructor.html quiz/.staticrypt.json || true
git push -q origin main
echo "$NEW" > "$STAMP"
log "갱신·push 완료 $(git log -1 --format=%h)"
