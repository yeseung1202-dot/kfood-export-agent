"""
K-푸드 수출 유망국 스코어링 (LOTTE AX Challenge 2026 / 롯데웰푸드 과제)

설계: docs/superpowers/specs/2026-10-29-score-design.md
  1단계  과제 3축(성장성·트렌드·수출입) 지표 백분위의 가중평균 -> TOP10
  2단계  롯데 실행가능성 점수, 최종 = (1-α)×1단계 + α×실행가능성
  검증   몬테카를로(가중치·α·지표 1개 제외)로 국가별 TOP3 진입률 집계

사용법
  python score.py
  python score.py --weights 0.5 0.25 0.25 --alpha 0.3 --runs 2000 --seed 42

입력
  data/country_features.csv     collect.py 출력
  data/manual/trend.csv         Google Trends·KOFICE (수동)
  data/manual/feasibility.csv   2단계 실행가능성 (수동, 항목별 출처는 <열>_src)

출력
  data/scores.csv        국가별 축 점수·단계별 점수·순위·탈락 사유·플래그
  data/drivers.json      TOP10 국가별 점수를 올린/깎은 지표 상위 3개 (원값·백분위·출처·기준연도)
  data/sensitivity.csv   국가별 TOP3 진입률·1위 비율·TOP10 진입률·안정 여부
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. 설정
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

MIN_MARKET_USD = 50_000_000  # 제과류 수입시장 최소 규모. 첫 수집 후 분포 보고 확정
MIN_BASE_USD = 100_000       # 증가율의 기저 수출액이 이보다 작으면 증가율을 결측 처리
MIN_JP_SHARE = 0.005         # 일본 점유율이 이보다 낮으면 아시아 과자 수요 미검증 -> 여백(gap_jp_kr) 결측
NEED_RATE = 0.2              # 몬테카를로 TOP10 진입률이 이 이상인 나라만 feasibility 입력 대상으로 안내
TOP_N = 10
ALPHA = 0.3
DIRICHLET_CONC = 5           # 몬테카를로 가중치 집중도 (클수록 균등 가중치 근처에서 흔들림)
STABLE_RATE = 0.7            # TOP3 진입률이 이 이상이면 '안정'
EXCLUDE = {"일본": "원롯데 일본 롯데제과 홈마켓", "미얀마": "내전"}
MAJOR_MARKET_USD = 20_000_000  # 한국 제과 수출 2025년 이 이상 = 기존 주력시장 -> '확대' 트랙 (2026-10-30 두 트랙 도입)
TRACKS = {"new": "신규 개척형", "expand": "기존 주력 확대형"}
REEXPORT_HUB = {"싱가포르", "네덜란드", "홍콩", "아랍에미리트"}  # 수입액에 재수출 물량 포함 -> 시장성 과대 가능 (표시만)

AXES = ["성장성", "트렌드", "수출입"]
FEAS = "실행가능성"

# (열, 축, 방향: 1 높을수록 좋음 / -1 낮을수록 좋음, 출처, 기준연도: '@열'이면 국가별 값)
INDICATORS = [
    ("imp_cagr", "성장성", 1, "UN Comtrade 제과류(HS 1905·1806·2105·1704) 수입액 CAGR", "@ct_latest_year"),
    ("imf_gdp_growth_fcst_26_30", "성장성", 1, "IMF WEO 실질GDP성장률 전망 평균", "2026~2030"),
    ("imf_gdp_pc_2026", "성장성", 1, "IMF WEO 1인당 GDP(USD)", "2026"),
    ("wb_0_14세비중", "성장성", 1, "World Bank 0~14세 인구 비중", "@wb_0_14세비중_year"),
    ("wb_도시화율", "성장성", 1, "World Bank 도시화율", "@wb_도시화율_year"),
    ("trend_korean_food", "트렌드", 1, "Google Trends '한국 요리'(주제, 언어 무관) 국가별 관심도", "2025-10-29~2026-10-29"),
    ("trend_korean_snacks", "트렌드", 1, "Google Trends 'korean snack'(검색어) 국가별 관심도", "2025-10-29~2026-10-29"),
    ("trend_pepero", "트렌드", 1, "Google Trends 'Pepero'(검색어) 국가별 관심도", "2025-10-29~2026-10-29"),
    ("kofice_hansik_favor", "트렌드", 1, "KOFICE 해외한류실태조사 한식 호감도", "최신 조사"),
    ("kr_exp_cagr_21_25", "수출입", 1, "관세청 한국 제과류 수출액 CAGR", "2021→2025"),
    ("kr_exp_ytd_growth_26", "수출입", 1, "관세청 한국 제과류 수출액 전년동기비", "2026년 1~9월"),
    ("gap_jp_kr", "수출입", 1, "UN Comtrade 수입시장 점유율 일본−한국", "@ct_latest_year"),
    ("lead_time_days", FEAS, -1, "롯데 공급거점 기준 리드타임(일)", "2026"),
    ("lpi_2023", FEAS, 1, "World Bank 물류성과지수(LPI) 종합점수(1~5)", "2023"),
    ("tariff_hs1905_pct", FEAS, -1, "WITS/MacMap HS1905 적용관세율(%)", "2026"),
    ("halal_required", FEAS, -1, "할랄 인증 필요 여부(0/1)", "2026"),
    ("sugar_tax", FEAS, -1, "당류세 시행 여부(0/1)", "2026"),
    ("oecd_risk", FEAS, -1, "OECD 국가위험등급(0~7)", "2026"),
    # 롯데 내부 데이터 슬롯: data/internal/internal.csv가 있을 때만 반영 (본선 현장 데이터)
    ("internal_margin_pct", FEAS, 1, "롯데웰푸드 내부 국가별 영업이익률(%)", "내부 데이터"),
]
INTERNAL_PREFIX = "internal_"

# (증가율 열, 기저 수출액 열)
SMALL_BASE = [("kr_exp_cagr_21_25", "kr_exp_2021_usd"), ("kr_exp_ytd_growth_26", "kr_exp_2025ytd_usd")]


# ---------------------------------------------------------------------------
# 2. 전처리·탈락조건
# ---------------------------------------------------------------------------

def prepare(feats: pd.DataFrame) -> pd.DataFrame:
    """숫자 변환(수동 입력의 '1,234' 포함), 파생 지표 gap_jp_kr, 작은 기저 증가율 결측 처리."""
    f = feats.copy()
    for col in [i[0] for i in INDICATORS] + [base for _, base in SMALL_BASE]:
        if col in f:
            f[col] = pd.to_numeric(f[col].astype(str).str.replace(",", ""), errors="coerce")
    if {"share_jp", "share_kr"} <= set(f.columns):
        f["gap_jp_kr"] = (f["share_jp"] - f["share_kr"]).where(f["share_jp"] >= MIN_JP_SHARE)
    for col, base in SMALL_BASE:
        if col in f and base in f:
            f.loc[f[base] < MIN_BASE_USD, col] = np.nan
    return f


def gate(feats: pd.DataFrame, track: str = "new") -> pd.Series:
    """국가별 탈락 사유. 통과국은 빈 문자열. track: new=기존 주력시장 제외, expand=기존 주력시장만."""
    out = []
    for _, r in feats.iterrows():
        why = []
        major = r.get("kr_exp_2025_usd", np.nan) >= MAJOR_MARKET_USD  # NaN이면 False -> 신규
        if track == "new" and major:
            why.append("기존 주력시장(확대 대상)")
        if track == "expand" and not major:
            why.append("신규 개척 대상(확대 트랙 제외)")
        m = r.get("imp_total_usd", np.nan)
        if pd.isna(m):
            why.append("제과류 수입시장 규모 미확인")
        elif m < MIN_MARKET_USD:
            why.append("제과류 수입시장 규모 미달")
        if r.get("lotte_base", False) == True:  # noqa: E712  numpy.bool_이라 'is True'로는 안 잡힘
            why.append("롯데 현지 생산거점 운영 중")
        if r.get("country") in EXCLUDE:
            why.append(EXCLUDE[r["country"]])
        out.append("; ".join(why))
    return pd.Series(out, index=feats.index)


# ---------------------------------------------------------------------------
# 3. 점수
# ---------------------------------------------------------------------------

def pct_rank(s: pd.Series) -> pd.Series:
    """0~100 백분위 (최솟값 0, 최댓값 100, 동점은 평균 순위). 결측은 결측 그대로."""
    n = s.notna().sum()
    if n <= 1:
        return s.where(s.isna(), 50.0)
    return (s.rank() - 1) / (n - 1) * 100


def axis_scores(df: pd.DataFrame, inds: list, weights: dict):
    """지표 백분위 -> 축 점수 -> 유효 축 가중평균.

    축 점수 = 유효 지표 비율 × 유효 지표 평균 + (1 − 비율) × 50 (중립 수축).
    결측 지표는 0점이 아니라 중립으로 메워져 지표 1개가 축을 좌우하지 못하고,
    전원 결측인 지표(예: 미입력 KOFICE)는 정보가 없으므로 제외. 유효 지표가 절반 미만이면 플래그.
    """
    pct = pd.DataFrame({col: pct_rank(df[col]) if sign > 0 else 100 - pct_rank(df[col])
                        for col, _, sign, *_ in inds}, index=df.index)
    axes = pd.DataFrame(index=df.index)
    flags = pd.Series("", index=df.index)
    for axis in weights:
        cols = [i[0] for i in inds if i[1] == axis and pct[i[0]].notna().any()]
        if not cols:
            axes[axis] = np.nan
            continue
        valid = pct[cols].notna().sum(axis=1)
        share = valid / len(cols)
        axes[axis] = share * pct[cols].mean(axis=1) + (1 - share) * 50  # 유효 지표 0개면 NaN 유지
        thin = valid < len(cols) / 2
        flags[thin] = (flags[thin] + f" 데이터 부족({axis})").str.strip()
    w = pd.Series(weights)
    total = (axes.fillna(0) * w).sum(axis=1) / (axes.notna() * w).sum(axis=1)
    return pct, axes, total, flags


def score(feats: pd.DataFrame, weights=(1 / 3, 1 / 3, 1 / 3), alpha: float = ALPHA,
          drop: str | None = None, track: str = "new") -> pd.DataFrame:
    """1단계(3축) -> TOP_N -> 2단계(실행가능성) 결합. drop은 제외할 지표 열(몬테카를로용), track은 TRACKS 키."""
    df = feats.copy()
    df["gate_reason"] = gate(df, track)
    df["flags"] = ""
    df[FEAS] = np.nan
    inds = [i for i in INDICATORS if i[0] in df.columns and i[0] != drop]
    ok = df["gate_reason"] == ""

    pct, axes, total, flags = axis_scores(df[ok], [i for i in inds if i[1] in AXES], dict(zip(AXES, weights)))
    for c in pct:
        df.loc[ok, f"pct_{c}"] = pct[c]
    for a in AXES:
        df.loc[ok, a] = axes[a]
    df.loc[ok, "stage1"] = total
    df.loc[ok, "flags"] = flags
    df["stage1_rank"] = df["stage1"].rank(ascending=False, method="first")

    top = df["stage1_rank"] <= TOP_N
    pct2, _, feas, _ = axis_scores(df[top], [i for i in inds if i[1] == FEAS], {FEAS: 1})
    for c in pct2:
        df.loc[top, f"pct_{c}"] = pct2[c]
    df.loc[top, FEAS] = feas
    has = top & df[FEAS].notna()
    df["final"] = np.where(has, (1 - alpha) * df["stage1"] + alpha * df[FEAS], df["stage1"].where(top))
    miss = top & ~has
    df.loc[miss, "flags"] = (df.loc[miss, "flags"] + " 실행가능성 미입력").str.strip()
    hub = top & df["country"].isin(REEXPORT_HUB)
    df.loc[hub, "flags"] = (df.loc[hub, "flags"] + " 재수출 허브(수입액 과대 가능)").str.strip()
    df["final_rank"] = df["final"].rank(ascending=False, method="first")
    return df


# ---------------------------------------------------------------------------
# 4. 근거 지표·안정성 검증
# ---------------------------------------------------------------------------

def _year(r: pd.Series, year: str):
    """기준연도 표기. '@열'이면 국가별 값(예: Comtrade 최신 보고연도), 아니면 그대로."""
    if not year.startswith("@"):
        return year
    v = r.get(year[1:])
    return None if v is None or pd.isna(v) else str(int(v))


def drivers(scored: pd.DataFrame, k: int = 3) -> dict:
    """TOP_N 국가별 점수를 올린/깎은 지표 상위 k개. LLM 리포트의 유일한 수치 입력."""
    out = {}
    for _, r in scored[scored["final_rank"].notna()].sort_values("final_rank").iterrows():
        items = []
        for col, axis, _, src, year in INDICATORS:
            p = r.get(f"pct_{col}")
            if p is None or pd.isna(p):
                continue
            own = r.get(f"{col}_src")
            items.append({"indicator": col, "axis": axis, "value": float(r[col]),
                          "percentile": round(float(p), 1),
                          "source": own if isinstance(own, str) and own else src,
                          "year": _year(r, year)})
        items.sort(key=lambda d: d["percentile"], reverse=True)
        out[r["country"]] = {"final_rank": int(r["final_rank"]), "final": round(float(r["final"]), 1),
                             "flags": r["flags"], "up": items[:k], "down": items[::-1][:k]}
    return out


def sensitivity(feats: pd.DataFrame, runs: int = 2000, seed: int = 42, track: str = "new") -> pd.DataFrame:
    """가중치~Dirichlet, α~U(0.1,0.5), 지표 1개 무작위 제외로 runs회 반복 -> 국가별 진입률."""
    # ponytail: score() 1회 ~60ms -> 2000회 ~80초. 데모는 sensitivity.csv를 읽어 쓰고,
    # 실시간 재계산이 필요해지면 gate·pct_ 대입을 루프 밖으로 빼서 줄일 것
    rng = np.random.default_rng(seed)
    cols = [i[0] for i in INDICATORS if i[0] in feats.columns]
    cnt = pd.DataFrame(0, index=pd.Index(feats["country"], name="country"), columns=["top3", "rank1", "top10"])
    for _ in range(runs):
        w = tuple(rng.dirichlet([DIRICHLET_CONC] * 3))
        a = rng.uniform(0.1, 0.5)
        s = score(feats, w, a, drop=rng.choice(cols), track=track).set_index("country")
        cnt.loc[s.index[s["final_rank"] <= 3], "top3"] += 1
        cnt.loc[s.index[s["final_rank"] == 1], "rank1"] += 1
        cnt.loc[s.index[s["stage1_rank"] <= TOP_N], "top10"] += 1
    res = (cnt / runs).add_suffix("_rate")
    res["stable"] = res["top3_rate"] >= STABLE_RATE
    return res.sort_values("top3_rate", ascending=False)


# ---------------------------------------------------------------------------
# 5. 실행
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> pd.DataFrame:
    """utf-8(BOM) 우선, 엑셀에서 저장한 cp949 CSV도 읽음."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp949")


def merge_csv(feats: pd.DataFrame, p: Path) -> pd.DataFrame:
    """국가별 수동/내부 CSV를 한글 국가명으로 병합. 후보 목록에 없는 국가명은 경고."""
    m = read_csv(p).dropna(subset=["country"]).drop_duplicates("country")
    unknown = sorted(set(m["country"]) - set(feats["country"]))
    if unknown:
        print(f"[경고] {p.name}의 국가명이 후보 목록에 없음(오타?): {', '.join(unknown)}")
    return feats.merge(m, on="country", how="left")


def load(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    path = data_dir / "country_features.csv"
    if not path.exists():
        sys.exit("data/country_features.csv가 없습니다. collect.py를 먼저 실행하세요.")
    feats = read_csv(path)
    for name in ("trend.csv", "feasibility.csv"):
        p = data_dir / "manual" / name
        if not p.exists():
            print(f"[경고] data/manual/{name} 없음 -> 해당 지표를 결측으로 두고 계속")
            continue
        feats = merge_csv(feats, p)
    internal = data_dir / "internal" / "internal.csv"  # 롯데 내부 실데이터 (예선에는 없음)
    if internal.exists():
        feats = merge_csv(feats, internal)
    return prepare(feats)


def main():
    ap = argparse.ArgumentParser(description="K-푸드 수출 유망국 스코어링")
    ap.add_argument("--weights", nargs=3, type=float, default=[1, 1, 1], metavar=("성장성", "트렌드", "수출입"))
    ap.add_argument("--alpha", type=float, default=ALPHA, help="2단계 실행가능성 비중")
    ap.add_argument("--runs", type=int, default=2000, help="몬테카를로 반복 횟수")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    feats = load()
    for col, *_ in INDICATORS:
        if col not in feats.columns and not col.startswith(INTERNAL_PREFIX):
            print(f"[경고] 지표 열 없음 -> 건너뜀: {col}")

    w = np.array(args.weights) / sum(args.weights)
    fcols = [i[0] for i in INDICATORS if i[1] == FEAS and i[0] in feats.columns]
    blank = (feats.set_index("country")[fcols].isna().all(axis=1) if fcols
             else pd.Series(True, index=feats["country"]))
    key = ["country", "gate_reason", *AXES, "stage1", "stage1_rank", FEAS, "final", "final_rank", "flags"]
    for track, name in TRACKS.items():
        sfx = "" if track == "new" else f"_{track}"  # 메인 트랙(신규 개척형)은 접미사 없음
        scored = score(feats, tuple(w), args.alpha, track=track)
        scored[key + [c for c in scored.columns if c not in key]].to_csv(
            DATA_DIR / f"scores{sfx}.csv", index=False, encoding="utf-8-sig")
        (DATA_DIR / f"drivers{sfx}.json").write_text(json.dumps(drivers(scored), ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        print(f"\n===== {name} (몬테카를로 {args.runs}회, seed={args.seed}) =====")
        sens = sensitivity(feats, args.runs, args.seed, track)
        sens.to_csv(DATA_DIR / f"sensitivity{sfx}.csv", encoding="utf-8-sig")

        top = scored.dropna(subset=["final_rank"]).sort_values("final_rank")
        print(f"TOP{TOP_N} (가중치 {np.round(w, 2).tolist()}, α={args.alpha})")
        print(top[["country", *AXES, "stage1", FEAS, "final", "flags"]].round(1).to_string(index=False))
        print("TOP3 안정성")
        for c in top["country"].head(3):
            r = sens.loc[c]
            print(f"  {c}: TOP3 진입률 {r.top3_rate:.0%}, 1위 {r.rank1_rate:.0%} -> {'안정' if r.stable else '불안정'}")
        need = [c for c in sens.index[sens["top10_rate"] >= NEED_RATE] if blank[c]]
        if need:
            print(f"feasibility.csv 입력 필요 ({len(need)}개국, 몬테카를로 TOP{TOP_N} 진입률 {NEED_RATE:.0%} 이상): "
                  f"{', '.join(need)}")
    print("\n저장: data/scores*.csv, data/drivers*.json, data/sensitivity*.csv (접미사 없음 = 신규 개척형)")


if __name__ == "__main__":
    sys.exit(main())
