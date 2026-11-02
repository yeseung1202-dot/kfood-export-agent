"""
K-제과 수출 유망국 발굴 에이전트 - 데모 화면 (LOTTE AX Challenge 2026 / 롯데웰푸드 과제)

실행: streamlit run app.py
  - 가중치·α 슬라이더를 움직이면 score.score()로 즉시 재계산
  - 몬테카를로 안정성은 python score.py가 저장한 data/sensitivity.csv를 표시 (2,000회 ~80초라 화면에서 재계산 안 함)
  - '내부 데이터 슬롯'은 가상 샘플로만 시연하며, 켰을 때만 반영
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import score
from score import AXES, DATA_DIR, FEAS, TOP_N

SAMPLE = DATA_DIR / "internal" / "sample_internal.csv"
REPORTS = Path(__file__).resolve().parent / "docs" / "reports"
# 검토 수정본이 있으면 그것을, 없으면 report.py 생성본을 보여 줌
REPORT = next((p for p in (REPORTS / "top3_report_final.md", REPORTS / "top3_report.md") if p.exists()),
              REPORTS / "top3_report.md")

st.set_page_config(page_title="K-제과 수출 유망국 발굴 에이전트", layout="wide")


@st.cache_data
def base_feats() -> pd.DataFrame:
    return score.load()


st.title("K-제과 수출 유망국 발굴 에이전트")
st.caption("공개 데이터 4개 API + 수동 자료 → 과제 3축 점수 → 롯데 실행가능성 검증 → 근거·출처 포함 TOP3 "
           "(신규 개척형 = 발굴 / 기존 주력 확대형 = 참고)")

with st.sidebar:
    track = st.radio("트랙", list(score.TRACKS), format_func=score.TRACKS.get,
                     help=f"한국 제과 수출 2025년 {score.MAJOR_MARKET_USD / 1e4:,.0f}만 달러 이상 = 기존 주력시장")
    st.header("가중치")
    w = [st.slider(a, 0.0, 1.0, 1 / 3, 0.05) for a in AXES]
    alpha = st.slider("α (실행가능성 비중)", 0.0, 0.6, score.ALPHA, 0.05)
    st.divider()
    use_sample = st.toggle("내부 데이터 슬롯: 가상 샘플로 시연", value=False,
                           help="data/internal/internal.csv(롯데 실데이터)를 넣으면 자동 반영")

if sum(w) == 0:
    st.error("가중치 합이 0입니다. 하나 이상 올려 주세요.")
    st.stop()

feats = base_feats()
if use_sample:
    feats = score.prepare(score.merge_csv(feats, SAMPLE))
    st.warning("⚠️ 가상 샘플 데이터(롯데 내부 영업이익률)가 반영된 결과입니다. 실제 분석 결과가 아닙니다.")

weights = tuple(x / sum(w) for x in w)
scored = score.score(feats, weights, alpha, track=track)
sfx = "" if track == "new" else f"_{track}"
top = scored[scored["final_rank"].notna()].sort_values("final_rank")
s1 = scored[scored["stage1_rank"].notna()].sort_values("stage1_rank")

cols = st.columns(3)
for i, (_, r) in enumerate(top.head(3).iterrows()):
    cols[i].metric(f"{i + 1}위", r["country"], f"{r['final']:.1f}점 · 1단계 {int(r['stage1_rank'])}위",
                   delta_color="off")

tab_rank, tab_why, tab_stable, tab_gate, tab_report = st.tabs(
    ["순위", "근거·출처", "안정성 검증", "탈락국", "리포트"])

score_col = st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")

with tab_rank:
    st.subheader(f"최종 TOP{TOP_N}")
    st.dataframe(top[["final_rank", "country", *AXES, "stage1", FEAS, "final", "flags"]],
                 hide_index=True, width="stretch",
                 column_config={"final_rank": "순위", "country": "국가", "stage1": score_col | {"label": "1단계"},
                                "final": score_col | {"label": "최종"}, FEAS: score_col,
                                **{a: score_col for a in AXES}, "flags": "데이터 한계"})
    st.subheader("1단계(과제 3축만) vs 최종(+ 롯데 실행가능성)")
    n = min(TOP_N, len(top))  # 확대 트랙은 후보가 TOP_N보다 적음
    st.dataframe(pd.DataFrame({"순위": range(1, n + 1),
                               "1단계": s1["country"].head(n).tolist(),
                               "최종": top["country"].head(n).tolist()}),
                 hide_index=True, width="stretch")

with tab_why:
    c = st.selectbox("국가", top["country"].tolist())
    d = score.drivers(scored)[c]
    st.markdown(f"**{c}** · 최종 {d['final']}점 · {d['final_rank']}위" + (f" · ⚠️ {d['flags']}" if d["flags"] else ""))
    for kind, title in (("up", "점수를 올린 지표"), ("down", "점수를 깎은 지표")):
        st.markdown(f"**{title}**")
        st.dataframe(pd.DataFrame(d[kind]), hide_index=True, width="stretch",
                     column_config={"percentile": score_col | {"label": "백분위"}})

with tab_stable:
    st.markdown("가중치(Dirichlet)·α(0.1~0.5)·지표 1개 제외를 무작위로 바꿔 2,000회 반복한 결과 "
                "(기본 설정 기준, `python score.py`로 갱신)")
    sens = pd.read_csv(DATA_DIR / f"sensitivity{sfx}.csv", encoding="utf-8-sig")
    rate = st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")
    st.dataframe(sens[sens["top10_rate"] > 0], hide_index=True, width="stretch",
                 column_config={"top3_rate": rate | {"label": "TOP3 진입률"}, "rank1_rate": rate | {"label": "1위 비율"},
                                "top10_rate": rate | {"label": "TOP10 진입률"}, "stable": f"안정(≥{score.STABLE_RATE:.0%})"})

with tab_gate:
    g = scored[scored["gate_reason"] != ""][["country", "gate_reason"]]
    st.dataframe(g, hide_index=True, width="stretch",
                 column_config={"country": "국가", "gate_reason": "탈락 사유"})

with tab_report:
    if REPORT.exists():
        st.markdown(REPORT.read_text(encoding="utf-8"), unsafe_allow_html=True)
    else:
        st.info("python report.py 를 실행하면 리포트가 여기에 표시됩니다.")
