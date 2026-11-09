#!/bin/bash
# 볼트 문제집 HTML 4개를 비밀번호로 암호화해 quiz/에 넣는다(공개 저장소라 원문을 올리지 않음).
# 비밀번호는 저장소 밖 파일 ~/.aw139_quiz_pw 에서 읽는다. 실행: bash quiz/build.sh
set -euo pipefail
SRC="/mnt/ssd/obsidian/10_Research/02_헬기_시스템/00_일반/문제집"
PW_FILE="${AW139_QUIZ_PW_FILE:-$HOME/.aw139_quiz_pw}"
OUT="$(cd "$(dirname "$0")" && pwd)"
[ -s "$PW_FILE" ] || { echo "비밀번호 파일이 없습니다: $PW_FILE"; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cp "$SRC/검증문제은행/검증문제은행.html" "$TMP/bank.html"
cp "$SRC/AW139_기종전환_평가_인터랙티브_CBT.html" "$TMP/transition.html"
cp "$SRC/AW139_종합평가_인터랙티브_CBT.html" "$TMP/general.html"
cp "$SRC/AW139_교관_시험비행_평가_인터랙티브_CBT.html" "$TMP/instructor.html"
cd "$TMP"   # staticrypt는 -c·-d 경로를 현재 폴더 기준으로 씀
[ -f "$OUT/.staticrypt.json" ] && cp "$OUT/.staticrypt.json" config.json
STATICRYPT_PASSWORD="$(cat "$PW_FILE")" npx -y staticrypt@3.5.4 bank.html transition.html general.html instructor.html \
  -d out -c config.json --short --remember 30 \
  --template-title "AW139 문제집" --template-instructions "비밀번호를 입력하세요" \
  --template-button "열기" --template-placeholder "비밀번호" --template-remember "30일 기억" \
  --template-error "비밀번호가 틀렸습니다"
mv out/*.html "$OUT/"; cp config.json "$OUT/.staticrypt.json"   # salt 보존(30일 기억 유지)
# 결과에 원문 문장이 남지 않았는지 확인
for f in bank transition general instructor; do
  ! grep -q "AW139 비행교범\|ENGINE\|HYD" "$OUT/$f.html" || { echo "암호화 안 된 내용 발견: $f.html"; exit 1; }
done
echo "완료: $OUT/{bank,transition,general,instructor}.html"
