// frontend/src/App.jsx
import { useEffect, useMemo, useState } from 'react';
import './App.css';

const API_BASE = ""; // same origin: the backend serves this UI (vite dev proxies /api)

function App() {
  const [darkMode, setDarkMode] = useState(false);
  const [data, setData] = useState([]);
  const [filter, setFilter] = useState('today');
  const [customRange, setCustomRange] = useState({ from: '', to: '' });
  const [loading, setLoading] = useState(false);
  const [priceSync, setPriceSync] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Backfill from Home Assistant history
  const toLocalInput = (d) => {
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const currentSlotStart = () => {
    const d = new Date();
    d.setMinutes(Math.floor(d.getMinutes() / 15) * 15, 0, 0);
    return d;
  };
  const [backfillOpen, setBackfillOpen] = useState(false);
  const [backfillRange, setBackfillRange] = useState(() => {
    const to = currentSlotStart();
    return { from: toLocalInput(new Date(to.getTime() - 24 * 3600000)), to: toLocalInput(to) };
  });
  const [backfill, setBackfill] = useState(null);
  const [qwReport, setQwReport] = useState([]);
  const [qwImport, setQwImport] = useState(null);

  // Import Qilowatt (KratTrade) CSV reports: each file is posted as the raw request body
  const importQwReports = async (files) => {
    const results = [];
    for (const file of files) {
      try {
        const res = await fetch(`${API_BASE}/api/qw-report`, { method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: await file.text() });
        const body = await res.json();
        results.push(res.ok
          ? `✓ ${file.name}: ${body.type === 'revenue' ? `${body.slots} revenue slots` : `${body.signals} signals${body.rows_market_corrected ? `, ${body.rows_market_corrected} row market(s) corrected` : ''}`}`
          : `✗ ${file.name}: ${body.detail || res.status}`);
      } catch (e) {
        results.push(`✗ ${file.name}: ${e}`);
      }
    }
    setQwImport(results);
    setReloadKey((k) => k + 1);
  };
  const [backfillError, setBackfillError] = useState(null);
  const backfillRunning = backfill?.state === 'running';

  // Backfill status on page load (e.g. a backfill started earlier is still running)
  useEffect(() => {
    fetch(`${API_BASE}/api/backfill`)
      .then((res) => (res.ok ? res.json() : null))
      .then((st) => { if (st) setBackfill(st); })
      .catch((e) => console.error('Backfill status fetch failed', e));
  }, []);
  // Poll while a backfill runs; reload the table when it finishes
  useEffect(() => {
    if (!backfillRunning) return undefined;
    const poll = setInterval(async () => {
      const res = await fetch(`${API_BASE}/api/backfill`).catch(() => null);
      if (!res?.ok) return;
      const st = await res.json();
      setBackfill(st);
      if (st.state !== 'running') setReloadKey((k) => k + 1);
    }, 2000);
    return () => clearInterval(poll);
  }, [backfillRunning]);

  const startBackfill = async () => {
    setBackfillError(null);
    try {
      const res = await fetch(`${API_BASE}/api/backfill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // datetime-local values are local time; the backend reads naive values as Europe/Tallinn
        body: JSON.stringify({ from: backfillRange.from, to: backfillRange.to }),
      });
      const body = await res.json();
      if (!res.ok) setBackfillError(body.detail || `Backfill failed (${res.status})`);
      else setBackfill(body);
    } catch (e) {
      setBackfillError(String(e));
    }
  };
  const [clock, setClock] = useState(() => Date.now());

  // mFRR price sync status (Baltic Transparency Dashboard), refreshed every 30 s
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/price-sync`);
        if (res.ok) setPriceSync(await res.json());
      } catch (e) {
        console.error('Price sync status fetch failed', e);
      }
    };
    load();
    const poll = setInterval(load, 30000);
    const tick = setInterval(() => setClock(Date.now()), 1000);
    return () => { clearInterval(poll); clearInterval(tick); };
  }, []);

  const fmtTime = (iso) =>
    iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '-';
  const fmtSlot = (iso) => {
    if (!iso) return '-';
    const start = new Date(iso);
    const end = new Date(start.getTime() + 15 * 60000);
    const hm = (d) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    return `${start.toLocaleDateString('et-EE')} ${hm(start)}–${hm(end)}`;
  };
  const fmtAgo = (iso) => {
    if (!iso) return '';
    const min = Math.round((clock - new Date(iso).getTime()) / 60000);
    return min < 1 ? '(just now)' : min < 120 ? `(${min} min ago)` : `(${Math.round(min / 60)} h ago)`;
  };
  const fmtIn = (iso) => {
    if (!iso) return '';
    const sec = Math.max(0, Math.round((new Date(iso).getTime() - clock) / 1000));
    return `(in ${sec} s)`;
  };

  const safeFixed = (val, digits = 3, suffix = '€') =>
    typeof val === 'number' ? `${val.toFixed(digits)} ${suffix}` : '-';
  const formatW = (v) => (typeof v === 'number' ? `${Math.round(v)} W` : '-');

  const now = new Date();
  const startOf = (unit) => {
    const d = new Date(now);
    if (unit === 'day') return new Date(d.setHours(0, 0, 0, 0));
    if (unit === 'week') {
      const day = d.getDay() || 7; // Monday=1..Sunday=7
      d.setHours(0, 0, 0, 0);
      d.setDate(d.getDate() - day + 1);
      return d;
    }
    if (unit === 'month') return new Date(d.getFullYear(), d.getMonth(), 1);
    return null;
  };

  const endOf = (unit) => {
    const d = new Date(now);
    if (unit === 'day') return new Date(d.setHours(23, 59, 59, 999));
    if (unit === 'week') {
      const start = startOf('week');
      return new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6, 23, 59, 59, 999);
    }
    if (unit === 'month') return new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59, 999);
    return null;
  };

  const getFilterRange = () => {
    switch (filter) {
      case 'today':
        return [startOf('day'), endOf('day')];
      case 'yesterday': {
        const y = new Date(now);
        y.setDate(y.getDate() - 1);
        const start = new Date(y.setHours(0, 0, 0, 0));
        const end = new Date(y.setHours(23, 59, 59, 999));
        return [start, end];
      }
      case 'this_week':
        return [startOf('week'), endOf('week')];
      case 'last_week': {
        const sw = startOf('week');
        sw.setDate(sw.getDate() - 7);
        const ew = new Date(sw);
        ew.setDate(ew.getDate() + 6);
        return [sw, new Date(ew.setHours(23, 59, 59, 999))];
      }
      case 'this_month':
        return [startOf('month'), endOf('month')];
      case 'last_month': {
        const lmStart = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        const lmEnd = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59, 999);
        return [lmStart, lmEnd];
      }
      case 'custom':
        if (!customRange.from || !customRange.to) return [null, null];
        return [new Date(customRange.from), new Date(customRange.to)];
      case 'all':
      default:
        return [null, null];
    }
  };

  const [from, to] = getFilterRange();

  // Fetch only what we need for the selected filter
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        let url = `${API_BASE}/api/mffr`;
        if (from && to) {
          // Send ISO8601 (UTC); backend compares ISO strings safely
          const qFrom = encodeURIComponent(from.toISOString());
          const qTo = encodeURIComponent(new Date(to).toISOString());
          url += `?from=${qFrom}&to=${qTo}`;
        } else if (filter === 'all') {
          url += `?limit=2000`; // cap "All" to something reasonable
        } else {
          // Fallback: if range invalid/missing, still avoid full-table dump
          url += `?limit=1000`;
        }

        const res = await fetch(url);
        const json = await res.json();
        // Official Qilowatt report figures for the same period (if imported)
        const qwUrl = `${API_BASE}/api/qw-report` + (from && to
          ? `?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(new Date(to).toISOString())}` : '');
        const qwRes = await fetch(qwUrl).catch(() => null);
        setQwReport(qwRes?.ok ? await qwRes.json() : []);

        // json shape: { "<timeslotISO>_<signal>": row, ... }: one row per slot and direction
        const enriched = Object.values(json).map((entry) => {
          const timeslot = entry.timeslot;
          const start = new Date(entry.start);
          const end = new Date(entry.end);
          const slotStart = new Date(timeslot);
          const slotEnd = new Date(slotStart);
          slotEnd.setMinutes(slotEnd.getMinutes() + 15);

          const duration =
            entry.duration_min ?? Math.round((end - start) / 60000);
          const was_backup =
            entry.was_backup ??
            (start.getMinutes() % 15 !== 0 || start.getSeconds() > 10);
          const cancelled =
            entry.cancelled ??
            end.getTime() < slotEnd.getTime() - 11000; // 11 sec buffer

          const slot_end = entry.slot_end ?? slotEnd.toISOString();

          return {
            timeslot,
            ...entry,
            duration,
            slot_end,
            was_backup,
            cancelled,
            slot_date: slotStart.toLocaleDateString('et-EE'),
            slot_time: slotStart.toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            }),
            slotStart,
          };
        });

        // Backend returns newest first; keep same UX as before (reverse to old order if desired)
        setData(enriched); // already desc; or use enriched.reverse() if you prefer asc
      } catch (e) {
        console.error('Fetch failed', e);
        setData([]);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    // re-fetch on filter or custom range change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, customRange.from, customRange.to, reloadKey]);

  const summary = useMemo(() => {
    const acc = {
      up:  { energy: 0, grid_energy: 0, profit: 0, duration: 0, count: 0, backup: 0, cancelled: 0, grid: 0, kratt: 0, ffr: 0, net: 0, priceSum: 0, priceCount: 0 },
      down:{ energy: 0, grid_energy: 0, profit: 0, duration: 0, count: 0, backup: 0, cancelled: 0, grid: 0, kratt: 0, ffr: 0, net: 0, priceSum: 0, priceCount: 0 },
      total:{ energy: 0, grid_energy: 0, profit: 0, duration: 0, count: 0, backup: 0, cancelled: 0, grid: 0, kratt: 0, ffr: 0, net: 0, priceSum: 0, priceCount: 0 },
    };

    for (const entry of data) {
      const signal = entry.signal;
      // An mFRR ramp minute (row id "…_r") is part of the next quarter's activation:
      // its energy and money count, but it isn't a separate activation
      const isRamp = Boolean(entry.id?.endsWith('_r'));
      const energy = entry.energy_kwh || 0;
      const gridEnergy = entry.grid_kwh || 0;
      const profit = entry.profit || 0;
      const duration = entry.duration || 0;
      const isBackup = !isRamp && Boolean(entry.was_backup);
      const isCancelled = !isRamp && Boolean(entry.cancelled);

      const gridCost = entry.grid_cost || 0;
      const krattFee = entry.kratt_fee || 0;
      const ffrIncome = entry.ffr_income || 0;
      const netTotal = entry.net_total || 0;
      const pricePerKwh = typeof entry.price_per_kwh === 'number' ? entry.price_per_kwh : null;

      // Totals
      acc.total.energy += energy;
      acc.total.grid_energy += gridEnergy;
      acc.total.profit += profit;
      acc.total.duration += duration;
      acc.total.backup += isBackup ? 1 : 0;
      acc.total.cancelled += isCancelled ? 1 : 0;
      acc.total.grid += gridCost;
      acc.total.kratt += krattFee;
      acc.total.ffr += ffrIncome;
      acc.total.net += netTotal;
      if (pricePerKwh !== null) {
        acc.total.priceSum += pricePerKwh;
        acc.total.priceCount += 1;
      }

      const bucket = signal === 'UP' ? acc.up : signal === 'DOWN' ? acc.down : null;
      if (bucket) {
        bucket.energy += energy;
        bucket.grid_energy += gridEnergy;
        bucket.profit += profit;
        bucket.duration += duration;
        if (!isRamp) bucket.count++;
        bucket.backup += isBackup ? 1 : 0;
        bucket.cancelled += isCancelled ? 1 : 0;
        bucket.grid += gridCost;
        bucket.kratt += krattFee;
        bucket.ffr += ffrIncome;
        bucket.net += netTotal;
        if (pricePerKwh !== null) {
          bucket.priceSum += pricePerKwh;
          bucket.priceCount += 1;
        }
      }

      if (!isRamp) acc.total.count++;
    }

    return acc;
  }, [data]);

  // Official figures per slot and direction; shown once per (slot, direction)
  const official = useMemo(() => {
    const map = new Map();
    for (const r of qwReport) {
      const pct = (r.share_pct ?? 80) / 100;
      map.set(`${r.timeslot}|UP`, { kwh: r.up_kwh, share: (r.up_net_eur ?? 0) * pct });
      map.set(`${r.timeslot}|DOWN`, { kwh: r.down_kwh, share: (r.down_net_eur ?? 0) * pct });
    }
    return map;
  }, [qwReport]);
  const officialTotal = useMemo(
    () => qwReport.reduce((acc, r) => ({ kwh: acc.kwh + (r.total_kwh || 0), share: acc.share + (r.share_eur || 0) }), { kwh: 0, share: 0 }),
    [qwReport],
  );
  // Show official figures on the main row of each (slot, direction): not a ramp row, most energy
  const officialRowId = useMemo(() => {
    const best = new Map();
    for (const e of data) {
      const key = `${e.timeslot}|${e.signal}`;
      const rank = [e.id?.endsWith('_r') ? 0 : 1, e.energy_kwh || 0];
      const cur = best.get(key);
      if (!cur || rank[0] > cur.rank[0] || (rank[0] === cur.rank[0] && rank[1] > cur.rank[1])) best.set(key, { id: e.id, rank });
    }
    return new Map([...best].map(([k, v]) => [k, v.id]));
  }, [data]);

  const formatVal = (val, digits = 2) => (val ? val.toFixed(digits) : '-');
  const percent = (count, total) => (total ? `${Math.round((count / total) * 100)}%` : '-');
  const formatDuration = (minutes) => {
    if (!minutes) return '-';
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return `${h}h ${m}min`;
  };

  const signalSplit = {
    up: percent(summary.up.count, summary.total.count),
    down: percent(summary.down.count, summary.total.count),
  };

  return (
    <div className={darkMode ? 'dark' : 'light'} style={{ padding: '2rem' }}>
      <h1 style={{ fontSize: '2rem', fontWeight: 'bold' }}>mFRR Profit Tracker</h1>

      {priceSync && (
        <div
          style={{
            marginBottom: '1rem',
            padding: '0.6rem 0.9rem',
            border: `1px solid ${priceSync.last_error ? '#d33' : '#8884'}`,
            borderRadius: 6,
            fontSize: '0.9rem',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.4rem 1.5rem',
          }}
        >
          <strong>mFRR prices · {priceSync.source} ({priceSync.area})</strong>
          <span>
            Last sync: {priceSync.last_sync_at ? `${fmtTime(priceSync.last_sync_at)} ${fmtAgo(priceSync.last_sync_at)}` : 'never'}{' '}
            {priceSync.last_sync_at && (priceSync.last_error ? <span style={{ color: '#d33' }}>✗ failed</span> : <span style={{ color: 'green' }}>✓</span>)}
          </span>
          <span title="The dashboard is only queried when a finished mFRR slot is missing its price">
            Next sync:{' '}
            {priceSync.next_sync_at
              ? `${fmtTime(priceSync.next_sync_at)} ${fmtIn(priceSync.next_sync_at)}`
              : 'not needed (no slots waiting for a price)'}
          </span>
          <span>
            Latest price data:{' '}
            {priceSync.latest_price_slot ? `${fmtSlot(priceSync.latest_price_slot)} ${fmtAgo(priceSync.latest_price_slot)}` : '-'}
            {priceSync.latest_price_slot && (
              <> · UP {priceSync.latest_up_price ?? '-'} / DOWN {priceSync.latest_down_price ?? '-'} €/MWh</>
            )}
          </span>
          {priceSync.pending_slots > 0 && <span>Waiting for prices: {priceSync.pending_slots} slot(s)</span>}
          <span title="aFRR energy prices are estimated until Volton publishes the aFRR clearing price">
            aFRR: {priceSync.afrr_estimated_slots > 0 ? `${priceSync.afrr_estimated_slots} slot(s) estimated` : 'no estimated slots'}
            {priceSync.afrr_last_check_at && ` · Volton checked ${fmtTime(priceSync.afrr_last_check_at)}`}
            {priceSync.afrr_last_error && <span style={{ color: '#d33' }}> ✗ {priceSync.afrr_last_error}</span>}
          </span>
          {priceSync.last_error && (
            <span style={{ color: '#d33', flexBasis: '100%' }}>
              Error: {priceSync.last_error}
              {priceSync.last_success_at && ` (last successful sync ${fmtTime(priceSync.last_success_at)})`}
            </span>
          )}
        </div>
      )}

      <div style={{ marginBottom: '1rem', padding: '0.6rem 0.9rem', border: '1px solid #8884', borderRadius: 6, fontSize: '0.9rem' }}>
        <button
          type="button"
          onClick={() => setBackfillOpen((o) => !o)}
          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontWeight: 'bold', color: 'inherit', fontSize: 'inherit' }}
        >
          {backfillOpen ? '▾' : '▸'} Backfill from Home Assistant · Import Qilowatt report
          {backfillRunning && ` · running ${backfill.progress}%`}
        </button>
        {backfillOpen && (
          <div style={{ marginTop: '0.6rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
              <label>
                From&nbsp;
                <input
                  type="datetime-local"
                  value={backfillRange.from}
                  onChange={(e) => setBackfillRange({ ...backfillRange, from: e.target.value })}
                  disabled={backfillRunning}
                />
              </label>
              <label>
                To&nbsp;
                <input
                  type="datetime-local"
                  value={backfillRange.to}
                  max={toLocalInput(currentSlotStart())}
                  onChange={(e) => setBackfillRange({ ...backfillRange, to: e.target.value })}
                  disabled={backfillRunning}
                />
              </label>
              <button type="button" onClick={startBackfill} disabled={backfillRunning || !backfillRange.from || !backfillRange.to}>
                {backfillRunning ? 'Backfilling…' : 'Backfill'}
              </button>
            </div>
            <div style={{ opacity: 0.75 }}>
              Replays Home Assistant history through the tracker. Existing rows in the range are recomputed.
              The range is rounded to 15-minute slots and stops at the current slot. Home Assistant keeps
              history for <code>purge_keep_days</code> (10 days by default).
            </div>
            {backfillRunning && (
              <div>
                <progress value={backfill.progress} max={100} style={{ width: '100%', maxWidth: 400 }} />{' '}
                {backfill.phase} · {backfill.progress}%
              </div>
            )}
            {backfill && backfill.state === 'done' && (
              <div style={{ color: 'green' }}>
                ✓ {backfill.message} ({fmtSlot(backfill.from).split('–')[0]} → {fmtSlot(backfill.to).split('–')[0]})
              </div>
            )}
            {backfill && backfill.state === 'error' && <div style={{ color: '#d33' }}>✗ {backfill.message}</div>}
            {backfillError && <div style={{ color: '#d33' }}>✗ {backfillError}</div>}
            <div style={{ borderTop: '1px solid #8884', paddingTop: '0.5rem', marginTop: '0.25rem' }}>
              <label>
                <strong>Import Qilowatt report</strong> (balancing revenue and/or signals CSV)&nbsp;
                <input type="file" accept=".csv,text/csv" multiple onChange={(e) => { importQwReports([...e.target.files]); e.target.value = ''; }} />
              </label>
              <div style={{ opacity: 0.75 }}>
                Official per-slot energy and revenue are shown next to the tracker&apos;s figures (Kratt kWh / Kratt €).
                A signals report also corrects the mFRR/aFRR market of matching rows.
              </div>
              {qwImport && qwImport.map((line) => (
                <div key={line} style={{ color: line.startsWith('✓') ? 'green' : '#d33' }}>{line}</div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div style={{ marginBottom: '1rem' }}>
        <label>Filter:&nbsp;</label>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All (latest)</option>
          <option value="today">Today</option>
          <option value="yesterday">Yesterday</option>
          <option value="this_week">This Week</option>
          <option value="last_week">Last Week</option>
          <option value="this_month">This Month</option>
          <option value="last_month">Last Month</option>
          <option value="custom">Custom Range</option>
        </select>
        {filter === 'custom' && (
          <>
            <input
              type="date"
              value={customRange.from}
              onChange={(e) => setCustomRange({ ...customRange, from: e.target.value })}
              style={{ marginLeft: '1rem' }}
            />
            <input
              type="date"
              value={customRange.to}
              onChange={(e) => setCustomRange({ ...customRange, to: e.target.value })}
              style={{ marginLeft: '0.5rem' }}
            />
          </>
        )}

        <button
          onClick={() => setDarkMode(!darkMode)}
          style={{
            marginLeft: '1rem',
            padding: '0.25rem 0.5rem',
            backgroundColor: darkMode ? '#eee' : '#333',
            color: darkMode ? '#000' : '#fff',
            border: '1px solid #888',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          {darkMode ? 'Light Mode' : 'Dark Mode'}
        </button>
      </div>

      {loading && <div style={{ marginBottom: '1rem' }}>Loading…</div>}

      <h2 style={{ marginTop: '2rem' }}>
        Summary <span style={{ fontSize: '0.8rem', fontWeight: 'normal' }}>({filter.replaceAll('_', ' ')})</span>
      </h2>

      {/* Summary table unchanged */}
      <table style={{ width: '100%', marginTop: '1rem', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '2px solid #ddd' }}>
            <th></th>
            <th>Split</th>
            <th>Count</th>
            <th>Duration</th>
            <th>Energy (kWh)</th>
            <th>Grid (kWh)</th>
            <th>Activation</th>
            <th>NPS</th>
            <th>Net</th>
            <th>Average</th>
            <th>Backup %</th>
            <th>Cancelled %</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>DOWN</strong></td>
            <td data-label="Signal Split">{signalSplit.down}</td>
            <td data-label="Count">{summary.down.count}</td>
            <td data-label="Duration">{formatDuration(summary.down.duration)}</td>
            <td data-label="mFRR (kWh)">{formatVal(summary.down.energy)} kWh</td>
            <td data-label="Grid">{formatVal(summary.down.grid_energy)} kWh</td>
            <td data-label="mFRR" style={{ color: summary.down.profit >= 0 ? 'green' : 'red' }}>{formatVal(summary.down.profit, 2)} €</td>
            <td data-label="NPS" style={{ color: summary.down.grid * -1 >= 0 ? 'green' : 'red' }}>{formatVal(summary.down.grid * -1, 2)} €</td>
            <td data-label="Net" style={{ color: summary.down.net >= 0 ? 'green' : 'red' }}>{formatVal(summary.down.net, 2)} €</td>
            <td data-label="Average" style={{ color: summary.down.net >= 0 ? 'green' : 'red' }}>
              {summary.down.grid_energy ? Math.round(summary.down.net / summary.down.grid_energy * 1000) : '-'} €/MWh
            </td>
            <td data-label="Backup (%)"> {percent(summary.down.backup, summary.down.count)}</td>
            <td data-label="Cancelled (%)"> {percent(summary.down.cancelled, summary.down.count)}</td>
          </tr>
          <tr>
            <td><strong>UP</strong></td>
            <td data-label="Signal Split">{signalSplit.up}</td>
            <td data-label="Count">{summary.up.count}</td>
            <td data-label="Duration">{formatDuration(summary.up.duration)}</td>
            <td data-label="mFRR (kWh)">{formatVal(summary.up.energy)} kWh</td>
            <td data-label="Grid">{formatVal(summary.up.grid_energy)} kWh</td>
            <td data-label="mFRR" style={{ color: summary.up.profit >= 0 ? 'green' : 'red' }}>{formatVal(summary.up.profit, 2)} €</td>
            <td data-label="NPS" style={{ color: summary.up.grid * -1 >= 0 ? 'green' : 'red' }}>{formatVal(summary.up.grid * -1, 2)} €</td>
            <td data-label="Net" style={{ color: summary.up.net >= 0 ? 'green' : 'red' }}>{formatVal(summary.up.net, 2)} €</td>
            <td data-label="Average" style={{ color: summary.up.net >= 0 ? 'green' : 'red' }}>
              {summary.up.energy ? Math.round(summary.up.net / summary.up.energy * 1000) : '-'} €/MWh
            </td>
            <td data-label="Backup (%)"> {percent(summary.up.backup, summary.up.count)}</td>
            <td data-label="Cancelled (%)"> {percent(summary.up.cancelled, summary.up.count)}</td>
          </tr>
          <tr>
            <td><strong>Total</strong></td>
            <td></td>
            <td data-label="Count">{summary.total.count}</td>
            <td data-label="Duration">{formatDuration(summary.total.duration)}</td>
            <td data-label="mFRR (kWh)">{formatVal(summary.total.energy)} kWh</td>
            <td data-label="Grid">{formatVal(summary.total.grid_energy)} kWh</td>
            <td data-label="mFRR" style={{ color: summary.total.profit >= 0 ? 'green' : 'red' }}>{formatVal(summary.total.profit, 2)} €</td>
            <td data-label="NPS" style={{ color: summary.total.grid * -1 >= 0 ? 'green' : 'red' }}>{formatVal(summary.total.grid * -1, 2)} €</td>
            <td data-label="Net" style={{ color: summary.total.net >= 0 ? 'green' : 'red' }}>{formatVal(summary.total.net, 2)} €</td>
            <td></td>
            <td data-label="Backup (%)"> {percent(summary.total.backup, summary.total.count)}</td>
            <td data-label="Cancelled (%)"> {percent(summary.total.cancelled, summary.total.count)}</td>
          </tr>
          {qwReport.length > 0 && (
            <tr style={{ fontStyle: 'italic' }}>
              <td><strong>Kratt report</strong></td>
              <td colSpan={3} style={{ opacity: 0.75 }}>official, imported Qilowatt revenue report</td>
              <td data-label="Energy (kWh)">{formatVal(officialTotal.kwh)} kWh</td>
              <td></td>
              <td data-label="Activation (official)" style={{ color: officialTotal.share >= 0 ? 'green' : 'red' }}>{formatVal(officialTotal.share, 2)} €</td>
              <td colSpan={5}></td>
            </tr>
          )}
        </tbody>
      </table>

      {/* Detail table */}
      <table style={{ width: '100%', marginTop: '1rem', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '2px solid #ddd' }}>
            <th>Date</th>
            <th>Time</th>
            <th>Signal</th>
            <th>Market</th>
            <th>Duration</th>
            <th>Energy (kWh)</th>
            <th>Requested (kWh)</th>
            <th>Delivery</th>
            <th>Grid (kWh)</th>
            <th>NPS €</th>
            <th>Activation €</th>
            <th>Kratt kWh</th>
            <th>Kratt €</th>
            <th>Net</th>
            <th>€/MWh</th>
            <th>Price (€/MWh)</th>
            <th>NPS (€/MWh)</th>
            <th>Baseline (W)</th>
            <th>Start</th>
            <th>End</th>
            <th>Backup</th>
            <th>Cancelled</th>
          </tr>
        </thead>
        <tbody>
          {data.map((entry, idx) => (
            <tr key={entry.id ?? idx}>
              <td data-label="Date">{entry.slot_date}</td>
              <td data-label="Time">{entry.slot_time}</td>
              <td data-label="Signal" style={{ color: entry.signal === 'UP' ? 'green' : 'red', fontWeight: 'bold' }}>
                {entry.signal}
              </td>
              <td data-label="Market" title={entry.id?.endsWith('_r') ? 'mFRR ramp minute, priced with the next quarter' : undefined}>
                {entry.market === 'AFRR' ? 'aFRR' : entry.market === 'MFRR' ? 'mFRR' : '-'}
                {entry.id?.endsWith('_r') ? ' ↗' : ''}
              </td>
              <td data-label="Duration">{entry.duration ?? '-'}</td>
              <td data-label="Energy (kWh)">{entry.energy_kwh?.toFixed(2)}</td>
              <td data-label="Requested (kWh)">{typeof entry.requested_kwh === 'number' ? entry.requested_kwh.toFixed(2) : '-'}</td>
              <td data-label="Delivery">{typeof entry.delivery_pct === 'number' ? `${Math.round(entry.delivery_pct)}%` : '-'}</td>
              <td data-label="Grid (kWh)">{entry.grid_kwh?.toFixed(2)}</td>
              <td data-label="NPS (€)" style={{ color: entry.grid_cost * -1 >= 0 ? 'green' : 'red' }}>{safeFixed(entry.grid_cost * -1, 2)}</td>
              <td data-label="Activation (€)" style={{ color: entry.profit >= 0 ? 'green' : 'red' }}>
                {entry.profit === null || entry.profit === undefined ? '-' : `${entry.profit.toFixed(2)} €`}
              </td>
              {(() => {
                const key = `${entry.timeslot}|${entry.signal}`;
                const o = official.get(key);
                if (!o || officialRowId.get(key) !== entry.id) return (<><td data-label="Kratt kWh">-</td><td data-label="Kratt €">-</td></>);
                return (
                  <>
                    <td data-label="Kratt kWh">{typeof o.kwh === 'number' ? o.kwh.toFixed(2) : '-'}</td>
                    <td data-label="Kratt €" style={{ color: o.share >= 0 ? 'green' : 'red' }}>{o.share.toFixed(2)} €</td>
                  </>
                );
              })()}
              <td data-label="Net (€)" style={{ color: entry.net_total >= 0 ? 'green' : 'red' }}>
                {safeFixed(entry.net_total, 2)}
              </td>
              <td data-label="€/MWh" style={{ color: entry.price_per_kwh >= 0 ? 'green' : 'red' }}>
                {typeof entry.price_per_kwh === 'number'
                  ? `${(entry.price_per_kwh * 1000).toFixed(2)}`
                  : '-'}
              </td>
              <td data-label="Price (€/MWh)" title={entry.price_source === 'estimate' ? 'aFRR price not published yet: estimate (AFRR_PRICE_*_EUR_MWH)' : entry.price_source || undefined}>
                {entry.mffr_price === null || entry.mffr_price === undefined ? '-' : entry.mffr_price}
                {entry.price_source === 'estimate' ? ' est.' : ''}
              </td>
              <td data-label="NPS (€/MWh)">{entry.nordpool_price === null ? '-' : (entry.nordpool_price * 1000).toFixed(2)}</td>
              <td data-label="Baseline (W)">{formatW(entry.baseline_w)}</td>
              <td data-label="Start">
                {new Date(entry.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}
              </td>
              <td data-label="End">
                {(() => {
                  const endDate = new Date(entry.end);
                  if (endDate.getSeconds() > 0 || endDate.getMilliseconds() > 0) {
                    endDate.setMinutes(endDate.getMinutes() + 1);
                  }
                  endDate.setSeconds(0);
                  endDate.setMilliseconds(0);
                  return endDate.toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: false,
                  });
                })()}
              </td>
              <td data-label="Backup">{entry.was_backup === undefined ? '-' : entry.was_backup ? 'Yes' : 'No'}</td>
              <td data-label="Cancelled">{entry.cancelled === undefined ? '-' : entry.cancelled ? 'Yes' : 'No'}</td>
            </tr>
          ))}
        </tbody>
      </table>

    </div>
  );
}

export default App;