"""
K-푸드 수출 유망국 발굴 - 공개데이터 수집기 (LOTTE AX Challenge 2026 / 롯데웰푸드 과제)

수집 소스 4개
  1) 관세청 품목별 국가별 수출입실적 (공공데이터포털)  -> 한국 -> 각국 제과류 수출액
  2) UN Comtrade                                       -> 각국의 제과류 전체 수입 규모, 한/일/중 점유율
  3) World Bank Indicators                             -> 인구, 1인당 GDP, 도시화율, 연령구조 등
  4) IMF DataMapper (WEO)                              -> 실질 GDP 성장률 전망 등

사용법
  pip install requests pandas
  keys.txt 파일에 키 입력 (.env 도 지원)
  python collect.py                 # 전체 수집 + country_features.csv 생성
  python collect.py --only wb imf   # 일부 소스만
  python collect.py --refresh       # 캐시 무시하고 다시 받기

결과
  data/raw/customs_kr_exports.csv   관세청 원자료(국가 x HS x 기간)
  data/raw/comtrade_imports.csv     Comtrade 원자료(수입국 x 상대국 x HS x 연도)
  data/raw/worldbank.csv            World Bank 원자료
  data/raw/imf.csv                  IMF 원자료
  data/country_features.csv         국가별 분석용 지표 1행 요약 (스코어링 입력)
  data/collect_log.json             수집 시각, 소스 URL, 오류 목록 (재현성/출처 기록용)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 1. 설정
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

# 후보 국가: (한글명, 관세청 ISO2, ISO3(WB/IMF), Comtrade M49 코드, 권역, 롯데 생산거점 여부)
# Comtrade 코드는 표준 M49와 다른 국가가 있음: 미국 842, 프랑스 251, 인도 699, 대만 490
COUNTRIES = [
    ("베트남", "VN", "VNM", 704, "동남아", False),
    ("필리핀", "PH", "PHL", 608, "동남아", False),
    ("인도네시아", "ID", "IDN", 360, "동남아", False),
    ("말레이시아", "MY", "MYS", 458, "동남아", False),
    ("태국", "TH", "THA", 764, "동남아", False),
    ("싱가포르", "SG", "SGP", 702, "동남아", False),
    ("캄보디아", "KH", "KHM", 116, "동남아", False),
    ("미얀마", "MM", "MMR", 104, "동남아", True),
    ("중국", "CN", "CHN", 156, "동아시아", False),
    ("일본", "JP", "JPN", 392, "동아시아", False),
    ("대만", "TW", "TWN", 490, "동아시아", False),
    ("홍콩", "HK", "HKG", 344, "동아시아", False),
    ("몽골", "MN", "MNG", 496, "동아시아", False),
    ("미국", "US", "USA", 842, "북미", False),
    ("캐나다", "CA", "CAN", 124, "북미", False),
    ("멕시코", "MX", "MEX", 484, "중남미", False),
    ("브라질", "BR", "BRA", 76, "중남미", False),
    ("칠레", "CL", "CHL", 152, "중남미", False),
    ("온두라스", "HN", "HND", 340, "중남미", False),
    ("아랍에미리트", "AE", "ARE", 784, "중동", False),
    ("사우디아라비아", "SA", "SAU", 682, "중동", False),
    ("카타르", "QA", "QAT", 634, "중동", False),
    ("쿠웨이트", "KW", "KWT", 414, "중동", False),
    ("영국", "GB", "GBR", 826, "유럽", False),
    ("독일", "DE", "DEU", 276, "유럽", False),
    ("프랑스", "FR", "FRA", 251, "유럽", False),
    ("네덜란드", "NL", "NLD", 528, "유럽", False),
    ("폴란드", "PL", "POL", 616, "유럽", False),
    ("라트비아", "LV", "LVA", 428, "유럽", False),
    ("호주", "AU", "AUS", 36, "오세아니아", False),
    ("뉴질랜드", "NZ", "NZL", 554, "오세아니아", False),
    ("러시아", "RU", "RUS", 643, "CIS", True),
    ("카자흐스탄", "KZ", "KAZ", 398, "CIS", True),
    ("우즈베키스탄", "UZ", "UZB", 860, "CIS", False),
    ("인도", "IN", "IND", 699, "서남아", True),
    ("파키스탄", "PK", "PAK", 586, "서남아", True),
    ("케냐", "KE", "KEN", 404, "아프리카", False),
]

# 롯데웰푸드 제품군에 맞춘 HS 4단위
HS_CODES = {
    "1905": "빵·비스킷류(빼빼로 포함)",
    "1806": "초콜릿·코코아 조제품",
    "2105": "아이스크림",
    "1704": "설탕과자(캔디·껌 등)",
}

# 관세청 조회기간(한 번에 1년 이내). 연간 5개 + 올해 누적(YTD) 비교용 2개
# 관세청은 매월 15일경 전월 자료까지 갱신 -> 10월 말 기준 9월까지 확보 가능
CUSTOMS_PERIODS = [
    ("2021", "202101", "202112"),
    ("2022", "202201", "202212"),
    ("2023", "202301", "202312"),
    ("2024", "202401", "202412"),
    ("2025", "202501", "202512"),
    ("2025YTD", "202501", "202509"),
    ("2026YTD", "202601", "202609"),
]

COMTRADE_YEARS = [2021, 2022, 2023, 2024, 2025]  # 2025는 국가별 보고 지연으로 일부 결측 정상
COMTRADE_PARTNERS = {0: "World", 410: "Korea", 392: "Japan", 156: "China"}

WB_INDICATORS = {
    "SP.POP.TOTL": "인구",
    "NY.GDP.PCAP.CD": "1인당GDP_USD",
    "NY.GDP.MKTP.KD.ZG": "실질GDP성장률",
    "SP.URB.TOTL.IN.ZS": "도시화율",
    "SP.POP.0014.TO.ZS": "0_14세비중",
    "SP.POP.1564.TO.ZS": "15_64세비중",
    "IT.NET.USER.ZS": "인터넷이용률",
}
WB_DATE_RANGE = "2015:2025"

IMF_INDICATORS = {
    "NGDP_RPCH": "실질GDP성장률",
    "NGDPDPC": "1인당GDP_USD",
    "PCPIPCH": "물가상승률",
}
IMF_PERIODS = list(range(2021, 2031))
IMF_FORECAST_YEARS = [2026, 2027, 2028, 2029, 2030]

CUSTOMS_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
COMTRADE_URL_KEY = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
COMTRADE_URL_PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
WB_URL = "https://api.worldbank.org/v2/country/{codes}/indicator/{ind}"
IMF_URL = "https://www.imf.org/external/datamapper/api/v1/{ind}"

TIMEOUT = 30
LOG: dict = {"started_at": None, "sources": {}, "errors": []}


# ---------------------------------------------------------------------------
# 2. 공통 유틸
# ---------------------------------------------------------------------------

def load_env() -> None:
    """python-dotenv 없이 키 파일 읽기 (KEY=VALUE 형식). keys.txt 또는 .env 둘 다 지원."""
    for path in (BASE_DIR / "keys.txt", BASE_DIR / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():  # 메모장 BOM 대응
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:  # 빈 값은 무시
                os.environ.setdefault(k.strip(), v)


def get_with_retry(url, params=None, headers=None, tries=3, wait=2.0):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if r.status_code == 429:  # rate limit
                time.sleep(wait * (i + 2))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"요청 실패: {url} ({last})")


def log_error(source: str, msg: str) -> None:
    LOG["errors"].append({"source": source, "msg": msg})
    print(f"  [경고] {source}: {msg}")


def to_num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 3. 관세청 (한국 -> 각국 수출)
# ---------------------------------------------------------------------------

def parse_customs_xml(text: str) -> list[dict]:
    """관세청 XML 응답 -> 월별 행 리스트. 인증 오류면 RuntimeError."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise RuntimeError(f"XML 파싱 실패: {text[:200]}")

    # 게이트웨이 인증 오류 형식(OpenAPI_ServiceResponse) 처리
    auth_msg = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
    if auth_msg and "NORMAL" not in auth_msg.upper():
        raise RuntimeError(f"인증/요청 오류: {auth_msg}")

    code = root.findtext(".//resultCode")
    if code not in (None, "00", "0"):
        raise RuntimeError(f"resultCode={code} {root.findtext('.//resultMsg')}")

    rows = []
    for item in root.iter("item"):
        rec = {child.tag: (child.text or "").strip() for child in item}
        rows.append(rec)
    return rows


def fetch_customs(key: str) -> pd.DataFrame:
    total = len(COUNTRIES) * len(CUSTOMS_PERIODS) * len(HS_CODES)
    print(f"[관세청] {total}회 호출 예정 (개발계정 일일 한도 10,000)")
    out, n = [], 0
    for name, iso2, *_ in COUNTRIES:
        for label, start, end in CUSTOMS_PERIODS:
            for hs in HS_CODES:
                n += 1
                params = {"serviceKey": key, "strtYymm": start, "endYymm": end,
                          "cntyCd": iso2, "hsSgn": hs}
                try:
                    r = get_with_retry(CUSTOMS_URL, params=params)
                    rows = parse_customs_xml(r.text)
                except RuntimeError as e:
                    if "SERVICE_KEY" in str(e) or "인증" in str(e):
                        raise  # 키 문제는 전체 중단
                    log_error("customs", f"{name} {label} {hs}: {e}")
                    continue
                # 월별 행만 합산 (year 예: '2025.01'), '총계' 등은 제외
                monthly = [x for x in rows if re.fullmatch(r"\d{4}\.\d{2}", x.get("year", ""))]
                exp = sum(to_num(x.get("expDlr")) or 0 for x in monthly)
                wgt = sum(to_num(x.get("expWgt")) or 0 for x in monthly)
                out.append({"country": name, "iso2": iso2, "period": label, "hs": hs,
                            "exp_usd": exp, "exp_kg": wgt, "months": len(monthly)})
                if n % 50 == 0:
                    print(f"  {n}/{total}")
                time.sleep(0.12)
    df = pd.DataFrame(out)
    LOG["sources"]["customs"] = {"url": CUSTOMS_URL, "calls": n, "rows": len(df)}
    return df


# ---------------------------------------------------------------------------
# 4. UN Comtrade (각국의 수입 규모와 상대국 점유율)
# ---------------------------------------------------------------------------

def parse_comtrade_json(js: dict) -> list[dict]:
    if js.get("error"):
        raise RuntimeError(js["error"])
    rows = []
    for d in js.get("data") or []:
        rows.append({
            "year": d.get("refYear") or d.get("period"),
            "reporter_m49": d.get("reporterCode"),
            "reporter": d.get("reporterDesc"),
            "partner_m49": d.get("partnerCode"),
            "partner": d.get("partnerDesc"),
            "hs": str(d.get("cmdCode")),
            "imp_usd": d.get("primaryValue"),
            "net_kg": d.get("netWgt"),
        })
    return rows


def fetch_comtrade(key: str | None) -> pd.DataFrame:
    url = COMTRADE_URL_KEY if key else COMTRADE_URL_PREVIEW
    headers = {"Ocp-Apim-Subscription-Key": key} if key else None
    reporters = ",".join(str(c[3]) for c in COUNTRIES)
    partners = ",".join(str(p) for p in COMTRADE_PARTNERS)
    print(f"[Comtrade] {'키 사용' if key else '키 없음(미리보기, 500건 제한)'} - "
          f"{len(COMTRADE_YEARS) * len(HS_CODES)}회 호출 예정")
    out = []
    for year in COMTRADE_YEARS:
        for hs in HS_CODES:  # 미리보기 500건 제한을 피하려고 연도 x HS 단위로 분할
            params = {"reporterCode": reporters, "period": str(year), "partnerCode": partners,
                      "partner2Code": "0", "cmdCode": hs, "flowCode": "M",
                      "customsCode": "C00", "motCode": "0", "includeDesc": "true"}
            try:
                r = get_with_retry(url, params=params, headers=headers)
                out.extend(parse_comtrade_json(r.json()))
            except (RuntimeError, ValueError) as e:
                log_error("comtrade", f"{year} {hs}: {e}")
            time.sleep(1.2)  # 무료 계정 속도 제한 대응
    df = pd.DataFrame(out)
    LOG["sources"]["comtrade"] = {"url": url, "rows": len(df), "keyed": bool(key)}
    return df


# ---------------------------------------------------------------------------
# 5. World Bank
# ---------------------------------------------------------------------------

def parse_wb_json(js) -> list[dict]:
    if not isinstance(js, list) or len(js) < 2:
        msg = js[0].get("message") if isinstance(js, list) and js else js
        raise RuntimeError(f"WB 응답 오류: {msg}")
    rows = []
    for d in js[1] or []:
        rows.append({"iso3": d.get("countryiso3code"), "indicator": d["indicator"]["id"],
                     "year": int(d["date"]), "value": d.get("value")})
    return rows


def fetch_worldbank() -> pd.DataFrame:
    codes = ";".join(c[2] for c in COUNTRIES if c[2] != "TWN")  # WB에는 대만 없음
    out = []
    print(f"[World Bank] 지표 {len(WB_INDICATORS)}개")
    for ind in WB_INDICATORS:
        url = WB_URL.format(codes=codes, ind=ind)
        try:
            r = get_with_retry(url, params={"format": "json", "date": WB_DATE_RANGE, "per_page": 20000})
            out.extend(parse_wb_json(r.json()))
        except (RuntimeError, ValueError) as e:
            log_error("worldbank", f"{ind}: {e}")
        time.sleep(0.3)
    df = pd.DataFrame(out)
    LOG["sources"]["worldbank"] = {"url": WB_URL, "rows": len(df)}
    return df


# ---------------------------------------------------------------------------
# 6. IMF DataMapper
# ---------------------------------------------------------------------------

def parse_imf_json(js: dict, ind: str) -> list[dict]:
    rows = []
    for iso3, series in (js.get("values", {}).get(ind) or {}).items():
        for year, val in (series or {}).items():
            rows.append({"iso3": iso3, "indicator": ind, "year": int(year), "value": val})
    return rows


def fetch_imf() -> pd.DataFrame:
    # 2026-10 확인: 국가·기간을 붙인 긴 URL은 404, 국가 필터는 무시됨 -> 지표 전체를 받아 후보국만 남김
    want = {c[2] for c in COUNTRIES}
    out = []
    print(f"[IMF] 지표 {len(IMF_INDICATORS)}개")
    for ind in IMF_INDICATORS:
        url = IMF_URL.format(ind=ind)
        try:
            r = get_with_retry(url)
            out.extend(x for x in parse_imf_json(r.json(), ind)
                       if x["iso3"] in want and x["year"] in IMF_PERIODS)
        except (RuntimeError, ValueError) as e:
            log_error("imf", f"{ind}: {e}")
        time.sleep(0.3)
    df = pd.DataFrame(out)
    LOG["sources"]["imf"] = {"url": IMF_URL, "rows": len(df)}
    return df


# ---------------------------------------------------------------------------
# 7. 국가별 지표 요약 (스코어링 입력 테이블)
# ---------------------------------------------------------------------------

def cagr(first, last, years):
    if first is None or last is None or pd.isna(first) or pd.isna(last) or first <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def growth(prev, cur):
    if prev is None or cur is None or pd.isna(prev) or pd.isna(cur) or prev <= 0:
        return None
    return cur / prev - 1


def build_features(customs, comtrade, wb, imf) -> pd.DataFrame:
    base = pd.DataFrame(COUNTRIES, columns=["country", "iso2", "iso3", "m49", "region", "lotte_base"])
    feats = base.copy()

    # --- 관세청: 한국 -> 해당국 수출 (4개 HS 합계 + HS별 2025)
    if customs is not None and not customs.empty:
        tot = customs.groupby(["iso2", "period"])["exp_usd"].sum().unstack()
        f = pd.DataFrame(index=tot.index)
        f["kr_exp_2025_usd"] = tot.get("2025")
        f["kr_exp_2021_usd"] = tot.get("2021")
        f["kr_exp_2025ytd_usd"] = tot.get("2025YTD")  # 2026 YTD 증가율의 기저 (score.py 작은 기저 판정)
        f["kr_exp_cagr_21_25"] = [cagr(a, b, 4) for a, b in zip(tot.get("2021"), tot.get("2025"))]
        f["kr_exp_ytd_growth_26"] = [growth(a, b) for a, b in zip(tot.get("2025YTD"), tot.get("2026YTD"))]
        by_hs = customs[customs.period == "2025"].pivot_table(index="iso2", columns="hs", values="exp_usd", aggfunc="sum")
        by_hs.columns = [f"kr_exp_2025_hs{c}" for c in by_hs.columns]
        f = f.join(by_hs)
        feats = feats.merge(f, left_on="iso2", right_index=True, how="left")

    # --- Comtrade: 국가별 최신 보고연도 기준 수입규모, 점유율, 수입 CAGR
    if comtrade is not None and not comtrade.empty:
        ct = comtrade.copy()
        ct["imp_usd"] = pd.to_numeric(ct["imp_usd"], errors="coerce")
        ct["year"] = pd.to_numeric(ct["year"], errors="coerce")
        agg = ct.groupby(["reporter_m49", "year", "partner_m49"])["imp_usd"].sum().reset_index()
        rows = []
        for m49, g in agg.groupby("reporter_m49"):
            world = g[g.partner_m49 == 0].set_index("year")["imp_usd"].dropna()
            world = world[world > 0]
            if world.empty:
                continue
            latest, first = int(world.index.max()), int(world.index.min())
            def share(pcode):
                sel = g[(g.year == latest) & (g.partner_m49 == pcode)]["imp_usd"].dropna()
                if sel.empty:  # 상대국 행 자체가 없으면 0이 아니라 결측으로 둠
                    return None
                return sel.sum() / world[latest]
            rows.append({"m49": int(m49), "ct_latest_year": latest,
                         "imp_total_usd": world[latest],
                         "imp_cagr": cagr(world[first], world[latest], latest - first),
                         "share_kr": share(410), "share_jp": share(392), "share_cn": share(156)})
        if rows:
            feats = feats.merge(pd.DataFrame(rows), on="m49", how="left")

    # --- World Bank: 지표별 최신 유효값 + 그 기준연도 (출처 표기용)
    if wb is not None and not wb.empty:
        w = wb.dropna(subset=["value"]).sort_values("year")
        latest = w.groupby(["iso3", "indicator"]).tail(1)
        piv = latest.pivot(index="iso3", columns="indicator", values="value")
        yrs = latest.pivot(index="iso3", columns="indicator", values="year")
        piv.columns = [f"wb_{WB_INDICATORS.get(c, c)}" for c in piv.columns]
        yrs.columns = [f"wb_{WB_INDICATORS.get(c, c)}_year" for c in yrs.columns]
        feats = feats.merge(piv.join(yrs), left_on="iso3", right_index=True, how="left")

    # --- IMF: 2026~2030 성장률 전망 평균, 2026 1인당 GDP
    if imf is not None and not imf.empty:
        m = imf.copy()
        m["value"] = pd.to_numeric(m["value"], errors="coerce")
        g = m[(m.indicator == "NGDP_RPCH") & (m.year.isin(IMF_FORECAST_YEARS))].groupby("iso3")["value"].mean()
        p = m[(m.indicator == "NGDPDPC") & (m.year == 2026)].set_index("iso3")["value"]
        i = m[(m.indicator == "PCPIPCH") & (m.year == 2026)].set_index("iso3")["value"]
        imf_f = pd.DataFrame({"imf_gdp_growth_fcst_26_30": g, "imf_gdp_pc_2026": p, "imf_inflation_2026": i})
        feats = feats.merge(imf_f, left_on="iso3", right_index=True, how="left")

    return feats


# ---------------------------------------------------------------------------
# 8. 실행
# ---------------------------------------------------------------------------

def run_source(name, path: Path, fn, refresh: bool):
    if path.exists() and not refresh:
        print(f"[{name}] 캐시 사용: {path.name} (다시 받으려면 --refresh)")
        return pd.read_csv(path, dtype={"hs": str})
    df = fn()
    if df is not None and not df.empty:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  -> 저장 {path.relative_to(BASE_DIR)} ({len(df)}행)")
    else:
        print(f"  -> {name}: 받은 데이터 없음")
    return df


def main():
    ap = argparse.ArgumentParser(description="K-푸드 수출 유망국 공개데이터 수집기")
    ap.add_argument("--only", nargs="*", choices=["customs", "comtrade", "wb", "imf"],
                    help="일부 소스만 수집")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 다시 수집")
    args = ap.parse_args()

    load_env()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LOG["started_at"] = datetime.now().isoformat(timespec="seconds")
    want = set(args.only or ["customs", "comtrade", "wb", "imf"])

    customs = comtrade = wb = imf = None
    p = {k: RAW_DIR / f for k, f in [("customs", "customs_kr_exports.csv"), ("comtrade", "comtrade_imports.csv"),
                                     ("wb", "worldbank.csv"), ("imf", "imf.csv")]}

    if "customs" in want:
        key = os.environ.get("DATA_GO_KR_KEY")
        if not key:
            print("[관세청] DATA_GO_KR_KEY가 .env에 없어 건너뜀")
        else:
            try:
                customs = run_source("관세청", p["customs"], lambda: fetch_customs(key), args.refresh)
            except RuntimeError as e:
                log_error("customs", str(e))
                print("  -> 인증키 문제로 중단. 활용신청 승인 후 1~2시간 뒤 다시 시도하거나 "
                      "'일반 인증키(Decoding)'를 넣었는지 확인하세요.")
    if "comtrade" in want:
        comtrade = run_source("Comtrade", p["comtrade"], lambda: fetch_comtrade(os.environ.get("COMTRADE_KEY")), args.refresh)
    if "wb" in want:
        wb = run_source("World Bank", p["wb"], fetch_worldbank, args.refresh)
    if "imf" in want:
        imf = run_source("IMF", p["imf"], fetch_imf, args.refresh)

    # 이번에 안 받은 소스는 기존 캐시가 있으면 읽어서 요약표에 포함
    def cached(df, path):
        if df is None and path.exists():
            return pd.read_csv(path, dtype={"hs": str})
        return df
    customs, comtrade, wb, imf = (cached(customs, p["customs"]), cached(comtrade, p["comtrade"]),
                                  cached(wb, p["wb"]), cached(imf, p["imf"]))

    feats = build_features(customs, comtrade, wb, imf)
    out = DATA_DIR / "country_features.csv"
    feats.to_csv(out, index=False, encoding="utf-8-sig")
    LOG["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (DATA_DIR / "collect_log.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: {out.relative_to(BASE_DIR)} ({len(feats)}개국 x {feats.shape[1]}개 열)")
    if LOG["errors"]:
        print(f"경고 {len(LOG['errors'])}건 -> data/collect_log.json 확인")


if __name__ == "__main__":
    sys.exit(main())
