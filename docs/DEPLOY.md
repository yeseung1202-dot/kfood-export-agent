# 설명 페이지·데모 배포 안내 (Claude Code용)

작성 2026-11-01 (claude.ai 세션). 목표: 11/2 배포 → 11/3 지원서 '구축방안' 칸 끝에 링크 한 줄 → 제출.

## 만든 것
- `index.html` (이 폴더 루트): 과정 설명 페이지 1장. 외부 의존은 Google Fonts뿐, 데이터는 페이지 안에 내장
  - 차트 값은 data/scores.csv · data/manual/feasibility.csv · data/sensitivity.csv 에서 코드로 뽑음 (2026-11-01 대조 완료)
  - 결과 파일을 다시 돌려 숫자가 바뀌면 `index.html`의 `const DATA = ...`와 본문 숫자도 같이 고칠 것
  - 맨 아래 `const LINKS = { demo: "", repo: "" };` 에 주소를 넣으면 버튼이 나타남(비어 있으면 숨김)
- `.nojekyll`: GitHub Pages가 파일을 가공하지 않게

## 1. 공개 저장소 만들기 (GitHub)
올리기 전에 확인:
- `keys.txt`, `.env`는 .gitignore로 제외됨 → `git status`로 한 번 더 확인
- 공개해도 되는지 검토: `docs/HANDOFF_COWORK.md`, `docs/REVIEW_TODO.md`(내부 메모), `data/internal/`(가상 샘플만이면 OK), `__pycache__/`
- `requirements.txt` 추가: `streamlit`, `pandas`, `numpy` (+ score.py가 쓰는 것)

## 2. GitHub Pages (설명 페이지)
Settings → Pages → Source: Deploy from a branch → `main` / `/ (root)` → 저장
→ `https://<아이디>.github.io/<저장소>/` 에서 index.html이 열림

## 3. Streamlit Community Cloud (데모) — 2026-11-01 하지 않기로 함
심사위원이 잠든 앱을 깨우다 기다리게 될 수 있어 설명 페이지만 배포. 데모 버튼은 LINKS.demo를 비워 숨김, 코드 저장소 버튼만 표시.

share.streamlit.io → GitHub 로그인 → New app → 저장소 / `main` / `app.py`
- app.py는 API 키 없이 data/*.csv만 읽음. report.py(LLM)는 배포에서 실행하지 않음
- 무료 앱은 오래 안 쓰면 잠들어 깨우는 데 수십 초 → 첫 링크는 설명 페이지로, 데모는 페이지 안 버튼으로

## 4. 링크 반영
- 배포 완료(2026-11-01): 설명 페이지 https://yeseung1202-dot.github.io/kfood-export-agent/ · 저장소 https://github.com/yeseung1202-dot/kfood-export-agent
- `index.html`의 LINKS에 두 주소 입력 → 커밋·푸시
- 지원서 '구축방안' 칸 끝에 한 줄: `■ 설명 페이지·데모: https://...` (현재 2,281자라 여유 있음)
- 시크릿 창에서 링크가 로그인 없이 열리는지 확인
