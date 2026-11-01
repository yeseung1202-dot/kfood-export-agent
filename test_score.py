"""score.py / collect.py 자체 점검 (합성 데이터). 실행: python test_score.py"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import collect
import score
from score import FEAS, INDICATORS, TOP_N, axis_scores, drivers, gate, load, prepare, sensitivity


def fake_feats(n=12, seed=0):
    """모든 INDICATORS 열을 난수로 채운 가상 국가 n개 (테스트 전용)."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"country": [f"국가{i}" for i in range(n)], "imp_total_usd": 1e9,
                       "lotte_base": False, "ct_latest_year": 2024})
    for col, *_ in INDICATORS:
        df[col] = rng.normal(size=n)
    return prepare(df)


def test_direction_reversed():
    pct, *_ = axis_scores(pd.DataFrame({"x": [1.0, 2.0, 3.0]}), [("x", "A", -1, "s", "y")], {"A": 1})
    assert pct["x"].tolist() == [100.0, 50.0, 0.0]


def test_missing_is_skipped_not_zero():
    df = pd.DataFrame({"x": [1.0, np.nan, 3.0], "y": [3.0, 2.0, 1.0], "z": [1.0, np.nan, 2.0]})
    inds = [(c, "A", 1, "s", "y") for c in "xyz"]
    pct, axes, total, flags = axis_scores(df, inds, {"A": 1})
    assert axes["A"][1] == pct["y"][1] == 50.0  # 결측 x·z는 0점이 아니라 빠짐
    assert flags.tolist() == ["", "데이터 부족(A)", ""]  # 3개 중 1개만 유효 -> 절반 미만
    assert total[1] == 50.0


def test_thin_axis_shrinks_to_neutral():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [1.0, 2.0, np.nan], "z": np.nan})
    inds = [(c, "A", 1, "s", "y") for c in "xyz"]
    _, axes, _, _ = axis_scores(df, inds, {"A": 1})
    # z는 전원 결측 -> 제외. 2행은 x=100 하나뿐 -> 1/2×100 + 1/2×50 = 75
    assert axes["A"].tolist() == [0.0, 75.0, 75.0]


def test_score_end_to_end():
    f = fake_feats()
    s = score.score(f)
    top = s[s["final_rank"].notna()]
    assert len(top) == TOP_N
    r = top.iloc[0]
    assert np.isclose(r["final"], 0.7 * r["stage1"] + 0.3 * r[FEAS])

    no_feas = f.drop(columns=[i[0] for i in INDICATORS if i[1] == FEAS])
    s2 = score.score(no_feas)
    top2 = s2[s2["final_rank"].notna()]
    assert top2["flags"].str.contains("실행가능성 미입력").all()
    assert np.allclose(top2["final"], top2["stage1"])


def test_drivers():
    f = fake_feats()
    f["lead_time_days_src"] = "선사 스케줄 2026-10"
    d = drivers(score.score(f))
    assert len(d) == TOP_N
    first = next(iter(d.values()))
    assert first["final_rank"] == 1 and len(first["up"]) == 3 and len(first["down"]) == 3
    assert first["up"][0]["percentile"] >= first["down"][0]["percentile"]
    items = {i["indicator"]: i for i in first["up"] + first["down"]}
    if "imp_cagr" in items:
        assert items["imp_cagr"]["year"] == "2024"  # '@ct_latest_year' -> 국가별 값
    lt = [i for c in d.values() for i in c["up"] + c["down"] if i["indicator"] == "lead_time_days"]
    assert all(i["source"] == "선사 스케줄 2026-10" for i in lt)


def test_sensitivity_reproducible():
    f = fake_feats()
    a = sensitivity(f, runs=30, seed=1)
    b = sensitivity(f, runs=30, seed=1)
    pd.testing.assert_frame_equal(a, b)
    assert np.isclose(a["top3_rate"].sum(), 3.0)   # 매 회 정확히 3개국
    assert np.isclose(a["top10_rate"].sum(), TOP_N)


def test_load_merges_manual_cp949():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "manual").mkdir()
        pd.DataFrame({"country": ["베트남", "태국"], "imp_total_usd": [1e9, 1e9]}).to_csv(
            d / "country_features.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame({"country": ["베트남", "오타국"], "trend_pepero": ["1,200", "5"]}).to_csv(
            d / "manual" / "trend.csv", index=False, encoding="cp949")  # 엑셀 저장본 흉내
        f = load(d).set_index("country")
    assert f.loc["베트남", "trend_pepero"] == 1200.0
    assert np.isnan(f.loc["태국", "trend_pepero"])


def test_gate_reasons():
    df = pd.DataFrame({"country": ["가", "나", "다", "라", "일본"],
                       "imp_total_usd": [1e9, 1e6, np.nan, 1e9, 1e9],
                       "lotte_base": [False, False, False, True, False]})
    assert gate(df).tolist() == ["", "제과류 수입시장 규모 미달", "제과류 수입시장 규모 미확인",
                                 "롯데 현지 생산거점 운영 중", "원롯데 일본 롯데제과 홈마켓"]


def test_two_tracks_split_major_markets():
    df = pd.DataFrame({"country": ["가", "나"], "imp_total_usd": 1e9, "lotte_base": False,
                       "kr_exp_2025_usd": [50e6, 1e6]})
    assert gate(df, "new").tolist() == ["기존 주력시장(확대 대상)", ""]
    assert gate(df, "expand").tolist() == ["", "신규 개척 대상(확대 트랙 제외)"]


def test_small_base_and_gap():
    df = pd.DataFrame({"kr_exp_2021_usd": [50_000, 200_000], "kr_exp_cagr_21_25": [3.0, 0.2],
                       "kr_exp_2025ytd_usd": [200_000, 50_000], "kr_exp_ytd_growth_26": [0.1, 5.0],
                       "share_jp": [0.3, 0.1], "share_kr": [0.1, 0.05]})
    f = prepare(df)
    assert np.isnan(f["kr_exp_cagr_21_25"][0]) and f["kr_exp_cagr_21_25"][1] == 0.2
    assert f["kr_exp_ytd_growth_26"][0] == 0.1 and np.isnan(f["kr_exp_ytd_growth_26"][1])
    assert np.allclose(f["gap_jp_kr"], [0.2, 0.05])


def test_gap_needs_japan_presence():
    f = prepare(pd.DataFrame({"share_jp": [0.001, 0.02], "share_kr": [0.0, 0.05]}))
    assert np.isnan(f["gap_jp_kr"][0])            # 한·일 둘 다 없음 -> 여백이 아니라 미검증
    assert np.isclose(f["gap_jp_kr"][1], -0.03)


def test_report_verify_catches_unknown_numbers():
    import report
    assert report.fmt("imp_cagr", 0.1234) == "12.3%" and report.fmt("imf_gdp_pc_2026", 8570.1) == "8,570달러"
    facts = [{"label": "a", "text": "12.3% (백분위 88)", "source": "IMF, 2026~2030"},
             {"label": "b", "text": "8,570달러", "source": "IMF WEO, 2026"}]
    assert report.verify("성장률 12.3%[1], 1인당 GDP 8,570달러[2], TOP3", facts) == []
    assert report.verify("성장률 15.2%로 높다", facts) == ["15.2"]  # 근거에 없는 숫자 = 환각
    ranked = facts + [{"label": "최종 순위", "text": "1위", "source": "score.py"}]
    assert report.verify("최종 1위[3]", ranked) == [] and report.verify("관심도 2위[1]", ranked) == ["2위"]
    vf = [{"label": "0~14세 비중", "text": "31.6% (백분위 100)", "value": "31.6%", "source": "WB"},
          {"label": "리드타임", "text": "32.4일 (백분위 11)", "value": "32.4일", "source": "Fluent Cargo"}]
    assert report.fill("비중 {1}, 리드타임 {2}, 없는 번호 {9}", vf) == "비중 31.6%[1], 리드타임 32.4일[2], 없는 번호 {9}"
    assert report.fill("리드타임 {2}일{백분위}로 길다", vf) == "리드타임 32.4일[2]로 길다"  # 단위 중복·빈 중괄호 정리
    assert report.stray("0~14세 비중이 {1}이다", vf) == [] and report.stray("관심도 순위 2", vf) == ["2"]
    raw = "추천한다 12.3%[1].\n(약 390자)\n*(공백 제외 398자)*\n\n---\n\n※ 숫자 표기: 예시\n*총 글자 수: 398자*"
    assert report.clean_llm(raw) == "추천한다 12.3%[1]."  # 메타 줄만 지우고 본문은 그대로


def test_collect_new_columns():
    customs = pd.DataFrame({"country": "가", "iso2": "VN", "hs": "1905", "exp_kg": 0, "months": 12,
                            "period": ["2021", "2025", "2025YTD", "2026YTD"], "exp_usd": [1.0, 2.0, 3.0, 4.0]})
    wb = pd.DataFrame({"iso3": "VNM", "indicator": "SP.URB.TOTL.IN.ZS",
                       "year": [2023, 2024], "value": [39.0, 40.0]})
    f = collect.build_features(customs, None, wb, None).set_index("iso2")
    assert f.loc["VN", "kr_exp_2025ytd_usd"] == 3.0
    assert f.loc["VN", "wb_도시화율"] == 40.0
    assert f.loc["VN", "wb_도시화율_year"] == 2024


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("모두 통과")
