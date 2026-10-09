// frontend/src/LiveView.jsx — the Kratt command right now and the last two hours of grid power.
// One axis (W): measured grid power, Kratt's target (baseline ± requested) while a command runs,
// and the commands as UP/DOWN bands. Hover or arrow keys show every value at a time; a table
// view lists them per minute.
import { useEffect, useMemo, useRef, useState } from 'react';

const POLL_MS = 10000;
const PLOT_H = 200;
const PAD = { left: 56, right: 14, top: 22, bottom: 26 };   // bottom = x-axis label band
const GAP_MS = 60000;   // a longer gap between points breaks the line

const fmtW = (w) => {
  if (w === null || w === undefined) return '–';
  const sign = w < 0 ? '−' : '';
  const a = Math.abs(w);
  return a >= 1000 ? `${sign}${(a / 1000).toFixed(2)} kW` : `${sign}${Math.round(a)} W`;
};
const fmtTick = (w) => {
  const sign = w < 0 ? '−' : '';
  const a = Math.abs(w);
  return a >= 1000 ? `${sign}${+(a / 1000).toFixed(1)} kW` : `${sign}${a} W`;
};
const clock = (ms, seconds = false) => new Date(ms).toLocaleTimeString([], {
  hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), hour12: false,
});
const marketLabel = (m) => (m === 'AFRR' ? 'aFRR' : m === 'MFRR' ? 'mFRR' : null);
// Where Kratt wants the grid: the baseline plus the requested power (DOWN = more import, UP = more export)
const targetOf = (p) => (p.signal && p.baseline_w !== null && p.baseline_w !== undefined
  ? p.baseline_w + (p.signal === 'DOWN' ? 1 : -1) * (p.requested_w ?? 0) : null);
// Power delivered in the commanded direction (grid deviation from the baseline)
const deliveredOf = (p) => (p.signal && p.grid_w !== null && p.baseline_w !== null && p.baseline_w !== undefined
  ? (p.signal === 'DOWN' ? 1 : -1) * (p.grid_w - p.baseline_w) : null);

const niceStep = (range) => {
  const raw = range / 5;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
};

function LiveChart({ data, windowS }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState(null);   // index into data
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const end = data[data.length - 1].ms;   // rendered only with data
  const start = end - windowS * 1000;
  const values = data.flatMap((d) => [d.grid_w, d.target]).filter((v) => v !== null && v !== undefined);
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  if (hi - lo < 1000) { hi += 500; lo -= 500; }
  const step = niceStep(hi - lo);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;

  const plotW = width - PAD.left - PAD.right;
  const height = PAD.top + PLOT_H + PAD.bottom;
  const x = (ms) => PAD.left + ((ms - start) / (end - start)) * plotW;
  const y = (v) => PAD.top + ((hi - v) / (hi - lo)) * PLOT_H;

  const path = (key) => {
    let d = '';
    let prev = null;
    for (const p of data) {
      const v = p[key];
      if (v === null || v === undefined) { prev = null; continue; }
      d += `${prev && p.ms - prev.ms <= GAP_MS ? 'L' : 'M'}${x(p.ms).toFixed(1)},${y(v).toFixed(1)}`;
      prev = p;
    }
    return d;
  };
  // Commands as bands: runs of the same direction
  const bands = [];
  for (const p of data) {
    const last = bands[bands.length - 1];
    if (p.signal && last && last.signal === p.signal && p.ms - last.endMs <= GAP_MS) last.endMs = p.ms;
    else if (p.signal) bands.push({ signal: p.signal, market: p.market, startMs: p.ms, endMs: p.ms });
  }
  const yTicks = [];
  for (let v = lo; v <= hi + 1e-6; v += step) yTicks.push(v);
  const xTicks = [];
  for (let t = Math.ceil(start / 1800000) * 1800000; t <= end; t += 1800000) xTicks.push(t);

  const nearest = (ms) => {
    let a = 0;
    let b = data.length - 1;
    while (b - a > 1) {
      const m = (a + b) >> 1;
      if (data[m].ms < ms) a = m; else b = m;
    }
    return Math.abs(data[a].ms - ms) <= Math.abs(data[b].ms - ms) ? a : b;
  };
  const onMove = (e) => {
    if (!data.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    setHover(nearest(start + ((px - PAD.left) / plotW) * (end - start)));
  };
  const onKey = (e) => {
    if (!data.length) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); setHover((h) => Math.max(0, (h ?? data.length - 1) - (e.shiftKey ? 30 : 1))); }
    if (e.key === 'ArrowRight') { e.preventDefault(); setHover((h) => Math.min(data.length - 1, (h ?? data.length - 1) + (e.shiftKey ? 30 : 1))); }
  };
  const h = hover !== null && data[hover] ? data[hover] : null;
  // Right of the crosshair, or left of it near the right edge
  const tipLeft = h ? (x(h.ms) + 230 < width ? x(h.ms) + 12 : Math.max(0, x(h.ms) - 232)) : 0;

  return (
    <div ref={wrapRef} className="live-chart">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} tabIndex={0}
        role="img" aria-label="Grid power and Kratt commands over the last two hours. Use the arrow keys to read values."
        onPointerMove={onMove} onPointerLeave={() => setHover(null)} onFocus={() => setHover(data.length - 1)}
        onBlur={() => setHover(null)} onKeyDown={onKey}>
        {bands.map((b) => {
          const x0 = Math.max(PAD.left, x(b.startMs));
          const x1 = Math.min(PAD.left + plotW, x(b.endMs + 10000));
          return (
            <g key={`${b.signal}-${b.startMs}`}>
              <rect x={x0} y={PAD.top} width={Math.max(1, x1 - x0)} height={PLOT_H} className={`live-band live-band-${b.signal}`} />
              {x1 - x0 >= 34 && <text x={x0 + 4} y={PAD.top - 6} className="live-band-label">{b.signal}</text>}
            </g>
          );
        })}
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={y(v)} y2={y(v)} className={v === 0 ? 'live-zero' : 'live-grid'} />
            <text x={PAD.left - 8} y={y(v) + 4} textAnchor="end" className="live-tick">{fmtTick(v)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t} x={x(t)} y={PAD.top + PLOT_H + 18} textAnchor="middle" className="live-tick">{clock(t)}</text>
        ))}
        <path d={path('target')} className="live-line live-line-target" />
        <path d={path('grid_w')} className="live-line live-line-grid" />
        {h && (
          <g>
            <line x1={x(h.ms)} x2={x(h.ms)} y1={PAD.top} y2={PAD.top + PLOT_H} className="live-crosshair" />
            {h.target !== null && <circle cx={x(h.ms)} cy={y(h.target)} r={4} className="live-dot live-dot-target" />}
            {h.grid_w !== null && <circle cx={x(h.ms)} cy={y(h.grid_w)} r={4} className="live-dot live-dot-grid" />}
          </g>
        )}
      </svg>
      {h && (
        <div className="live-tip" style={{ left: tipLeft }} aria-live="polite">
          <div className="muted small">{clock(h.ms, true)}{h.signal ? ` · ${h.signal} ${marketLabel(h.market) ?? ''}` : ' · no command'}</div>
          <div className="live-tip-row"><span className="key key-grid" /><strong>{fmtW(h.grid_w)}</strong><span className="muted">grid</span></div>
          {h.target !== null && (
            <>
              <div className="live-tip-row"><span className="key key-target" /><strong>{fmtW(h.target)}</strong><span className="muted">target</span></div>
              <div className="live-tip-row"><span className="key" /><strong>{fmtW(h.delivered)}</strong><span className="muted">delivered of {fmtW(h.requested_w)}</span></div>
              <div className="live-tip-row"><span className="key" /><strong>{fmtW(h.baseline_w)}</strong><span className="muted">baseline</span></div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function LiveView({ apiBase }) {
  const [live, setLive] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let stop = false;
    const load = () => fetch(`${apiBase}/api/live`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.status))))
      .then((data) => { if (!stop) { setLive(data); setFailed(false); } })
      .catch(() => { if (!stop) setFailed(true); });   // keep the last render
    load();
    const poll = setInterval(load, POLL_MS);
    return () => { stop = true; clearInterval(poll); };
  }, [apiBase]);

  const data = useMemo(() => (live?.points ?? []).map((p) => ({
    ...p, ms: Date.parse(p.t), target: targetOf(p), delivered: deliveredOf(p),
  })), [live]);
  // Table view: one row per minute, newest first
  const minutes = useMemo(() => {
    const rows = new Map();
    for (const p of data) {
      const key = Math.floor(p.ms / 60000) * 60000;
      const r = rows.get(key) ?? { ms: key, grid: [], target: [], signal: null, market: null };
      if (p.grid_w !== null) r.grid.push(p.grid_w);
      if (p.target !== null) r.target.push(p.target);
      if (p.signal) { r.signal = p.signal; r.market = p.market; }
      rows.set(key, r);
    }
    const avg = (a) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : null);
    return [...rows.values()].reverse().map((r) => ({ ...r, grid: avg(r.grid), target: avg(r.target) }));
  }, [data]);

  if (!live) return null;
  const last = data[data.length - 1];
  const lastCommand = [...data].reverse().find((p) => p.signal);
  const sinceMs = live.since ? Date.parse(live.since) : null;
  const delivered = last ? last.delivered : null;
  const pct = delivered !== null && live.requested_w ? Math.round((delivered / live.requested_w) * 100) : null;

  return (
    <section className="card live" aria-label="Kratt right now">
      <div className="live-status">
        <div className="muted small">
          Kratt right now{live.updated_at ? ` · ${clock(Date.parse(live.updated_at), true)}` : ''}
          {failed && <span className="err"> · not updating</span>}
        </div>
        {live.signal ? (
          <>
            <div className="live-head">
              <span className={`pill pill-${live.signal}`}>{live.signal}</span>
              {marketLabel(live.market) && <span className={`badge badge-${marketLabel(live.market)}`}>{marketLabel(live.market)}</span>}
            </div>
            {sinceMs && last && <div className="muted small">since {clock(sinceMs)} · {Math.max(0, Math.round((last.ms - sinceMs) / 60000))} min</div>}
            <dl className="live-figures">
              <div><dt>Requested</dt><dd>{fmtW(live.requested_w)}</dd></div>
              <div><dt>Delivered</dt><dd>{fmtW(delivered)}{pct !== null && <span className="muted small"> {pct}%</span>}</dd></div>
              <div><dt>Grid now</dt><dd>{fmtW(live.grid_w)}</dd></div>
              <div><dt>Baseline</dt><dd>{fmtW(live.baseline_w)}</dd></div>
            </dl>
          </>
        ) : (
          <>
            <div className="live-head"><span className="live-idle">No command</span></div>
            <dl className="live-figures">
              <div><dt>Grid now</dt><dd>{fmtW(live.grid_w)}</dd></div>
              <div><dt>Last command</dt><dd>{lastCommand ? `${lastCommand.signal} · ${clock(lastCommand.ms)}` : 'over 2 h ago'}</dd></div>
            </dl>
          </>
        )}
      </div>
      <div className="live-plot">
        <div className="live-legend">
          <span><span className="key key-grid" />Grid power (+ import / − export)</span>
          <span><span className="key key-target" />Kratt target (baseline ± requested)</span>
          <span><span className="swatch live-band-DOWN" />DOWN</span>
          <span><span className="swatch live-band-UP" />UP</span>
        </div>
        {data.length ? <LiveChart data={data} windowS={live.window_s} /> : <div className="muted small live-empty">Collecting readings…</div>}
        <details className="live-table">
          <summary className="small">Table view (per minute)</summary>
          <table>
            <thead><tr><th>Time</th><th>Grid</th><th>Command</th><th>Target</th></tr></thead>
            <tbody>
              {minutes.map((r) => (
                <tr key={r.ms}>
                  <td className="num">{clock(r.ms)}</td>
                  <td className="num">{fmtW(r.grid)}</td>
                  <td>{r.signal ? `${r.signal} ${marketLabel(r.market) ?? ''}` : '–'}</td>
                  <td className="num">{fmtW(r.target)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </div>
    </section>
  );
}
