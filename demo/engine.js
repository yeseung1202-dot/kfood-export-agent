// score.py의 score()·drivers()를 브라우저로 옮긴 것. 같은 입력이면 같은 값 (python build_demo.py가 대조)
(function (g) {
  const isNum = v => v !== null && v !== undefined && !Number.isNaN(v);

  // 0~100 백분위 (최솟값 0, 최댓값 100, 동점은 평균 순위). 결측은 결측 그대로
  function pctRank(vals) {
    const idx = vals.map((v, i) => i).filter(i => isNum(vals[i]));
    const n = idx.length, out = vals.map(() => null);
    if (n <= 1) { idx.forEach(i => { out[i] = 50; }); return out; }
    idx.sort((a, b) => vals[a] - vals[b]);
    for (let s = 0; s < n;) {
      let e = s;
      while (e + 1 < n && vals[idx[e + 1]] === vals[idx[s]]) e++;
      const rank = (s + e) / 2 + 1;
      for (let k = s; k <= e; k++) out[idx[k]] = (rank - 1) / (n - 1) * 100;
      s = e + 1;
    }
    return out;
  }

  // 지표 백분위 -> 축 점수(중립 수축) -> 유효 축 가중평균
  function axisScores(rows, inds, weights) {
    const pct = {};
    for (const i of inds) {
      const p = pctRank(rows.map(r => r.v[i.col]));
      pct[i.col] = i.sign > 0 ? p : p.map(x => (x === null ? null : 100 - x));
    }
    const axes = {}, flags = rows.map(() => "");
    for (const axis of Object.keys(weights)) {
      const cols = inds.filter(i => i.axis === axis && pct[i.col].some(isNum)).map(i => i.col);
      if (!cols.length) { axes[axis] = rows.map(() => null); continue; }
      axes[axis] = rows.map((_, k) => {
        const got = cols.map(c => pct[c][k]).filter(isNum);
        const share = got.length / cols.length;
        if (got.length < cols.length / 2) flags[k] = (flags[k] + ` 데이터 부족(${axis})`).trim();
        return got.length ? share * (got.reduce((a, b) => a + b, 0) / got.length) + (1 - share) * 50 : null;
      });
    }
    const total = rows.map((_, k) => {
      let num = 0, den = 0;
      for (const [axis, w] of Object.entries(weights)) {
        const a = axes[axis][k];
        if (isNum(a)) { num += a * w; den += w; }
      }
      return den ? num / den : null;
    });
    return { pct, axes, total, flags };
  }

  // 내림차순, 동점은 원래 순서 (pandas rank(ascending=False, method="first"))
  function rankDesc(vals) {
    const idx = vals.map((v, i) => i).filter(i => isNum(vals[i]));
    idx.sort((a, b) => vals[b] - vals[a] || a - b);
    const out = vals.map(() => null);
    idx.forEach((i, r) => { out[i] = r + 1; });
    return out;
  }

  // weights: [성장성, 트렌드, 수출입] (합 1일 필요 없음), track: "new" | "expand", sample: 가상 샘플 반영 여부
  function score(D, weights, alpha, track, sample) {
    const rows = D.rows.map(r => {
      const v = { ...r.v, internal_margin_pct: sample && r.country in D.sample ? D.sample[r.country] : null };
      const src = { ...r.src };
      if (isNum(v.internal_margin_pct)) src.internal_margin_pct = D.sampleSrc;
      return { country: r.country, gate: r.gate[track], v, src, yr: r.yr, pct: {}, flags: "",
               axes: {}, stage1: null, stage1Rank: null, feas: null, final: null, finalRank: null };
    });
    const ok = rows.filter(r => r.gate === "");
    const stageInds = D.indicators.filter(i => D.axes.includes(i.axis));
    const w = Object.fromEntries(D.axes.map((a, k) => [a, weights[k]]));
    const s1 = axisScores(ok, stageInds, w);
    ok.forEach((r, k) => {
      for (const c in s1.pct) r.pct[c] = s1.pct[c][k];
      for (const a of D.axes) r.axes[a] = s1.axes[a][k];
      r.stage1 = s1.total[k];
      r.flags = s1.flags[k];
    });
    rankDesc(rows.map(r => r.stage1)).forEach((x, k) => { rows[k].stage1Rank = x; });

    const top = rows.filter(r => r.stage1Rank !== null && r.stage1Rank <= D.topN);
    const s2 = axisScores(top, D.indicators.filter(i => i.axis === D.feas), { [D.feas]: 1 });
    top.forEach((r, k) => {
      for (const c in s2.pct) r.pct[c] = s2.pct[c][k];
      r.feas = s2.total[k];
      r.final = isNum(r.feas) ? (1 - alpha) * r.stage1 + alpha * r.feas : r.stage1;
      if (!isNum(r.feas)) r.flags = (r.flags + " 실행가능성 미입력").trim();
      if (D.hub.includes(r.country)) r.flags = (r.flags + " 재수출 허브(수입액 과대 가능)").trim();
    });
    rankDesc(rows.map(r => r.final)).forEach((x, k) => { rows[k].finalRank = x; });
    return rows;
  }

  // 점수를 올린/깎은 지표 상위 k개 (원값·백분위·출처·기준연도)
  function drivers(D, row, k = 3) {
    const items = D.indicators.filter(i => isNum(row.pct[i.col])).map(i => ({
      col: i.col, axis: i.axis, value: row.v[i.col], pct: row.pct[i.col],
      source: row.src[i.col] || i.source, year: row.yr[i.col] ?? (i.year.startsWith("@") ? null : i.year),
    }));
    items.sort((a, b) => b.pct - a.pct);
    return { up: items.slice(0, k), down: items.slice().reverse().slice(0, k) };
  }

  g.KScore = { pctRank, score, drivers };
})(globalThis);
