// frontend/src/LiveView.jsx — the Kratt command right now, and a graph of grid power for any time.
//
// One axis (W): measured grid power, Kratt's target (baseline ± requested) where the window is
// detailed enough, and the commands as UP/DOWN bands (aFRR striped, mFRR solid). Each legend entry
// hides or shows its part of the graph. The graph window is the page's period: a period picked at
// the top frames the graph (live from its start while it reaches now), and moving the graph sets
// the period to Custom with its window once it settles. Live follows now from the tracker's own
// readings up to 2 h; any other window comes from Home Assistant (/api/graph: 10 s history up to
// 6 h, 5-minute or hourly statistics beyond). Navigate with the presets, ◀ ▶, Go to, the overview
// strip, scrolling or pinch (zoom), dragging (zoom to the selected range), Shift-drag, sideways
// scroll or a touch drag (pan) and double-click (zoom out). Hover or arrow keys read values; the
// table view lists them.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const POLL_MS = 10000;
const LIVE_REFRESH_MS = 60000;      // wider live windows come from Home Assistant
const STATS_REFRESH_MS = 300000;    // live windows over DETAIL_SPAN use 5-minute or hourly means
const OVERVIEW_REFRESH_MS = 600000;
const REPORT_MS = 600;              // the graph sets the page period once a move settles
const MIN = 60000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const LIVE_SPAN = 2 * HOUR;         // what the tracker keeps in memory
const DETAIL_SPAN = 6 * HOUR;       // longest window with 10-second history
const MIN_SPAN = 5 * MIN;
const MAX_SPAN = 31 * DAY;
const PRESETS = [['15 min', 15 * MIN], ['1 h', HOUR], ['2 h', 2 * HOUR], ['6 h', 6 * HOUR], ['24 h', DAY], ['7 d', 7 * DAY]];
const PLOT_H = 200;
const PAD = { left: 56, right: 14, top: 22, bottom: 26 };   // bottom = x-axis label band
const GAP = { '10s': MIN, '5min': 11 * MIN, '1h': 2.2 * HOUR };   // a longer gap breaks the line
const RESOLUTION_TEXT = {
  '10s': '10-second readings from Home Assistant history',
  '5min': '5-minute means (Home Assistant statistics)',
  '1h': 'hourly means (Home Assistant statistics)',
};
const TICK_STEPS = [MIN, 2 * MIN, 5 * MIN, 10 * MIN, 15 * MIN, 30 * MIN, HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY];

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
const dayLabel = (ms) => new Date(ms).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'numeric' });
const stamp = (ms) => `${new Date(ms).toLocaleDateString('et-EE')} ${clock(ms)}`;
const fmtSpan = (ms) => (ms < 2 * HOUR ? `${Math.round(ms / MIN)} min`
  : ms < 2 * DAY ? `${+(ms / HOUR).toFixed(1)} h` : `${+(ms / DAY).toFixed(1)} d`);
const marketLabel = (m) => (m === 'AFRR' ? 'aFRR' : m === 'MFRR' ? 'mFRR' : null);
// Where Kratt wants the grid: the baseline plus the requested power (DOWN = more import, UP = more export)
const targetOf = (p) => (p.signal && p.baseline_w !== null && p.baseline_w !== undefined
  ? p.baseline_w + (p.signal === 'DOWN' ? 1 : -1) * (p.requested_w ?? 0) : null);
// Power delivered in the commanded direction (grid deviation from the baseline)
const deliveredOf = (p) => (p.signal && p.grid_w !== null && p.baseline_w !== null && p.baseline_w !== undefined
  ? (p.signal === 'DOWN' ? 1 : -1) * (p.grid_w - p.baseline_w) : null);
const prepare = (points) => points.map((p) => ({
  ...p, ms: Date.parse(p.t), target: targetOf(p) ?? null, delivered: deliveredOf(p) ?? null,
  signal: p.signal ?? null, baseline_w: p.baseline_w ?? null, requested_w: p.requested_w ?? null,
}));
// Commands as bands from 10 s points (the live view has no separate list)
const bandsFromPoints = (data) => {
  const bands = [];
  for (const p of data) {
    const last = bands[bands.length - 1];
    if (p.signal && last && last.signal === p.signal && last.market === p.market && p.ms - last.endMs <= MIN) last.endMs = p.ms + 10000;
    else if (p.signal) bands.push({ signal: p.signal, market: p.market, startMs: p.ms, endMs: p.ms + 10000 });
  }
  return bands;
};
const bandsFromCommands = (commands) => (commands ?? []).map((c) => ({
  signal: c.signal, market: c.market, startMs: Date.parse(c.start), endMs: Date.parse(c.end),
}));
const bandShown = (hidden) => (b) => !hidden.has(b.signal) && !hidden.has(b.market);
// aFRR bands are striped in their direction's color, mFRR bands solid (each svg has its own ids)
function Hatches({ id }) {
  return ['DOWN', 'UP'].map((s) => (
    <pattern key={s} id={`${id}-${s}`} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width={6} height={6} className={`live-band-${s}`} />
      <line x1={1.5} y1={0} x2={1.5} y2={6} className={`live-hatch live-hatch-${s}`} />
    </pattern>
  ));
}
const bandProps = (b, id) => (b.market === 'AFRR'
  ? { className: 'live-band', fill: `url(#${id}-${b.signal})` }
  : { className: `live-band live-band-${b.signal}` });
// The view that shows a period: All as the last 31 days, live from the start while the period
// reaches now (to = null), else the fixed window (its last 31 days at most)
const fitPeriod = (p) => {
  if (!p || p.from === null) return { live: true, span: MAX_SPAN };
  if (p.to === null) return { live: true, from: p.from };
  const start = Math.max(p.from, p.to - MAX_SPAN);
  return { live: false, start, end: Math.max(p.to, start + MIN_SPAN) };
};
// Which parts of the graph the legend has hidden (remembered per browser)
const HIDDEN_KEY = 'graph-hidden';
const loadHidden = () => {
  try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]')); } catch { return new Set(); }
};
const niceStep = (range) => {
  const raw = range / 5;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
};
// Time ticks on local clock boundaries, at most one per ~90 px
const timeTicks = (start, end, plotW) => {
  const step = TICK_STEPS.find((s) => (end - start) / s <= plotW / 90) ?? TICK_STEPS[TICK_STEPS.length - 1];
  const offset = -new Date(start).getTimezoneOffset() * MIN;
  const ticks = [];
  for (let t = Math.ceil((start + offset) / step) * step - offset; t <= end; t += step) {
    const local = new Date(t);
    const midnight = local.getHours() === 0 && local.getMinutes() === 0;
    ticks.push({ t, label: step >= DAY || midnight ? dayLabel(t) : clock(t) });
  }
  return ticks;
};
const nearestIndex = (data, ms) => {
  let a = 0;
  let b = data.length - 1;
  while (b - a > 1) {
    const m = (a + b) >> 1;
    if (data[m].ms < ms) a = m; else b = m;
  }
  return Math.abs(data[a].ms - ms) <= Math.abs(data[b].ms - ms) ? a : b;
};
const useWidth = () => {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
};

function PowerChart({ data, bands, hidden, start, end, resolution, loading, onView }) {
  const [wrapRef, width] = useWidth();
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);       // index into data
  const [selection, setSelection] = useState(null);   // [x0, x1] px while dragging a range
  const [panning, setPanning] = useState(false);
  const gesture = useRef(null);
  const pointers = useRef(new Map());

  const plotW = width - PAD.left - PAD.right;
  const height = PAD.top + PLOT_H + PAD.bottom;
  const span = end - start;
  const visible = data.filter((p) => p.ms >= start - span * 0.05 && p.ms <= end + span * 0.05);
  const showGrid = !hidden.has('grid');
  const showTarget = !hidden.has('target');
  // The y-axis fits what is shown
  const values = visible.flatMap((d) => [showGrid ? d.grid_w : null, showTarget ? d.target : null])
    .filter((v) => v !== null && v !== undefined);
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  if (hi - lo < 1000) { hi += 500; lo -= 500; }
  const step = niceStep(hi - lo);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const x = (ms) => PAD.left + ((ms - start) / span) * plotW;
  const y = (v) => PAD.top + ((hi - v) / (hi - lo)) * PLOT_H;
  const gap = GAP[resolution] ?? MIN;

  const path = (key) => {
    let d = '';
    let prev = null;
    for (const p of visible) {
      const v = p[key];
      if (v === null || v === undefined) { prev = null; continue; }
      d += `${prev && p.ms - prev.ms <= gap ? 'L' : 'M'}${x(p.ms).toFixed(1)},${y(v).toFixed(1)}`;
      prev = p;
    }
    return d;
  };
  const yTicks = [];
  for (let v = lo; v <= hi + 1e-6; v += step) yTicks.push(v);
  const xTicks = timeTicks(start, end, plotW);

  // Pointer position → px in the SVG's own coordinates, and px → time
  const toPx = (clientX) => {
    const rect = svgRef.current.getBoundingClientRect();
    return ((clientX - rect.left) / rect.width) * width;
  };
  const timeAt = (px) => start + ((px - PAD.left) / plotW) * span;
  const inPlot = (px) => Math.max(PAD.left, Math.min(PAD.left + plotW, px));
  const zoomAround = (t, factor, s0 = start, e0 = end) => onView(t - (t - s0) * factor, t + (e0 - t) * factor);

  // Scroll zooms around the pointer (trackpad pinch arrives as Ctrl + scroll); sideways scroll or
  // Shift + scroll moves. A native listener: React's wheel handler is passive.
  const wheel = useRef(null);
  useEffect(() => {
    wheel.current = (e) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? plotW : 1;   // lines / pages → px
      const dx = (e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX) * unit;
      const dy = e.shiftKey ? 0 : e.deltaY * unit;
      if (Math.abs(dx) > Math.abs(dy)) {
        const shift = (dx / plotW) * span;
        onView(start + shift, end + shift);
      } else if (dy) {
        zoomAround(timeAt(toPx(e.clientX)), Math.exp(Math.max(-0.5, Math.min(0.5, dy * 0.004))));
      }
    };
  });
  useEffect(() => {
    const el = svgRef.current;
    const handler = (e) => wheel.current?.(e);
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, toPx(e.clientX));
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gesture.current = { kind: 'pinch', d0: Math.max(10, Math.abs(a - b)), mid: timeAt((a + b) / 2), start0: start, end0: end };
      setSelection(null);
    } else {
      // A mouse drag selects a range; Shift-drag or a finger moves the graph
      const kind = e.shiftKey || e.pointerType === 'touch' ? 'pan' : 'select';
      const x0 = kind === 'select' ? inPlot(toPx(e.clientX)) : toPx(e.clientX);
      gesture.current = { kind, x0, start0: start, end0: end, moved: false };
      setPanning(kind === 'pan');
    }
  };
  const onPointerMove = (e) => {
    const px = toPx(e.clientX);
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, px);
    const g = gesture.current;
    if (!g) {
      if (visible.length) setHover(nearestIndex(data, timeAt(px)));
      return;
    }
    if (g.kind === 'pan') {
      const dx = px - g.x0;
      if (Math.abs(dx) > 3) g.moved = true;
      if (g.moved) {
        const shift = (-dx / plotW) * (g.end0 - g.start0);
        onView(g.start0 + shift, g.end0 + shift);
        setHover(null);
      }
    } else if (g.kind === 'select') {
      const x1 = inPlot(px);
      if (Math.abs(x1 - g.x0) > 3) g.moved = true;
      if (g.moved) {
        setSelection([g.x0, x1]);
        setHover(null);
      }
    } else if (g.kind === 'pinch' && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      zoomAround(g.mid, g.d0 / Math.max(10, Math.abs(a - b)), g.start0, g.end0);
    }
  };
  const onPointerUp = (e) => {
    pointers.current.delete(e.pointerId);
    const g = gesture.current;
    if (e.type === 'pointerup' && g?.kind === 'select' && g.moved) {
      const x1 = inPlot(toPx(e.clientX));
      if (Math.abs(x1 - g.x0) > 6) onView(timeAt(Math.min(g.x0, x1)), timeAt(Math.max(g.x0, x1)));
    }
    setSelection(null);
    if (!pointers.current.size) {
      gesture.current = null;
      setPanning(false);
    }
  };
  const onKey = (e) => {
    if (e.key === 'Escape' && gesture.current?.kind === 'select') {   // cancel the range being dragged
      gesture.current = null;
      setSelection(null);
      return;
    }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomAround(start + span / 2, 0.5); return; }
    if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomAround(start + span / 2, 2); return; }
    if (!data.length) return;
    const from = hover ?? nearestIndex(data, end);
    if (e.key === 'ArrowLeft') { e.preventDefault(); setHover(Math.max(0, from - (e.shiftKey ? 30 : 1))); }
    if (e.key === 'ArrowRight') { e.preventDefault(); setHover(Math.min(data.length - 1, from + (e.shiftKey ? 30 : 1))); }
  };

  const h = hover !== null && data[hover] && data[hover].ms >= start && data[hover].ms <= end ? data[hover] : null;
  const hBands = h ? bands.filter((b) => b.startMs <= h.ms && b.endMs >= h.ms) : [];
  const tipLeft = h ? (x(h.ms) + 250 < width ? x(h.ms) + 12 : Math.max(0, x(h.ms) - 252)) : 0;
  const bucket = { '5min': 5 * MIN, '1h': HOUR }[resolution];
  // The range being selected, as text over it
  let selLabel = null;
  if (selection) {
    const [s0, s1] = [timeAt(Math.min(...selection)), timeAt(Math.max(...selection))];
    const at = (ms) => (new Date(s0).toDateString() === new Date(s1).toDateString() ? clock(ms, s1 - s0 < 10 * MIN) : stamp(ms));
    const text = `${at(s0)}–${at(s1)} · ${fmtSpan(s1 - s0)}`;
    const half = text.length * 3.3;   // ~11 px font
    const mid = (selection[0] + selection[1]) / 2;
    selLabel = { text, x: Math.max(PAD.left + half, Math.min(PAD.left + plotW - half, mid)) };
  }

  return (
    <div ref={wrapRef} className={`live-chart ${loading ? 'is-loading' : ''}`}>
      <svg ref={svgRef} width={width} height={height} viewBox={`0 0 ${width} ${height}`} tabIndex={0} className={panning ? 'is-panning' : ''}
        role="img" aria-label="Grid power and Kratt commands. Scroll to zoom, drag to select a range and zoom into it, Shift-drag or scroll sideways to move, double-click to zoom out, + and − to zoom, arrow keys to read values."
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
        onPointerLeave={() => { if (!gesture.current) setHover(null); }}
        onDoubleClick={(e) => zoomAround(timeAt(toPx(e.clientX)), 2)}
        onFocus={() => { if (data.length) setHover(nearestIndex(data, end)); }} onBlur={() => setHover(null)} onKeyDown={onKey}>
        <defs>
          <clipPath id="live-plot-clip"><rect x={PAD.left} y={0} width={plotW} height={PAD.top + PLOT_H} /></clipPath>
          <Hatches id="live-hatch" />
        </defs>
        <g clipPath="url(#live-plot-clip)">
          {bands.filter(bandShown(hidden)).map((b) => {
            const x0 = Math.max(PAD.left, x(b.startMs));
            const x1 = Math.min(PAD.left + plotW, x(b.endMs));
            if (x1 <= PAD.left || x0 >= PAD.left + plotW) return null;
            return (
              <g key={`${b.signal}-${b.startMs}`}>
                <rect x={x0} y={PAD.top} width={Math.max(1, x1 - x0)} height={PLOT_H} {...bandProps(b, 'live-hatch')} />
                {x1 - x0 >= 34 && (
                  <text x={x0 + 4} y={PAD.top - 6} className="live-band-label">
                    {x1 - x0 >= 84 && marketLabel(b.market) ? `${b.signal} · ${marketLabel(b.market)}` : b.signal}
                  </text>
                )}
              </g>
            );
          })}
        </g>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={y(v)} y2={y(v)} className={v === 0 ? 'live-zero' : 'live-grid'} />
            <text x={PAD.left - 8} y={y(v) + 4} textAnchor="end" className="live-tick">{fmtTick(v)}</text>
          </g>
        ))}
        {xTicks.map(({ t, label }) => (
          <text key={t} x={x(t)} y={PAD.top + PLOT_H + 18} textAnchor="middle" className="live-tick">{label}</text>
        ))}
        <g clipPath="url(#live-plot-clip)">
          {showTarget && <path d={path('target')} className="live-line live-line-target" />}
          {showGrid && <path d={path('grid_w')} className="live-line live-line-grid" />}
        </g>
        {selection && (
          <g>
            <rect x={Math.min(...selection)} y={PAD.top} width={Math.abs(selection[1] - selection[0])} height={PLOT_H} className="live-selection" />
            <text x={selLabel.x} y={PAD.top + 14} textAnchor="middle" className="live-selection-label">{selLabel.text}</text>
          </g>
        )}
        {h && (
          <g>
            <line x1={x(h.ms)} x2={x(h.ms)} y1={PAD.top} y2={PAD.top + PLOT_H} className="live-crosshair" />
            {showTarget && h.target !== null && <circle cx={x(h.ms)} cy={y(h.target)} r={4} className="live-dot live-dot-target" />}
            {showGrid && h.grid_w !== null && <circle cx={x(h.ms)} cy={y(h.grid_w)} r={4} className="live-dot live-dot-grid" />}
          </g>
        )}
      </svg>
      {h && (
        <div className="live-tip" style={{ left: tipLeft }} aria-live="polite">
          <div className="muted small">
            {bucket ? `${stamp(h.ms - bucket / 2)}–${clock(h.ms + bucket / 2)} mean` : `${new Date(h.ms).toLocaleDateString('et-EE')} ${clock(h.ms, true)}`}
            {h.signal ? ` · ${h.signal} ${marketLabel(h.market) ?? ''}`
              : hBands.length ? ` · ${hBands.map((b) => `${b.signal} ${marketLabel(b.market) ?? ''}`.trim()).join(', ')}` : ' · no command'}
          </div>
          {showGrid && <div className="live-tip-row"><span className="key key-grid" /><strong>{fmtW(h.grid_w)}</strong><span className="muted">grid</span></div>}
          {h.target !== null && (
            <>
              {showTarget && <div className="live-tip-row"><span className="key key-target" /><strong>{fmtW(h.target)}</strong><span className="muted">target</span></div>}
              <div className="live-tip-row"><span className="key" /><strong>{fmtW(h.delivered)}</strong><span className="muted">delivered of {fmtW(h.requested_w)}</span></div>
              <div className="live-tip-row"><span className="key" /><strong>{fmtW(h.baseline_w)}</strong><span className="muted">baseline</span></div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// The last 7 days (or back to the view when it starts earlier), small: click or drag to move the graph there
function OverviewStrip({ data, bands, start, end, viewStart, viewEnd, onCenter }) {
  const [wrapRef, width] = useWidth();
  const dragging = useRef(false);
  const height = 46;
  const plotW = width - PAD.left - PAD.right;
  const x = (ms) => PAD.left + ((ms - start) / (end - start)) * plotW;
  const values = data.map((p) => p.grid_w).filter((v) => v !== null);
  const lo = Math.min(0, ...values);
  const hi = Math.max(1, ...values);
  const y = (v) => 4 + ((hi - v) / (hi - lo || 1)) * (height - 18);
  let d = '';
  let prev = null;
  for (const p of data) {
    if (p.grid_w === null) { prev = null; continue; }
    d += `${prev && p.ms - prev.ms <= GAP['1h'] ? 'L' : 'M'}${x(p.ms).toFixed(1)},${y(p.grid_w).toFixed(1)}`;
    prev = p;
  }
  const centerAt = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    onCenter(start + ((px - PAD.left) / plotW) * (end - start));
  };
  const b0 = Math.max(PAD.left, x(viewStart));
  const b1 = Math.min(PAD.left + plotW, x(viewEnd));
  const days = [];
  for (let t = new Date(start).setHours(24, 0, 0, 0); t < end; t += DAY) days.push(t);
  const labelEvery = Math.max(1, Math.ceil((days.length * 64) / plotW));   // day labels need ~64 px
  return (
    <div ref={wrapRef} className="live-overview">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="slider" tabIndex={0}
        aria-label={`${stamp(start)} – ${stamp(end)}: click or drag to move the graph there, arrow keys to step`} aria-valuemin={start} aria-valuemax={end}
        aria-valuenow={Math.round((viewStart + viewEnd) / 2)} aria-valuetext={stamp((viewStart + viewEnd) / 2)}
        onPointerDown={(e) => { dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); centerAt(e); }}
        onPointerMove={(e) => { if (dragging.current) centerAt(e); }}
        onPointerUp={() => { dragging.current = false; }}
        onKeyDown={(e) => {
          const stepMs = (viewEnd - viewStart) / 2;
          if (e.key === 'ArrowLeft') { e.preventDefault(); onCenter((viewStart + viewEnd) / 2 - stepMs); }
          if (e.key === 'ArrowRight') { e.preventDefault(); onCenter((viewStart + viewEnd) / 2 + stepMs); }
        }}>
        <defs><Hatches id="overview-hatch" /></defs>
        {bands.map((b) => (
          <rect key={`${b.signal}-${b.startMs}`} x={x(b.startMs)} y={4} width={Math.max(1, x(b.endMs) - x(b.startMs))} height={height - 18}
            {...bandProps(b, 'overview-hatch')} />
        ))}
        <path d={d} className="live-line live-overview-line" />
        {days.map((t, i) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={4} y2={height - 14} className="live-grid" />
            {i % labelEvery === 0 && <text x={x(t) + 3} y={height - 3} className="live-tick">{dayLabel(t)}</text>}
          </g>
        ))}
        {b1 > b0 && <rect x={b0} y={2} width={Math.max(3, b1 - b0)} height={height - 14} className="live-brush" />}
      </svg>
    </div>
  );
}

export default function LiveView({ apiBase, focus, period, onPeriod }) {
  const cardRef = useRef(null);
  const [live, setLive] = useState(null);
  const [failed, setFailed] = useState(false);
  // What the graph shows: live (follows now) for a span or from a start, or a fixed window
  const [view, setView] = useState(() => fitPeriod(period));
  const [peek, setPeek] = useState(false);   // showing an activation without changing the period
  const [range, setRange] = useState(null);       // /api/graph response for the view
  const [loading, setLoading] = useState(false);
  const [rangeError, setRangeError] = useState(null);
  const [overview, setOverview] = useState(null);
  const [goTo, setGoTo] = useState('');
  const [hidden, setHidden] = useState(loadHidden);
  const request = useRef(0);
  useEffect(() => {
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden])); } catch { /* storage unavailable */ }
  }, [hidden]);
  const toggle = (key) => setHidden((prev) => {
    const next = new Set(prev);
    if (!next.delete(key)) next.add(key);
    return next;
  });

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

  const loadGraph = useCallback((from, to) => {
    const id = ++request.current;
    setLoading(true);
    const q = new URLSearchParams({ from: new Date(from).toISOString(), to: new Date(to).toISOString() });
    return fetch(`${apiBase}/api/graph?${q}`)
      .then((res) => res.json().then((data) => (res.ok ? data : Promise.reject(new Error(data.detail || res.status)))))
      .then((data) => { if (id === request.current) { setRange(data); setRangeError(null); } })
      .catch((e) => { if (id === request.current) setRangeError(String(e.message || e)); })
      .finally(() => { if (id === request.current) setLoading(false); });
  }, [apiBase]);

  const liveData = useMemo(() => prepare(live?.points ?? []), [live]);
  const rangeData = useMemo(() => prepare(range?.points ?? []), [range]);
  const now = liveData.length ? liveData[liveData.length - 1].ms : (live?.updated_at ? Date.parse(live.updated_at) : null);
  // A live view from a start covers that start to now, within [MIN_SPAN, MAX_SPAN]
  const liveStart = (nowMs) => (view.from !== undefined
    ? Math.min(Math.max(view.from, nowMs - MAX_SPAN), nowMs - MIN_SPAN) : nowMs - view.span);
  const end = view.live ? now : view.end;
  const start = view.live ? (now === null ? null : liveStart(now)) : view.start;

  // Fetch the window once it settles (panning and zooming change it many times a second)
  const liveFromMemory = view.live && (start === null || now - start <= LIVE_SPAN);
  useEffect(() => {
    if (liveFromMemory) return undefined;
    const fetchNow = () => (view.live ? loadGraph(liveStart(Date.now()), Date.now()) : loadGraph(view.start, view.end));
    const timer = setTimeout(fetchNow, 300);
    const every = view.live && Date.now() - liveStart(Date.now()) > DETAIL_SPAN ? STATS_REFRESH_MS : LIVE_REFRESH_MS;
    const refresh = view.live ? setInterval(fetchNow, every) : null;
    return () => { clearTimeout(timer); if (refresh) clearInterval(refresh); };
    // liveStart only reads view
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, liveFromMemory, loadGraph]);

  // The overview strip: the last 7 days, or from the day the view starts when that is earlier
  // (31 days at most); refetched when that day changes
  const stripFrom = now === null ? null : new Date(Math.min(now - 7 * DAY, start ?? now)).setHours(0, 0, 0, 0);
  const stripTo = stripFrom !== null && now - stripFrom > MAX_SPAN ? stripFrom + MAX_SPAN : null;   // null = now
  useEffect(() => {
    if (stripFrom === null) return undefined;
    const load = () => {
      const q = new URLSearchParams({ from: new Date(stripFrom).toISOString(), to: new Date(stripTo ?? Date.now()).toISOString() });
      fetch(`${apiBase}/api/graph?${q}`).then((res) => (res.ok ? res.json() : null)).then((d) => { if (d) setOverview(d); }).catch(() => {});
    };
    load();
    const refresh = stripTo === null ? setInterval(load, OVERVIEW_REFRESH_MS) : null;
    return () => { if (refresh) clearInterval(refresh); };
  }, [apiBase, stripFrom, stripTo]);

  // The period picked at the top frames the graph; a period the graph reported itself doesn't move it
  const [shownPeriod, setShownPeriod] = useState(period?.key);
  if (period && period.key !== shownPeriod) {
    setShownPeriod(period.key);
    if (period.source !== 'graph') {
      setView(fitPeriod(period));
      setPeek(false);
    }
  }
  // The user's own moves set the page period once they settle; any other view change cancels that
  const userMoved = useRef(false);
  const report = useRef(onPeriod);
  useEffect(() => { report.current = onPeriod; });
  useEffect(() => {
    if (!userMoved.current) return undefined;
    userMoved.current = false;
    const timer = setTimeout(() => {
      if (!view.live) report.current?.(view.start, view.end);
      else report.current?.(view.from !== undefined ? view.from : Date.now() - view.span, null);
    }, REPORT_MS);
    return () => clearTimeout(timer);
  }, [view]);

  // "Show in graph" from an activation: the window around it (a new focus object each click),
  // without changing the period
  const [shownFocus, setShownFocus] = useState(null);
  if (focus && focus !== shownFocus) {
    setShownFocus(focus);
    const s = Date.parse(focus.start) - 10 * MIN;
    setView({ live: false, start: s, end: Math.max(Date.parse(focus.end) + 10 * MIN, s + 30 * MIN) });
    setPeek(true);
  }
  useEffect(() => {
    if (focus) cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focus]);

  const data = liveFromMemory ? liveData : rangeData;
  const bands = useMemo(() => (liveFromMemory ? bandsFromPoints(liveData) : bandsFromCommands(range?.commands)),
    [liveFromMemory, liveData, range]);
  const resolution = liveFromMemory ? '10s' : range?.resolution;

  // Table view: per minute for 10 s readings, per bucket otherwise; newest first
  const tableRows = useMemo(() => {
    const inView = data.filter((p) => start !== null && p.ms >= start && p.ms <= end);
    const commandAt = (ms) => bands.find((b) => b.startMs <= ms && b.endMs >= ms) ?? null;
    if (resolution !== '10s') return inView.map((p) => ({ ms: p.ms, grid: p.grid_w, target: null, command: commandAt(p.ms) })).reverse();
    const rows = new Map();
    for (const p of inView) {
      const key = Math.floor(p.ms / MIN) * MIN;
      const r = rows.get(key) ?? { ms: key, grid: [], target: [], command: null };
      if (p.grid_w !== null) r.grid.push(p.grid_w);
      if (p.target !== null) r.target.push(p.target);
      if (p.signal) r.command = { signal: p.signal, market: p.market };
      rows.set(key, r);
    }
    const avg = (a) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : null);
    return [...rows.values()].reverse().map((r) => ({ ...r, grid: avg(r.grid), target: avg(r.target) }));
  }, [data, bands, resolution, start, end]);

  if (!live) return null;
  const span = start !== null ? end - start : view.span;
  // Every move of the user's own becomes the page period (see the report effect above)
  const userSetView = (v) => {
    userMoved.current = true;
    setPeek(false);
    setView(v);
  };
  // Any pan or zoom leaves live mode; the window stays within [MIN_SPAN, MAX_SPAN] and not past now
  const changeView = (s, e) => {
    const width = Math.min(MAX_SPAN, Math.max(MIN_SPAN, e - s));
    const mid = (s + e) / 2;
    let ne = mid + width / 2;
    const latest = now ?? Date.now();
    if (ne > latest) ne = latest;
    userSetView({ live: false, start: ne - width, end: ne });
  };
  const preset = (ms) => {
    if (view.live) userSetView({ live: true, span: ms });
    else changeView((start + end) / 2 - ms / 2, (start + end) / 2 + ms / 2);
  };
  const stepBy = (dir) => changeView(start + dir * span, end + dir * span);
  const goToTime = () => {
    const t = Date.parse(goTo);
    if (!Number.isNaN(t)) changeView(t - span / 2, t + span / 2);
  };

  const last = liveData[liveData.length - 1];
  const lastCommand = [...liveData].reverse().find((p) => p.signal);
  const sinceMs = live.since ? Date.parse(live.since) : null;
  const delivered = last ? last.delivered : null;
  const pct = delivered !== null && live.requested_w ? Math.round((delivered / live.requested_w) * 100) : null;
  const atNow = view.live || (now !== null && end >= now - 1000);
  const longPeriod = period && (period.from === null || (period.to ?? now) - period.from > MAX_SPAN);
  const overviewData = prepare(overview?.points ?? []);
  // A legend entry is a button that hides or shows its part of the graph
  const entry = (key, mark, label) => (
    <button type="button" className="legend-toggle" aria-pressed={!hidden.has(key)} onClick={() => toggle(key)}
      title={hidden.has(key) ? 'Show on the graph' : 'Hide from the graph'}>
      {mark}{label}
    </button>
  );
  const overviewStart = overview ? Date.parse(overview.from) : null;
  const overviewEnd = overview ? Date.parse(overview.to) : null;

  return (
    <section ref={cardRef} className="card live" aria-label="Kratt right now and grid power graph">
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
        <div className="live-toolbar" role="group" aria-label="Graph time range">
          <span className="live-nav">
          <button type="button" className="iconbtn iconbtn-sm" aria-label="Earlier" disabled={start === null} onClick={() => stepBy(-1)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>
          </button>
          <div className="seg seg-sm">
            {PRESETS.map(([label, ms]) => (
              <button key={label} type="button" className={Math.abs(span - ms) < 1000 ? 'on' : ''} aria-pressed={Math.abs(span - ms) < 1000}
                onClick={() => preset(ms)}>{label}</button>
            ))}
          </div>
          <button type="button" className="iconbtn iconbtn-sm" aria-label="Later" disabled={start === null || atNow} onClick={() => stepBy(1)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
          </button>
          </span>
          <button type="button" className={`btn-chip ${view.live ? 'on' : ''}`} aria-pressed={view.live}
            onClick={() => userSetView({ live: true, span: Math.min(span, 7 * DAY) })}>
            <span className={`dot ${view.live ? 'dot-pos' : 'dot-off'}`} />Live
          </button>
          <form className="live-goto" onSubmit={(e) => { e.preventDefault(); goToTime(); }}>
            <input type="datetime-local" aria-label="Go to date and time" value={goTo} onChange={(e) => setGoTo(e.target.value)} />
            <button type="submit" className="btn-link" disabled={!goTo}>Go to</button>
          </form>
        </div>
        <div className="live-legend" role="group" aria-label="Show or hide on the graph">
          {entry('grid', <span className="key key-grid" />, 'Grid power (+ import / − export)')}
          {resolution === '10s' && entry('target', <span className="key key-target" />, 'Kratt target (baseline ± requested)')}
          {entry('DOWN', <span className="swatch live-band-DOWN" />, 'DOWN')}
          {entry('UP', <span className="swatch live-band-UP" />, 'UP')}
          {entry('MFRR', <span className="swatch swatch-mfrr" />, 'mFRR')}
          {entry('AFRR', <span className="swatch swatch-afrr" />, 'aFRR')}
        </div>
        {start !== null && (data.length || range || rangeError) ? (
          <PowerChart data={data} bands={bands} hidden={hidden} start={start} end={end} resolution={resolution}
            loading={loading && !liveFromMemory} onView={changeView} />
        ) : (
          <div className="muted small live-empty">{loading ? 'Loading…' : 'Collecting readings…'}</div>
        )}
        <div className="muted small live-caption">
          {start !== null && `${stamp(start)} – ${new Date(start).toDateString() === new Date(end).toDateString() ? clock(end) : stamp(end)}${view.live ? ' · live' : ''}`}
          {resolution && ` · ${RESOLUTION_TEXT[resolution]}`}
          {resolution && resolution !== '10s' && ' · zoom in to 6 h or less for 10-second detail and the Kratt target'}
          {loading && !liveFromMemory && ' · loading…'}
          {range?.notice && !liveFromMemory && <span className="err"> · {range.notice}</span>}
          {rangeError && !liveFromMemory && <span className="err"> · {rangeError}</span>}
          {!peek && longPeriod && ' · the graph shows the last 31 days of the period'}
          {peek && period && (
            <> · <button type="button" className="btn-link" onClick={() => { setView(fitPeriod(period)); setPeek(false); }}>← Back to {period.label}</button></>
          )}
        </div>
        {overview && overviewStart !== null && start !== null && (
          <OverviewStrip data={overviewData} bands={bandsFromCommands(overview.commands).filter(bandShown(hidden))} start={overviewStart} end={overviewEnd}
            viewStart={start} viewEnd={end} onCenter={(t) => changeView(t - span / 2, t + span / 2)} />
        )}
        <div className="muted small">Scroll to zoom · drag to select a range and zoom in · Shift-drag or scroll sideways to move · double-click to zoom out</div>
        <details className="live-table">
          <summary className="small">Table view ({resolution === '10s' ? 'per minute' : resolution === '5min' ? '5-minute means' : 'hourly means'})</summary>
          <table>
            <thead><tr><th>Time</th><th>Grid</th><th>Command</th><th>Target</th></tr></thead>
            <tbody>
              {tableRows.map((r) => (
                <tr key={r.ms}>
                  <td className="num">{span > DAY ? stamp(r.ms) : clock(r.ms)}</td>
                  <td className="num">{fmtW(r.grid)}</td>
                  <td>{r.command ? `${r.command.signal} ${marketLabel(r.command.market) ?? ''}` : '–'}</td>
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
