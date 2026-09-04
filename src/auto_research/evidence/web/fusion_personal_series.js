(() => {
  "use strict";
  // Pure public projection and rendering; Fusion owns requests, tabs and events.
  const ROOT_KEYS = "schema_version source_id entity_uid series_index name x_column y_column uncertainty_column x_unit y_unit uncertainty_unit total_rows valid_points missing_rows invalid_rows uncertainty_missing_rows uncertainty_invalid_rows points".split(" ");
  const POINT_KEYS = "row x y x_text y_text uncertainty uncertainty_text status".split(" ");
  const COUNTS = "valid_points missing_rows invalid_rows uncertainty_missing_rows uncertainty_invalid_rows".split(" ");
  const own = (value, keys) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.prototype.hasOwnProperty.call(value, key));
  const text = (value, limit = 500) => typeof value === "string" && value.length <= limit;
  const unit = value => value === null || text(value, 200);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const number = value => value === null || typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e150;
  const matchesText = (value, original) => {
    if (value === null) return true;
    const lexical = original.trim().replace(/−/g, "-");
    return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(lexical) && Number(lexical) === value && (value !== 0 || !/[1-9]/.test(lexical.split(/[eE]/)[0]));
  };
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const label = (name, value) => `${name}${value ? ` (${value})` : "（单位未提供）"}`;

  function validate(raw, expected) {
    if (!own(raw, ROOT_KEYS) || raw.schema_version !== "personal-series-plot-v1" || raw.source_id !== expected?.sourceId || raw.entity_uid !== expected?.entityUid || raw.series_index !== expected?.seriesIndex || !integer(raw.series_index) || raw.series_index > 199) return null;
    if (!text(raw.source_id) || !text(raw.entity_uid) || !text(raw.name) || !raw.name || !text(raw.x_column) || !raw.x_column || !text(raw.y_column) || !raw.y_column || raw.x_column === raw.y_column || !(raw.uncertainty_column === null || text(raw.uncertainty_column) && raw.uncertainty_column)) return null;
    if (![raw.x_unit, raw.y_unit, raw.uncertainty_unit].every(unit) || raw.uncertainty_column !== null && raw.uncertainty_unit !== raw.y_unit || raw.uncertainty_column === null && raw.uncertainty_unit !== null) return null;
    if (!integer(raw.total_rows) || raw.total_rows > 5000 || !Array.isArray(raw.points) || raw.points.length !== raw.total_rows || !COUNTS.every(key => integer(raw[key]) && raw[key] <= raw.total_rows)) return null;
    const counts = Object.fromEntries(COUNTS.map(key => [key, 0])), points = [];
    for (let index = 0; index < raw.points.length; index++) {
      const p = raw.points[index];
      if (!own(p, POINT_KEYS) || p.row !== index + 1 || ![p.x, p.y, p.uncertainty].every(number) || !text(p.x_text, 10000) || !text(p.y_text, 10000) || !(p.uncertainty_text === null || text(p.uncertainty_text, 10000))) return null;
      if (!matchesText(p.x, p.x_text) || !matchesText(p.y, p.y_text) || p.uncertainty !== null && (p.uncertainty_text === null || !matchesText(p.uncertainty, p.uncertainty_text))) return null;
      const missing = !p.x_text.trim() || !p.y_text.trim(), status = missing ? "missing" : p.x === null || p.y === null ? "invalid" : "valid";
      if (p.status !== status || !p.x_text.trim() && p.x !== null || !p.y_text.trim() && p.y !== null || p.uncertainty !== null && p.uncertainty < 0) return null;
      counts[{valid:"valid_points",missing:"missing_rows",invalid:"invalid_rows"}[status]]++;
      if (raw.uncertainty_column === null) {
        if (p.uncertainty !== null || p.uncertainty_text !== null) return null;
      } else {
        if (p.uncertainty_text === null) return null;
        if (!p.uncertainty_text.trim()) { if (p.uncertainty !== null) return null; counts.uncertainty_missing_rows++; }
        else if (p.uncertainty === null) counts.uncertainty_invalid_rows++;
      }
      points.push(Object.fromEntries(POINT_KEYS.map(key => [key, p[key]])));
    }
    if (!COUNTS.every(key => counts[key] === raw[key])) return null;
    return {...Object.fromEntries(ROOT_KEYS.filter(key => key !== "points").map(key => [key, raw[key]])), points};
  }

  function sourceText(plot, p) {
    return `原始第 ${p.row} 行 · ${label(plot.x_column, plot.x_unit)}：${p.x_text || "缺失"} · ${label(plot.y_column, plot.y_unit)}：${p.y_text || "缺失"}${plot.uncertainty_column ? ` · ${label(plot.uncertainty_column, plot.uncertainty_unit)}：${p.uncertainty_text || "缺失"}` : ""}`;
  }

  function geometry(plot) {
    const drawable = plot.points.filter(p => p.status === "valid");
    if (!drawable.length) return null;
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (const p of drawable) { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); ymin = Math.min(ymin, p.y - (p.uncertainty ?? 0)); ymax = Math.max(ymax, p.y + (p.uncertainty ?? 0)); }
    if (xmin === xmax) { const pad = Math.abs(xmin) * .05 || 1; xmin -= pad; xmax += pad; }
    if (ymin === ymax) { const pad = Math.abs(ymin) * .05 || 1; ymin -= pad; ymax += pad; }
    const x = value => 84 + (value - xmin) / (xmax - xmin) * 636, y = value => 314 - (value - ymin) / (ymax - ymin) * 278;
    const segments = []; let segment = [];
    for (const p of plot.points) { if (p.status !== "valid") { if (segment.length) segments.push(segment); segment = []; } else segment.push(p); }
    if (segment.length) segments.push(segment);
    return {xmin, xmax, ymin, ymax, x, y, segments};
  }

  function render(plot, selectedRow = null) {
    const g = geometry(plot), active = plot.points.find(p => p.row === selectedRow) || plot.points.find(p => p.status === "valid") || plot.points[0];
    const coordinate = value => Number(value.toFixed(3)), tick = value => Number(value.toPrecision(5)).toString();
    const ticks = g ? Array.from({length:5}, (_, index) => { const xv = g.xmin + (g.xmax - g.xmin) * index / 4, yv = g.ymin + (g.ymax - g.ymin) * index / 4; return `<line class="fusion-series-grid-line" x1="84" x2="720" y1="${coordinate(g.y(yv))}" y2="${coordinate(g.y(yv))}"/><text x="76" y="${coordinate(g.y(yv)+4)}" text-anchor="end">${tick(yv)}</text><text x="${coordinate(g.x(xv))}" y="336" text-anchor="middle">${tick(xv)}</text>`; }).join("") : "";
    const paths = g ? g.segments.filter(segment => segment.length > 1).map(segment => `<polyline class="fusion-series-line" points="${segment.map(p => `${coordinate(g.x(p.x))},${coordinate(g.y(p.y))}`).join(" ")}"/>`).join("") : "";
    const dots = g ? plot.points.filter(p => p.status === "valid").map(p => { const x=coordinate(g.x(p.x)),y=coordinate(g.y(p.y)),lo=p.uncertainty===null?null:coordinate(g.y(p.y-p.uncertainty)),hi=p.uncertainty===null?null:coordinate(g.y(p.y+p.uncertainty)); return `${lo===null?"":`<path class="fusion-series-error" data-series-error-row="${p.row}" d="M${x},${lo}V${hi}M${x-4},${lo}H${x+4}M${x-4},${hi}H${x+4}"/>`}<circle class="fusion-series-point" data-series-point="${p.row}" cx="${x}" cy="${y}" r="3.5" tabindex="${p.row===active?.row?0:-1}" role="button" aria-label="${esc(sourceText(plot,p))}"><title>${esc(sourceText(plot,p))}</title></circle>`; }).join("") : "";
    const chart = g ? `<svg class="fusion-series-svg" viewBox="0 0 760 380" role="group" aria-label="${esc(plot.name)}：已确认全系列，原始行序，无拟合"><g class="fusion-series-axes">${ticks}<path d="M84,36V314H720"/><text x="402" y="370" text-anchor="middle">${esc(label(plot.x_column,plot.x_unit))}</text><text transform="translate(18 175) rotate(-90)" text-anchor="middle">${esc(label(plot.y_column,plot.y_unit))}</text></g>${paths}${dots}</svg>` : `<p class="fusion-series-terminal" role="status">${plot.total_rows?"没有可绘制的有效数值；缺失或非法数据未补零。":"该系列没有数据行。"}</p>`;
    const statuses = {valid:"有效",missing:"缺失（断点）",invalid:"非法（断点）"};
    return `<section class="fusion-series-plot"><h2>${esc(plot.name)}</h2><p class="fusion-series-counts">完整原表 ${plot.total_rows} 行 · 有效 ${plot.valid_points} · 缺失 ${plot.missing_rows} · 非法 ${plot.invalid_rows}${plot.uncertainty_column?` · 不确定度缺失 ${plot.uncertainty_missing_rows} / 非法 ${plot.uncertainty_invalid_rows}`:""}</p><p class="fusion-series-help">按原始行序展示；断点不连接，不拟合、不插值。点选或用方向键查看原始数值。</p>${chart}<output data-series-selection aria-live="polite">${active?esc(sourceText(plot,active)):"没有可选数据行"}</output><details class="fusion-series-source"><summary>查看完整系列的原始数值（${plot.total_rows} 行）</summary><div class="fusion-private-table-scroll" tabindex="0"><table class="fusion-data-grid"><caption>原始单元格文本；不以绘图浮点坐标代替数值</caption><thead><tr><th>原始行</th><th>${esc(label(plot.x_column,plot.x_unit))}</th><th>${esc(label(plot.y_column,plot.y_unit))}</th>${plot.uncertainty_column?`<th>${esc(label(plot.uncertainty_column,plot.uncertainty_unit))}</th>`:""}<th>状态</th></tr></thead><tbody>${plot.points.map(p=>`<tr><th><button type="button" data-series-source-row="${p.row}">${p.row}</button></th><td>${esc(p.x_text)}</td><td>${esc(p.y_text)}</td>${plot.uncertainty_column?`<td>${esc(p.uncertainty_text)}</td>`:""}<td>${statuses[p.status]}</td></tr>`).join("")}</tbody></table></div></details></section>`;
  }
  globalThis.AutoResearchPersonalSeries = Object.freeze({schemaVersion:"personal-series-plot-v1", validate, render, sourceText, geometry});
})();
