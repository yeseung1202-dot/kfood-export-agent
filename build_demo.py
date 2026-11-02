"""
정적 데모(demo/) 데이터 생성 + JS 계산 대조

실행: python build_demo.py
  - demo/data.js 생성: 국가별 지표 원값·출처·기준연도, 트랙별 탈락 사유, 몬테카를로 결과, 검토 수정본 리포트
  - node가 있으면 demo/engine.js(브라우저 계산)가 score.score()와 같은 값을 내는지 여러 설정으로 대조
  - 롯데 실데이터 자리(data/internal/internal.csv)는 넣지 않음. 내부 데이터 슬롯은 가상 샘플만
"""

import json
import math
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

import score
from score import AXES, DATA_DIR, FEAS, INDICATORS, INTERNAL_PREFIX, TRACKS

BASE = Path(__file__).resolve().parent
OUT = BASE / "demo" / "data.js"
SAMPLE = DATA_DIR / "internal" / "sample_internal.csv"
REPORT = BASE / "docs" / "reports" / "top3_report_final.md"
CASES = [((1 / 3, 1 / 3, 1 / 3), 0.3, "new", False), ((0.6, 0.2, 0.2), 0.1, "new", False),
         ((0.1, 0.1, 0.8), 0.5, "new", True), ((1 / 3, 1 / 3, 1 / 3), 0.3, "expand", False),
         ((0.2, 0.5, 0.3), 0.6, "expand", True), ((0, 1, 0), 0.0, "new", False)]


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v) else float(v)


def build() -> pd.DataFrame:
    feats = score.load()
    feats = feats.drop(columns=[c for c in feats if c.startswith(INTERNAL_PREFIX)])  # 실데이터는 공개 페이지에 넣지 않음
    cols = [i[0] for i in INDICATORS if not i[0].startswith(INTERNAL_PREFIX)]
    gates = {t: score.gate(feats, t).tolist() for t in TRACKS}
    rows = []
    for k, (_, r) in enumerate(feats.iterrows()):
        rows.append({"country": r["country"],
                     "gate": {t: gates[t][k] for t in TRACKS},
                     "v": {c: num(r.get(c)) for c in cols},
                     "src": {c: r[f"{c}_src"] for c in cols if isinstance(r.get(f"{c}_src"), str) and r[f"{c}_src"]},
                     "yr": {i[0]: score._year(r, i[4]) for i in INDICATORS if i[0] in cols}})
    sample = score.read_csv(SAMPLE)
    sens = {}
    for t in TRACKS:
        s = pd.read_csv(DATA_DIR / f"sensitivity{'' if t == 'new' else '_' + t}.csv", encoding="utf-8-sig")
        sens[t] = [{"country": r.country, "top3": r.top3_rate, "rank1": r.rank1_rate, "top10": r.top10_rate}
                   for r in s.itertuples() if r.top10_rate > 0]
    data = {
        "built": date.today().isoformat(),
        "indicators": [{"col": c, "axis": a, "sign": s, "source": src, "year": y} for c, a, s, src, y in INDICATORS],
        "axes": AXES, "feas": FEAS, "topN": score.TOP_N, "alpha": score.ALPHA, "tracks": TRACKS,
        "hub": sorted(score.REEXPORT_HUB), "stableRate": score.STABLE_RATE, "majorUsd": score.MAJOR_MARKET_USD,
        "rows": rows,
        "sample": {r.country: float(r.internal_margin_pct) for r in sample.itertuples()},
        "sampleSrc": str(sample["internal_margin_pct_src"].iloc[0]),
        "sens": sens,
        "report": REPORT.read_text(encoding="utf-8"),
    }
    OUT.write_text("globalThis.DEMO_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"저장: {OUT.relative_to(BASE)} ({OUT.stat().st_size / 1024:.0f}KB, {len(rows)}개국)")
    return feats


def check(feats: pd.DataFrame):
    """demo/engine.js 결과를 score.score()와 대조. 다르면 예외."""
    if not shutil.which("node"):
        print("[건너뜀] node 없음 -> JS 대조 생략")
        return
    js = ("require('./demo/data.js'); require('./demo/engine.js');"
          f"const cases={json.dumps(CASES)};"
          "console.log(JSON.stringify(cases.map(([w,a,t,s])=>KScore.score(DEMO_DATA,w,a,t,s)"
          ".map(r=>[r.country,r.stage1,r.stage1Rank,r.feas,r.final,r.finalRank,r.flags]))));")
    res = json.loads(subprocess.run(["node", "-e", js], cwd=BASE, capture_output=True, text=True,
                                    encoding="utf-8", check=True).stdout)
    with_sample = score.prepare(score.merge_csv(feats, SAMPLE))
    for (w, a, t, s), got in zip(CASES, res):
        exp = score.score(with_sample if s else feats, w, a, track=t)
        for (c, s1, s1r, fe, fi, fir, fl), (_, r) in zip(got, exp.iterrows()):
            assert c == r["country"]
            for name, x, y in (("stage1", s1, r["stage1"]), ("stage1_rank", s1r, r["stage1_rank"]),
                               (FEAS, fe, r[FEAS]), ("final", fi, r["final"]), ("final_rank", fir, r["final_rank"])):
                same = (x is None and pd.isna(y)) or (x is not None and not pd.isna(y) and abs(x - y) < 1e-9)
                assert same, f"{c} {name}: JS {x} != Python {y} (가중치 {w}, α {a}, {t}, 샘플 {s})"
            assert fl == r["flags"], f"{c} flags: JS '{fl}' != Python '{r['flags']}'"
    print(f"JS 계산 대조 통과: {len(CASES)}개 설정 × {len(feats)}개국 (stage1·순위·실행가능성·최종·플래그)")


if __name__ == "__main__":
    check(build())
