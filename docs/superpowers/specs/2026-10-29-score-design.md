# score.py 설계 (2026-10-29 합의)

K-푸드(제과·빙과) 수출 유망국 TOP3를 도출하는 스코어링 모듈. `collect.py` 출력과 수동 자료를 받아 2단계로 점수를 매기고, 몬테카를로로 결과 안정성을 검증한다.

## 1. 핵심 결정

| 항목 | 결정 |
|---|---|
| '유망' 정의 | **성장·여백 중심**. 시장 규모는 탈락조건(gate)에만 쓰고 점수에는 넣지 않음 |
| 구조 | `score.py` 단일 파일 + 상단 `INDICATORS` 표. collect.py와 같은 스타일 |
| 1단계 | 과제 3축(성장성·트렌드·수출입) 가중평균, 초안 각 1/3 → TOP10 |
| 2단계 | 롯데 실행가능성 점수, 최종 = (1−α)×1단계 + α×실행가능성, α 초안 0.3 |
| 트렌드 축 | Google Trends 국가별 CSV + KOFICE 한식 호감도 (수동) |
| 한국 점유율 | 여백 = `share_jp − share_kr` (높을수록 좋음) |
| 생산거점국 | 후보에서 제외, 2단계 리드타임 출발지로만 사용 |

## 2. 입출력

**입력**
- `data/country_features.csv`: collect.py 출력 (37개국)
- `data/manual/trend.csv`: 국가(한글명)별 `trend_korean_food`, `trend_pepero`, `kofice_hansik_favor`. 원본 다운로드 파일은 `data/manual/raw/`에 보관 (출처 증빙)
- `data/manual/feasibility.csv`: 2단계 입력. 국가, 공급거점명, 아래 2단계 지표, 항목별 출처 열

**출력**
- `data/scores.csv`: 국가별 축 점수 3개, 1단계 점수·순위, 실행가능성 점수, 최종 점수·순위, 탈락 사유, 플래그(데이터 부족 / 실행가능성 미입력)
- `data/drivers.json`: 국가별 점수를 올린 지표·깎은 지표 각 상위 3개 (원값, 백분위, 출처, 기준연도). LLM 리포트가 쓰는 유일한 수치 입력
- `data/sensitivity.csv`: 국가별 TOP3 진입률, 1위 비율, 안정 여부

**선행 수정 (collect.py)**: World Bank 최신값의 기준연도를 `wb_<지표>_year` 열로 남긴다. 현재 `build_features`에서 연도가 사라져 기준연도를 표기할 수 없음.

## 3. 처리 흐름

1. 로드·병합 (`country_features` ← `trend` ← `feasibility`, 키는 한글 국가명)
2. 파생 열: `gap_jp_kr = share_jp − share_kr`. 단 `share_jp < MIN_JP_SHARE`(0.5%)이면 결측 — 한·일 둘 다 없는 시장은 '여백'이 아니라 '아시아 과자 수요 미검증' (첫 실행에서 온두라스가 gap=0으로 과대평가된 것을 보고 추가, 2026-10-29). 작은 기저 처리: 기준연도 한국 수출액 < `MIN_BASE_USD`(10만 달러)이면 해당 증가율을 결측으로 처리
   - `kr_exp_cagr_21_25` ← `kr_exp_2021_usd` 기준
   - `kr_exp_ytd_growth_26` ← 2025 1~9월 수출액 기준 (collect.py에 `kr_exp_2025ytd_usd` 열 추가 필요)
3. 탈락조건 → 사유 기록 후 이후 계산에서 제외
   - `imp_total_usd < MIN_MARKET_USD` (초안 5천만 달러, 첫 수집 후 분포 보고 확정): "제과류 수입시장 규모 미달"
   - `lotte_base == True` (인도·카자흐스탄·파키스탄·러시아·미얀마): "롯데 현지 생산거점 운영 중"
   - `EXCLUDE` 사전: 일본 "원롯데 일본 롯데제과 홈마켓", 미얀마 "내전" (미얀마는 lotte_base와 중복, 사유는 둘 다 기록)
4. 1단계: 통과국 안에서 지표별 백분위(0~100, `rank(pct=True)`, 동점은 평균) → 방향이 ↓이면 100−값 → 축 점수 = 축 내 유효 지표 평균 → 1단계 = 유효 축의 가중평균(가중치 재정규화)
5. TOP10 선정 → 2단계: 같은 함수로 TOP10 안에서 실행가능성 점수(축 1개) → 최종 점수·순위
6. 근거 지표 추출 → drivers.json
7. 몬테카를로 → sensitivity.csv

## 4. 지표

### 1단계

| 축 | 열 | 방향 | 출처·기준연도 |
|---|---|---|---|
| ① 성장성 | `imp_cagr` | ↑ | UN Comtrade, 국가별 최초~최신 보고연도 (`ct_latest_year`) |
| | `imf_gdp_growth_fcst_26_30` | ↑ | IMF WEO, 2026~2030 전망 평균 |
| | `imf_gdp_pc_2026` | ↑ | IMF WEO, 2026 |
| | `wb_0_14세비중` | ↑ | World Bank, 최신값 (`wb_0_14세비중_year`) |
| | `wb_도시화율` | ↑ | World Bank, 최신값 |
| ② 트렌드 | `trend_korean_food` | ↑ | Google Trends, 최근 12개월 |
| | `trend_pepero` | ↑ | Google Trends, 최근 12개월 |
| | `kofice_hansik_favor` | ↑ | KOFICE 해외한류실태조사 (조사 대상국만) |
| ③ 수출입 | `kr_exp_cagr_21_25` | ↑ | 관세청, 2021→2025 |
| | `kr_exp_ytd_growth_26` | ↑ | 관세청, 2026년 1~9월 전년 동기 대비 |
| | `gap_jp_kr` | ↑ | UN Comtrade, 최신 보고연도 |

점수에 넣지 않는 열: 인구·`imp_total_usd`(규모는 탈락조건 전용), `wb_1인당GDP_USD`(IMF와 중복, WB에 대만 없음), 인터넷이용률·물가.

### 2단계 (feasibility.csv)

| 열 | 방향 | 비고 |
|---|---|---|
| `lead_time_days` | ↓ | 가장 가까운 롯데 공급거점(한국·인도·카자흐스탄·일본 롯데 동남아망·싱가포르 JV) 기준, `supply_base` 열에 거점명 |
| `lpi_2023` | ↑ | World Bank LPI 2023 종합점수. 주관적 콜드체인 등급 대신 공식 지표 사용 (2026-10-30 변경), 빙과 콜드체인은 리포트에서 정성 서술 |
| `tariff_hs1905_pct` | ↓ | WITS / MacMap 적용 관세율 |
| `halal_required` | ↓ | 0/1 |
| `sugar_tax` | ↓ | 0/1. ZERO 브랜드 기회 측면은 리포트에서 언급 |
| `oecd_risk` | ↓ | OECD 국가위험등급 0~7 |

### 결측 처리
- **중립 수축** (2026-10-30 추가): 축 점수 = 유효 지표 비율 × 유효 지표 평균 + (1 − 비율) × 50. Pepero 1개뿐인 몽골이 트렌드 100점으로 1위가 된 것을 보고 도입 — 지표 1개가 축을 좌우하지 못하게 함
- 전원 결측인 지표(예: 미입력 KOFICE)는 정보가 없으므로 비율 계산에서 제외
- 축 내 유효 지표가 절반 미만이면 플래그 `데이터 부족(축명)`
- 트렌드 결측 보강: Google Trends 국가별 표에 안 나오는 소국(몽골·캄보디아·쿠웨이트·카타르)은 '한국 요리' 주제 지역 비교(싱가포르=100, 52주 평균비)로 환산, `trend_korean_food_src`에 환산 표기. 'korean snack'은 해당국 검색량 부족으로 결측 유지(0으로 채우지 않음)
- 축 전체가 결측이면 해당 축을 빼고 나머지 축 가중치를 재정규화, 같은 플래그
- feasibility 미입력국은 최종 = 1단계, 플래그 `실행가능성 미입력`

## 5. 함수 인터페이스

- `score(feats, weights=(1/3,1/3,1/3), alpha=0.3, drop=None) -> DataFrame`: 위 1~5단계. `drop`은 제외할 지표 열 이름 (몬테카를로용). Streamlit 데모가 이 함수를 그대로 import
- `drivers(scored, feats) -> dict`: 근거 지표 추출
- `sensitivity(feats, runs=2000, seed=42) -> DataFrame`: 몬테카를로
- `main()`: CLI. `--weights w1 w2 w3 --alpha A --runs N --seed S`

## 6. 몬테카를로 안정성 검증

- 반복마다: 가중치 ~ `Dirichlet(DIRICHLET_CONC × [1,1,1])` (`DIRICHLET_CONC = 5`, 각 축 대략 0.15~0.55), α ~ U(0.1, 0.5), 1단계·2단계 지표 중 1개 무작위 제외
- 집계: 국가별 TOP3 진입률, 1위 비율. TOP3 진입률 ≥ `STABLE_RATE`(0.7)이면 '안정'
- TOP10 진입률이 `NEED_RATE`(20%) 이상인 국가 중 feasibility 미입력국을 콘솔에 출력 → 그 국가만 수동 입력 (한 번이라도 진입 기준은 27개국이라 과다, 2026-10-29 조정)

## 7. 오류 처리

- `country_features.csv` 없음 → "collect.py를 먼저 실행하세요" 출력 후 종료
- `trend.csv` / `feasibility.csv` 없음 → 경고 후 해당 지표 결측으로 계속
- `INDICATORS`의 열이 데이터에 없음 → 경고 후 건너뜀 (오탈자 방지)

## 8. 조정 상수 (score.py 상단)

`MIN_MARKET_USD = 50_000_000` (첫 수집 분포에서 3,360만→1억 660만 달러 사이 공백, 확정), `MIN_BASE_USD = 100_000`, `MIN_JP_SHARE = 0.005`, `NEED_RATE = 0.2`, `TOP_N = 10`, `ALPHA = 0.3`, `DIRICHLET_CONC = 5`, `STABLE_RATE = 0.7`, `EXCLUDE = {...}`

## 9. 테스트

`test_score.py` 하나, 5개국 합성 데이터, assert만 사용, `python test_score.py`로 실행.
1. ↓ 지표의 방향 반전
2. 결측 지표가 0점이 아니라 축 내 나머지 평균으로 처리됨
3. 탈락조건 적용과 사유 기록
4. 작은 기저(< MIN_BASE_USD) 증가율이 결측 처리됨
5. 같은 seed → 같은 sensitivity 결과

## 10. 범위 밖

- LLM 리포트 생성, Streamlit UI, 내부 데이터 슬롯: 11/1 별도 작업 (`score()`와 `drivers.json`만 맞춰 둠)
- Euromonitor 보강: 접근 확인 후 ① 축 지표로 추가

## 11. 변경: 두 트랙 (2026-10-30)
- 단일 트랙 결과 TOP3(싱가포르·필리핀·캐나다) 중 필리핀·캐나다가 한국 제과 수출 4·5위 기존 주력시장 → '발굴' 취지와 어긋남. 원인은 트렌드(브랜드 검색)·수출 증가율 지표가 기존 침투 시장에 유리하기 때문
- `MAJOR_MARKET_USD = 20,000,000`: 2025 한국 제과 수출이 이 이상이면 기존 주력시장
- `score(..., track="new")`: 기존 주력 제외 = 신규 개척형(메인), `track="expand"`: 기존 주력만 = 확대형(참고). 출력 파일은 신규가 접미사 없음, 확대형이 `_expand`
- `REEXPORT_HUB`(싱가포르·네덜란드·홍콩·UAE): 점수 영향 없이 플래그만
- 유럽 공급거점(벨기에 길리안)은 빼빼로 미생산이라 미반영, 부산 기준 유지
