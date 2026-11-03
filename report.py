"""
국가별 추천 리포트 생성 (LOTTE AX Challenge 2026 / 롯데웰푸드 과제)

사용법
  python report.py              # TOP3 리포트 -> docs/reports/top3_report.md
  python report.py --top 5
  python report.py --template   # LLM 없이 템플릿으로만 작성

동작
  1) score.py 출력(scores.csv, drivers.json, sensitivity.csv)에서 국가별 '근거 목록'을 만든다
  2) LLM이 근거 목록만 보고 문장을 쓴다. 키 우선순위(keys.txt 또는 .env):
       CLOVA_STUDIO_API_KEY (HyperCLOVA X, 모델 CLOVA_MODEL, 기본 HCX-007)
       -> OPENAI_API_KEY (모델 OPENAI_MODEL) -> 둘 다 없으면 템플릿
  3) verify(): 본문에 나온 모든 숫자를 근거 목록과 코드로 대조 -> 근거에 없는 숫자가 있으면 리포트에 경고
     (LLM 환각 방지. 수치는 오직 score.py 출력에서만 온다)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import requests

from collect import load_env
from score import AXES, DATA_DIR, FEAS, INDICATORS, MAJOR_MARKET_USD

OUT_DIR = Path(__file__).resolve().parent / "docs" / "reports"
# (이름, 키 변수, 모델 변수, 기본 모델, OpenAI 호환 chat/completions 주소)
PROVIDERS = [
    ("HyperCLOVA X", "CLOVA_STUDIO_API_KEY", "CLOVA_MODEL", "HCX-007",
     "https://clovastudio.stream.ntruss.com/v1/openai/chat/completions"),
    ("OpenAI", "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-5.6-luna", "https://api.openai.com/v1/chat/completions"),
]

LABEL = {i[0]: i[3] for i in INDICATORS}

# 재수출 허브는 수입액이 소비보다 커서 '소비시장'이 아니라 '거점'으로 해석 (2026-10-31 사용자 결정: 점수 유지, 거점 논리로 설명)
HUB_ROLE = {
    "싱가포르": "동남아 거점 — 원롯데 싱가포르 합작법인(2026-07 출범, 한·일 롯데 아시아 사업 총괄)과 연계한 역내 유통·재수출 허브",
    "네덜란드": "EU 관문 — 로테르담항을 통한 유럽 역내 유통 허브. 수입액에 EU 재수출 물량 포함",
    "홍콩": "중화권 관문 — 중국 본토·마카오 재수출 허브",
    "아랍에미리트": "중동 관문 — 제벨알리항을 통한 GCC·아프리카 재수출 허브",
}

# 지표별 표기 형식 (원값 -> 리포트 문자열). 비율 지표는 0.123 -> 12.3%
PCT = {"imp_cagr", "kr_exp_cagr_21_25", "kr_exp_ytd_growth_26"}
FMT = {
    "gap_jp_kr": lambda v: f"{v * 100:+.1f}%p",
    "imf_gdp_growth_fcst_26_30": lambda v: f"{v:.1f}%",
    "imf_gdp_pc_2026": lambda v: f"{v:,.0f}달러",
    "wb_0_14세비중": lambda v: f"{v:.1f}%",
    "wb_도시화율": lambda v: f"{v:.1f}%",
    **{t: (lambda v: f"{v:g}/100") for t in ("trend_korean_food", "trend_korean_snacks", "trend_pepero", "trend_kpop")},
    "lead_time_days": lambda v: f"{v:.1f}일",
    "lpi_2023": lambda v: f"{v:.1f}점",
    "tariff_hs1905_pct": lambda v: f"{v:g}%",
    "halal_required": lambda v: "필요" if v else "불필요",
    "sugar_tax": lambda v: "있음" if v else "없음",
    "oecd_risk": lambda v: f"{v:.0f}등급",
}


def fmt(indicator: str, value: float) -> str:
    if indicator in PCT:
        return f"{value * 100:.1f}%"
    return FMT.get(indicator, lambda v: f"{v:g}")(value)


# ---------------------------------------------------------------------------
# 근거 목록
# ---------------------------------------------------------------------------

def facts_for(country: str, row: pd.Series, drv: dict, sens: pd.Series | None) -> list[dict]:
    """한 나라의 근거 목록. 리포트에 쓸 수 있는 숫자는 여기 있는 것뿐."""
    facts = [{"label": "최종 순위", "text": f"{int(row['final_rank'])}위", "source": "score.py"},
             {"label": "최종 점수", "text": f"{row['final']:.1f}점", "source": "score.py (0~100)"},
             {"label": "1단계(과제 3축) 순위", "text": f"{int(row['stage1_rank'])}위", "source": "score.py"}]
    for a in AXES + [FEAS]:
        if pd.notna(row.get(a)):
            facts.append({"label": f"{a} 축 점수", "text": f"{row[a]:.1f}점", "source": "score.py (백분위 0~100)"})
    if sens is not None:
        facts.append({"label": "몬테카를로 TOP3 진입률", "text": f"{sens['top3_rate'] * 100:.0f}%",
                      "source": "score.py 몬테카를로 2,000회 (가중치·α·지표 1개 제외 무작위)"})
    if isinstance(row.get("supply_base"), str):
        facts.append({"label": "공급 거점", "text": row["supply_base"], "source": "feasibility.csv"})
    if country in HUB_ROLE:
        facts.append({"label": "시장 성격(재수출 허브)", "text": HUB_ROLE[country],
                      "source": "수입액이 실제 소비보다 클 수 있어 소비시장보다 거점으로 해석"})
    for kind in ("up", "down"):
        for d in drv.get(kind, []):
            # '한국 제과류 수출액'을 LLM이 '국내 수출'로 읽어서 방향을 명시 (2026-10-31 HCX-007)
            label = LABEL.get(d["indicator"], d["indicator"]).replace("한국 제과류", f"한국→{country} 제과류")
            own = d["source"] if d["source"] != label else ""  # 국가별 출처(<열>_src)가 따로 있을 때만 표기
            value = fmt(d["indicator"], d["value"])
            facts.append({"label": label, "value": value,
                          # 강점/약점 표시: HCX-007이 약점(리드타임 백분위 0)을 '최단·효율적'으로 뒤집어 쓴 일이 있음 (2026-10-31)
                          "text": f"{value} (백분위 {d['percentile']:.0f}, "
                                  f"{'강점: 후보국 중 유리한 편' if kind == 'up' else '약점: 후보국 중 불리한 편'})",
                          "source": ", ".join(x for x in (own, f"기준 {d['year']}") if x), "kind": kind})
    for f in facts:  # {n} 자리에 코드가 넣을 값. 긴 설명(허브 역할)은 앞부분만 (HCX-007이 설명문 전체를 문장에 박음)
        f.setdefault("value", f["text"].split(" — ")[0])
    return facts


PLACE = re.compile(r"\{(\d+)\}")
PLACE_UNIT = re.compile(r"\{(\d+)\}(위|점|%|일|달러|등급)?")


def fill(raw: str, facts: list[dict]) -> str:
    """LLM이 쓴 {n} 자리에 근거 값을 넣고 [n]을 붙인다. 없는 번호는 그대로 남겨 verify가 잡게 한다.
    모델이 단위를 또 붙이면('{1}위' -> '1위위') 지우고, 숫자 없는 중괄호('{백분위}')는 지운다 (2026-10-31 HCX-007)."""
    def put(m):
        i, unit = int(m[1]), m[2] or ""
        if not 0 < i <= len(facts):
            return m[0]
        v = facts[i - 1]["value"]
        return f"{v}[{i}]" + ("" if v.endswith(unit) else unit)
    return re.sub(r"\{[^}\d]*\}", "", PLACE_UNIT.sub(put, raw))


def stray(raw: str, facts: list[dict]) -> list[str]:
    """{n} 밖에 LLM이 직접 쓴 숫자. 지표 이름·출처에 있는 숫자(예: 0~14세, HS 1905)만 허용."""
    allowed = set().union(*(numbers(f["label"] + " " + f["source"]) for f in facts))
    return sorted(numbers(PLACE.sub("", raw)) - allowed)


def eun(word: str) -> str:
    """받침에 맞는 조사 은/는."""
    last = word[-1]
    return word + ("은" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "는")


def numbers(text: str) -> set[str]:
    """본문 숫자 추출 (콤마 제거, 끝의 .0 제거). 근거 번호 [n]은 제외."""
    text = re.sub(r"\[\d+\]", "", text)
    return {n.replace(",", "").removesuffix(".0") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def verify(body: str, facts: list[dict]) -> list[str]:
    """근거 목록에 없는 숫자 목록. 10 이하 정수(TOP3, 3축 등)는 허용."""
    allowed = set().union(*(numbers(" ".join((f["label"], f["text"], f["source"]))) for f in facts)) if facts else set()
    bad = {n for n in numbers(body) - allowed if not (n.isdigit() and int(n) <= 10)}
    # 순위는 작은 정수라도 근거에 'N위'로 있어야 함 (HCX-007이 관심도 값 2.0을 '2위'로 쓴 일이 있음, 2026-10-31)
    ranks = set(re.findall(r"(\d+)\s*위", " ".join(f["text"] for f in facts)))
    bad |= {f"{n}위" for n in re.findall(r"(\d+)\s*위", re.sub(r"\[\d+\]", "", body)) if n not in ranks}
    return sorted(bad)


# ---------------------------------------------------------------------------
# 문장 작성: LLM 또는 템플릿
# ---------------------------------------------------------------------------

PROMPT = """너는 롯데웰푸드 해외사업 분석가다. 아래 '근거 목록'만 사용해 {country}를 K-제과 수출 유망국으로 추천하는 보고서 단락을 한국어로 써라.
규칙:
- 숫자를 직접 쓰지 마라(아라비아 숫자 금지). 수치가 필요한 자리에는 근거 번호를 {{n}} 형태로만 써라.
  코드가 그 자리에 근거 목록의 값(단위 포함)을 넣는다. {{n}}은 3개 이상 쓰고, 반드시 지표 이름 바로 뒤에 써라.
  {{n}} 뒤에 단위를 붙이지 말고, {{n}}을 두 개 붙여 쓰지 마라(틀린 예: {{11}}{{12}}).
  예: 한국→영국 제과류 수출액 CAGR이 {{11}}로 높다. / 리드타임이 {{13}}로 후보국 중 불리한 편이다.
- '약점'으로 표시된 근거를 강점처럼 쓰지 마라. 약점은 리스크·한계에서 다룬다. 백분위는 후보국 안에서의 상대 위치다.
- 구성: (1) 한 줄 추천 요지 (2) 시장성 근거 2~3문장 (3) 실행 타당성(물류·관세·규제) 1~2문장 (4) 리스크·한계 1~2문장
- 400자 내외, 과장 금지(강력히·반드시·최고·보장 같은 말 금지), 근거가 약하면 약하다고 써라.
- 소제목('추천 요지:' 등)과 굵은 글씨(**)를 쓰지 말고 이어지는 문장으로 써라.
- 회사 계획처럼 쓰지 마라('…할 예정이다', '…할 계획이다' 금지). 분석 결과와 시사점으로 써라.
- 근거 목록에 없는 판단(예: 규제가 개방적이다, 경쟁이 적다)을 만들지 마라. 관세·물류·위험은 근거 목록에 있는 것만 쓴다.
- 단락만 써라. 글자 수, 작성 메모, '※' 설명, 구분선(---), 제목을 덧붙이지 마라.

근거 목록:
{facts}"""

# 프롬프트를 어기고 붙는 메타 줄 (2026-10-31 HCX-007: '(약 390자)', '*총 글자 수: 398자*', '※ 숫자 표기: …', '---')
META_LINE = re.compile(r"^\s*(\*?\(?[^()\n]{0,15}\d+\s*자\s*\)?\*?|\*?총\s*글자\s*수.*|※.*|-{3,}|#+\s.*)\s*$")


def clean_llm(text: str) -> str:
    """메타 줄만 지운다. 본문 문장·숫자는 바꾸지 않으며, 숫자 대조(verify)는 이 결과에 돌린다."""
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in text.splitlines() if not META_LINE.match(l))).strip()


def pick_provider() -> dict | None:
    """키가 있는 첫 LLM. load_env() 이후에 불러야 keys.txt 값이 반영됨."""
    for name, key_var, model_var, default, url in PROVIDERS:
        if os.environ.get(key_var):
            return {"name": name, "key": os.environ[key_var], "model": os.environ.get(model_var) or default, "url": url}
    return None


REVIEW = """너는 보고서 검토자다. 아래 [단락]을 [근거 목록]과 한 문장씩 대조해 고쳐 써라.
- 근거 목록과 반대되는 서술을 고쳐라. 특히 '약점'으로 표시된 근거를 장점처럼 쓴 문장
  (예: 리드타임이 약점인데 '가깝다·접근성이 높다·효율적'이라고 쓴 문장).
- 근거 목록에 없는 판단과 과장(강력히·반드시·최고·보장 등)을 빼라. 소제목과 굵은 글씨(**)를 지워라.
- {{n}} 자리표시는 그대로 유지하라. 숫자를 직접 쓰지 마라. 자리표시가 가리키는 근거와 문장 내용이 맞는지 확인하라.
- 고친 단락만 출력하라. 설명·메모·글자 수를 쓰지 마라.

[근거 목록]
{facts}

[단락]
{draft}"""

MAX_TRIES = 3


def ask(llm: dict, prompt: str) -> str:
    """LLM 1회 호출 (OpenAI 호환 chat/completions)."""
    body = {"model": llm["model"], "max_completion_tokens": 3000,  # HCX-007·GPT-5 계열은 max_tokens 거부
            "messages": [{"role": "user", "content": prompt}]}
    # temperature는 GPT-5 계열에서 기본값 외 거부될 수 있어 지정하지 않음
    # HCX-007은 추론에 토큰을 다 써서 내용이 빌 수 있어 reasoning_effort=none으로 한 번 더 (항만해운 공모전 clova.py 경험)
    for extra in ({}, {"reasoning_effort": "none"}):
        try:  # 2026-10-31 HCX-007 응답이 120초를 넘겨 전체 실행이 멈춘 일이 있음
            r = requests.post(llm["url"], headers={"Authorization": f"Bearer {llm['key']}"}, timeout=180,
                              json=body | extra)
        except requests.RequestException as e:
            raise RuntimeError(f"{llm['name']} 호출 실패: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise RuntimeError(f"{llm['name']} API 오류 {r.status_code}: {r.text[:300]}")
        choice = r.json()["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        if content and choice.get("finish_reason") == "length":  # 2026-10-31 네덜란드 단락이 '로테'에서 잘림
            raise RuntimeError(f"{llm['name']} 응답이 토큰 한도로 잘림")
        if content:
            return clean_llm(content)
    raise RuntimeError(f"{llm['name']} 응답이 비어 있습니다")


def llm_paragraph(country: str, facts: list[dict], llm: dict) -> tuple[str, int]:
    """작성 -> 검토 -> {n}에 값 넣기 -> 숫자 대조. 통과할 때까지 최대 MAX_TRIES번. (단락, 시도 횟수)

    LLM은 문장만 쓰고 숫자는 코드가 넣는다: HCX-007이 반올림(20.0%->20%), 다른 지표 값 끌어오기
    (수출입 축 '백분위 83'), 값→순위 오기('순위 2')를 반복해서 숫자 작성을 LLM에서 뺐다 (2026-10-31)
    """
    listing = "\n".join(f"{{{i + 1}}} {f['label']}: {f['text']} — {f['source']}" for i, f in enumerate(facts))
    final = None
    for n in range(1, MAX_TRIES + 1):
        try:
            draft = ask(llm, PROMPT.format(country=country, facts=listing))
            raw = ask(llm, REVIEW.format(facts=listing, draft=draft))
        except RuntimeError as e:
            print(f"  {country} {n}번째 시도 실패: {e}")
            continue
        final, extra, used = fill(raw, facts), stray(raw, facts), len(set(PLACE.findall(raw)))
        bad = verify(final, facts)
        glued = bool(re.search(r"\}\s*\{", raw))  # {11}{12}: 지표 이름 없이 값만 이어 붙임
        print(f"  {country} {n}번째 시도: 자리표시 {used}개, 직접 쓴 숫자 {extra or '없음'}, "
              f"붙여 쓴 자리표시 {'있음' if glued else '없음'}, 숫자 대조 {'통과' if not bad else bad}")
        if not extra and not bad and not glued and used >= 3:
            return final, n
    if final is None:  # 모든 호출 실패 -> 리포트는 멈추지 않고 템플릿으로
        return template_paragraph(country, facts) + "\n\n(LLM 호출이 모두 실패해 템플릿으로 작성)", 0
    return final, n  # 마지막 시도도 기준 미달이면 그대로 두고 리포트에 경고가 붙음


def template_paragraph(country: str, facts: list[dict]) -> str:
    by = {f["label"]: f["text"] for f in facts}
    up = [f for f in facts if f.get("kind") == "up"]
    down = [f for f in facts if f.get("kind") == "down"]
    axes = {a: float(by[f"{a} 축 점수"].rstrip("점")) for a in AXES if f"{a} 축 점수" in by}
    best = max(axes, key=axes.get) if axes else "-"
    lines = [f"**추천 요지**: {eun(country)} 최종 {by['최종 점수']}으로 {by['최종 순위']}이며, "
             f"과제 3축 중 {best} 축이 가장 강하다 ({best} {by.get(f'{best} 축 점수', '-')}).",
             "", "**점수를 올린 근거**"]
    lines += [f"- {f['label']}: {f['text']} — {f['source']}" for f in up]
    lines += ["", "**점수를 깎은 근거 (리스크)**"]
    lines += [f"- {f['label']}: {f['text']} — {f['source']}" for f in down]
    if "시장 성격(재수출 허브)" in by:
        lines += ["", f"**시장 성격**: {by['시장 성격(재수출 허브)']}. 수입액이 실제 소비보다 클 수 있어 소비시장보다 거점으로 해석한다."]
    if "몬테카를로 TOP3 진입률" in by:
        lines += ["", f"**안정성**: 가중치·지표를 무작위로 바꾼 2,000회 중 TOP3 진입률 {by['몬테카를로 TOP3 진입률']}."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def build(top: int, use_llm: bool) -> str:
    scores = pd.read_csv(DATA_DIR / "scores.csv", encoding="utf-8-sig")
    drv = json.loads((DATA_DIR / "drivers.json").read_text(encoding="utf-8"))
    sens = pd.read_csv(DATA_DIR / "sensitivity.csv", encoding="utf-8-sig").set_index("country")
    llm = pick_provider() if use_llm else None
    how = f"LLM({llm['name']} {llm['model']}) + 숫자 대조 검증" if llm else "템플릿 (LLM 미사용)"

    ranked = scores[scores["final_rank"].notna()].sort_values("final_rank")
    s1 = scores[scores["stage1_rank"].notna()].sort_values("stage1_rank")
    majors = scores.loc[scores["gate_reason"].fillna("").str.contains("기존 주력"), "country"].tolist()
    out = ["# K-제과 수출 유망국 추천 리포트 — 신규 개척형 TOP3", "",
           f"- 작성 방식: {how}",
           "- 모든 수치는 score.py 출력(data/scores*.csv, drivers*.json, sensitivity*.csv)에서만 가져옴",
           f"- 두 트랙: 2025년 한국 제과 수출 {MAJOR_MARKET_USD / 1e4:,.0f}만 달러 이상인 기존 주력시장"
           f"({', '.join(majors)})은 '확대형'으로 분리하고, 나머지에서 '발굴'할 신규 개척형 TOP3를 뽑음."
           " 확대형 우선순위는 맨 아래 참고", "",
           "## 1단계(과제 3축)와 최종(실행가능성 반영) 비교", "",
           "| 순위 | 1단계: 성장성·트렌드·수출입 | 최종: + 롯데 실행가능성 |", "|---|---|---|"]
    out += [f"| {i + 1} | {a} | {b} |" for i, (a, b) in
            enumerate(zip(s1["country"].head(top), ranked["country"].head(top)))]

    warnings = 0
    for _, row in ranked.head(top).iterrows():
        c = row["country"]
        facts = facts_for(c, row, drv.get(c, {}), sens.loc[c] if c in sens.index else None)
        body, tries = llm_paragraph(c, facts, llm) if llm else (template_paragraph(c, facts), 0)
        bad = verify(body, facts)
        warnings += bool(bad)
        out += ["", f"## {int(row['final_rank'])}위. {c}", "", body, ""]
        how_made = f"작성→검토 {tries}회 시도" if tries else "템플릿"
        if bad:
            out.append(f"> ⚠️ 숫자 대조 실패({how_made}): 근거 목록에 없는 숫자 {', '.join(bad)} — 수동 확인 필요")
        else:
            out.append(f"> ✅ 숫자 대조 통과({how_made}): 본문의 숫자가 모두 근거 목록에 있음 (해석이 맞는지는 사람이 검토)")
        if isinstance(row.get("flags"), str) and row["flags"]:
            out.append(f"> 데이터 한계: {row['flags']}")
        if llm:
            out += ["", "<details><summary>근거 목록</summary>", ""]
            out += [f"{i + 1}. {f['label']}: {f['text']} — {f['source']}" for i, f in enumerate(facts)]
            out += ["", "</details>"]
    exp_path = DATA_DIR / "scores_expand.csv"
    if exp_path.exists():
        ex = pd.read_csv(exp_path, encoding="utf-8-sig")
        ex = ex[ex["final_rank"].notna()].sort_values("final_rank")
        es_path = DATA_DIR / "sensitivity_expand.csv"
        es = pd.read_csv(es_path, encoding="utf-8-sig").set_index("country") if es_path.exists() else None
        out += ["", "## 참고: 기존 주력시장 확대 우선순위 (확대형 트랙)", "",
                "| 순위 | 국가 | 최종 점수 | 성장성 | 트렌드 | 수출입 | 실행가능성 | TOP3 진입률 |", "|---|---|---|---|---|---|---|---|"]
        for _, r in ex.iterrows():
            rate = f"{es.loc[r['country'], 'top3_rate'] * 100:.0f}%" if es is not None and r["country"] in es.index else "-"
            out.append(f"| {int(r['final_rank'])} | {r['country']} | {r['final']:.1f} | " +
                       " | ".join(f"{r[a]:.1f}" for a in AXES + [FEAS]) + f" | {rate} |")
    print(f"리포트 {min(top, len(ranked))}개국, 숫자 대조 경고 {warnings}건")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="국가별 추천 리포트 생성")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--template", action="store_true", help="LLM 없이 템플릿으로 작성")
    args = ap.parse_args()
    load_env()
    if not (DATA_DIR / "scores.csv").exists():
        sys.exit("data/scores.csv가 없습니다. score.py를 먼저 실행하세요.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"top{args.top}_report.md"
    path.write_text(build(args.top, not args.template), encoding="utf-8")
    print(f"저장: {path.relative_to(Path(__file__).resolve().parent)}")


if __name__ == "__main__":
    sys.exit(main())
