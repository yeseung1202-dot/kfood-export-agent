# K-푸드 수출 유망국 공개데이터 수집기

LOTTE AX Challenge 2026 · 롯데웰푸드 과제(K-푸드 수출 유망국가 발굴) 예선용

## 실행

```bash
pip install requests pandas
# keys.txt 를 메모장으로 열어 키 2개 입력 후 저장
python collect.py
```

- 관세청 약 1,000회 호출(10분 내외) + Comtrade 20회 + World Bank 7회 + IMF 3회
- 한 번 받은 소스는 `data/raw/`에 저장되고 다음 실행 때 재사용됨. 다시 받으려면 `--refresh`
- 일부만: `python collect.py --only wb imf` (키 필요 없는 것부터 테스트할 때 유용)

## 결과물

| 파일 | 내용 |
|---|---|
| `data/country_features.csv` | 37개국 × 지표 요약표 (스코어링 입력) |
| `data/raw/*.csv` | 소스별 원자료 |
| `data/collect_log.json` | 수집 시각, 소스 URL, 경고 목록 |

### country_features.csv 주요 열

| 열 | 의미 | 출처 |
|---|---|---|
| `kr_exp_2025_usd` | 한국→해당국 제과류(4개 HS) 2025년 수출액 | 관세청 |
| `kr_exp_cagr_21_25` | 위 수출액의 2021→2025 연평균 성장률 | 관세청 |
| `kr_exp_ytd_growth_26` | 2026년 1~9월 수출 전년 동기 대비 증감률 | 관세청 |
| `kr_exp_2025_hs1905` 등 | HS별 2025년 수출액 | 관세청 |
| `imp_total_usd` | 해당국의 제과류 전체 수입액(최신 보고연도) | Comtrade |
| `imp_cagr` | 수입 시장 연평균 성장률 | Comtrade |
| `share_kr / share_jp / share_cn` | 수입시장 내 한국·일본·중국 점유율 | Comtrade |
| `ct_latest_year` | Comtrade 최신 보고연도 (국가별로 다를 수 있음) | Comtrade |
| `wb_*` | 인구, 1인당 GDP, 도시화율, 연령구조, 인터넷 이용률(최신값) | World Bank |
| `imf_gdp_growth_fcst_26_30` | 2026~2030 실질 GDP 성장률 전망 평균 | IMF WEO |

## 스코어링 (score.py)

```bash
python score.py                                   # 기본: 3축 균등, α=0.3, 몬테카를로 2,000회(약 80초)
python score.py --weights 0.5 0.25 0.25 --alpha 0.3
python test_score.py                              # 자체 점검
```

- 입력: `data/country_features.csv` + 수동 입력 `data/manual/trend.csv`, `data/manual/feasibility.csv` (국가명은 `COUNTRIES` 한글명과 동일, 항목별 출처는 `<열>_src`)
- 출력: `data/scores.csv`(점수·순위·탈락 사유), `data/drivers.json`(국가별 근거 지표 + 출처·기준연도), `data/sensitivity.csv`(TOP3 진입률)
- 두 트랙: 2025 한국 제과 수출 2,000만 달러 이상(`MAJOR_MARKET_USD`) = 기존 주력 → 접미사 없는 파일은 **신규 개척형**, `*_expand`는 **기존 주력 확대형**
- 설계: `docs/superpowers/specs/2026-10-29-score-design.md`
- 실행가능성 입력 재생성: `python build_feasibility.py .` (값·출처가 스크립트에 기록돼 있음)

## 리포트·데모

```bash
python report.py                # docs/reports/top3_report.md (신규 개척형 TOP3 + 확대형 참고표)
python report.py --template     # LLM 없이 템플릿으로
streamlit run app.py            # 트랙 선택·가중치 슬라이더·근거·안정성·탈락국·리포트 화면
```

- LLM 키 우선순위(keys.txt): `CLOVA_STUDIO_API_KEY`(HyperCLOVA X, 모델 `CLOVA_MODEL`, 기본 HCX-007) → `OPENAI_API_KEY` → 없으면 템플릿
- LLM은 숫자 자리에 `{n}`만 쓰고 코드가 근거 값을 넣음. 작성 → LLM 검토 → 코드 검사(자리표시 밖 숫자·붙여 쓴 자리표시·잘린 응답이면 재생성, 최대 3회) → `verify()` 숫자 대조
- 사람이 해석 오류를 고친 **검토 수정본**은 `docs/reports/top3_report_final.md` (앱은 이 파일을 우선 표시). report.py를 다시 돌려도 수정본은 그대로이므로, 점수가 바뀌면 다시 검토할 것
- 롯데 내부 데이터 슬롯: `data/internal/internal.csv`(country, internal_margin_pct, internal_margin_pct_src)가 있으면 자동 반영. `sample_internal.csv`는 **가상 샘플**로, 앱 토글을 켤 때만 쓰임

## 알아둘 점

- HS코드: 1905 비스킷류(빼빼로), 1806 초콜릿, 2105 아이스크림, 1704 설탕과자
- 관세청은 매월 15일경 전월까지 갱신 → 2026년 9월까지 반영
- Comtrade는 국가별 보고가 늦어 2025년이 비는 국가가 많음 → 국가마다 최신 보고연도를 따로 씀
- 대만은 World Bank에 없어 `wb_*`가 비어 있음(정상)
- 첫 실행 후 한 국가(예: 미국 2025 HS1905)를 관세청 수출입무역통계 누리집 값과 대조해 합계가 맞는지 확인할 것
- 후보국 추가/삭제는 `collect.py` 상단 `COUNTRIES` 목록 수정
