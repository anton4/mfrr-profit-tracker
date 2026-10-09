// frontend/src/App.jsx
import { useCallback, useEffect, useMemo, useState } from 'react';
import ConfigPanel from './ConfigPanel';
import LiveView from './LiveView';
import './App.css';

// Relative, so the UI also works under a path prefix (Home Assistant Ingress); vite dev proxies /api
const API_BASE = ".";
// Set at build time from the add-on version (Dockerfile BUILD_VERSION)
const UI_VERSION = import.meta.env.VITE_APP_VERSION || 'dev';

// Column explanations shown by <Hint>
const HINTS = {
  split: 'Share of all activations in this direction. Ramp minutes (↗) are not counted as separate activations.',
  count: 'Number of activations: one per 15-minute slot, market and direction. Ramp minutes (↗) are not counted.',
  duration: 'Total time Kratt commanded this direction.',
  energy: 'Regulated energy, measured at the grid connection against the baseline (the grid power just before the signal). DOWN = extra import or less export, UP = extra export or less import. This is the energy Kratt settles.',
  requested: 'Energy Kratt asked for: the requested power (qw_powerlimit) × the time it was requested.',
  delivery: 'Regulated energy ÷ requested energy. 100% means the battery followed the command fully; above 100% is overshoot.',
  activation: 'Your share of Kratt\'s activation revenue after Kratt\'s fee (20% by default). UP: (market price − NPS) × energy; DOWN: (NPS − market price) × energy.',
  net: 'Activation revenue plus the change in your electricity bill compared with staying at the baseline. With Fees on, network and seller fees are included; otherwise import is spot + VAT and export is spot.',
  effPrice: 'Effective price per kWh, including Kratt\'s payment and the energy bill effect (and fees when Fees is on). DOWN: what each kWh you charged actually cost you (negative = you were paid to charge). UP: what each kWh you delivered actually earned you. Compare with the average spot price of the same slots.',
  backup: 'Share of activations that started 15 s or more after their slot began (joined mid-slot).',
  cancelled: 'Share of activations that ended before their slot did. For mFRR the slot\'s last minute belongs to the next activation.',
  report: 'Official totals from the imported Qilowatt (KratTrade) revenue report for this period.',
  slot: 'Start of the 15-minute settlement period, local time.',
  direction: 'DOWN: Kratt asked you to consume more (charge, import). UP: deliver more (discharge, export).',
  market: 'mFRR: scheduled reserve, starts one minute before a quarter. aFRR: automatic reserve, updated about every minute. ↗ = the minute before an mFRR quarter, priced with the next quarter.',
  minutes: 'Minutes this direction was commanded within the slot.',
  kratt: 'Official regulated energy (kWh) and your share (€) for this slot and direction, from the imported Qilowatt revenue report.',
  price: 'Balancing energy price used for income in this direction: mFRR from the Baltic Transparency Dashboard. aFRR uses Volton when published, otherwise an estimate ("est.") calibrated on Kratt reports. "market" = the real aFRR market price (CBMP) during this activation, for comparison.',
  cbmp: 'Real Estonian aFRR cross-border marginal price (CBMP): the average of the 4-second prices published while this activation ran (from ENTSO-E, PICASSO). Kratt has paid 1.3–3× this in its reports, so income keeps using the estimate or the imported report.',
  grid: 'Net grid energy during the activation: + import, − export.',
  energyEur: 'Change in your electricity bill at spot price (plus VAT on import) compared with staying at the baseline: extra import costs, extra export earns, avoided import saves.',
  feesEur: 'What network and seller fees add (−) or save (+) for this activation on top of Energy €: network tariff, renewable energy, excise, balancing and security of supply fees, seller margin (with VAT) and export fees. Counted in Net only when Fees is on.',
  totals: 'Sums for all activations in the selected period. Delivery = total regulated ÷ total requested energy.',
  bill: 'Change in your electricity bill compared with what the baseline would have imported or exported in the same time.',
  rate: 'Network tariff period of the slot: day, night/weekend/holiday, or Võrk 5 winter peak.',
  nps: 'Nord Pool day-ahead (spot) price for the slot.',
  baseline: 'Grid power just before the Kratt signal, locked for the whole run. Energy is measured against it.',
  span: 'First and last moment the tracker saw this activation in the slot.',
  perMwh: 'Net result per MWh of regulated energy for this row.',
  backupRow: 'Started 15 s or more after the slot began.',
  cancelledRow: 'Ended before the slot did.',
};

// Label with an ⓘ button: explanation on hover, keyboard focus or tap
function Hint({ label, hint, align = 'left' }) {
  return (
    <span className="hint">
      <span>{label}</span>
      <button type="button" className="hint-btn" aria-label={`${label}: ${hint}`}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9.5" /><path d="M12 11v6M12 7.5v.01" />
        </svg>
      </button>
      <span className={`hint-pop hint-${align}`} role="tooltip">{hint}</span>
    </span>
  );
}

function App() {
  const [darkMode, setDarkModeState] = useState(() => {
    try {
      const saved = localStorage.getItem('theme');
      if (saved) return saved === 'dark';
    } catch { /* storage unavailable */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });
  const setDarkMode = (dark) => {
    setDarkModeState(dark);
    try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch { /* storage unavailable */ }
  };
  const [data, setData] = useState([]);
  // Include seller and network fees in bill effect / net (remembered per browser)
  const [feesOn, setFeesOnState] = useState(() => {
    const param = new URLSearchParams(window.location.search).get('fees');
    if (param === 'on' || param === 'off') return param === 'on';
    try { return localStorage.getItem('fees') === 'on'; } catch { return false; }
  });
  const setFeesOn = (on) => {
    setFeesOnState(on);
    try { localStorage.setItem('fees', on ? 'on' : 'off'); } catch { /* storage unavailable */ }
  };
  const [toolsOpen, setToolsOpen] = useState(false);
  useEffect(() => {
    if (!toolsOpen) return undefined;
    // Escape in the sensor search only dismisses its suggestions
    const onKey = (e) => { if (e.key === 'Escape' && !e.target.list) setToolsOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toolsOpen]);
  // Configured sensors that Home Assistant doesn't have, and HA's sensors for the search
  const [sensorStatus, setSensorStatus] = useState(null);   // { values, problems, entities, ha_error }
  // The add-on options, edited as a form or YAML (Data tools → Configuration)
  const [configOpen, setConfigOpen] = useState(false);
  // Period: from ?range=… (shareable links), else today
  const [filter, setFilterState] = useState(() => {
    const range = new URLSearchParams(window.location.search).get('range');
    return ['today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month', 'all', 'custom'].includes(range) ? range : 'today';
  });
  const setFilter = (value) => {
    setFilterState(value);
    const url = new URL(window.location.href);
    url.searchParams.set('range', value);
    window.history.replaceState(null, '', url);
  };
  const [customRange, setCustomRange] = useState({ from: '', to: '' });
  const [loading, setLoading] = useState(false);
  const [priceSync, setPriceSync] = useState(null);
  // Installed version: differs from UI_VERSION when the browser kept a page cached from an older one
  const [installed, setInstalled] = useState(null);   // { version, started_at }
  useEffect(() => {
    fetch(`${API_BASE}/api/version`)
      .then((res) => (res.ok ? res.json() : null))
      .then((v) => { if (v) setInstalled(v); })
      .catch((e) => console.error('Version fetch failed', e));
  }, []);
  const installedVersion = installed?.version;
  const staleUi = installedVersion && installedVersion !== UI_VERSION && installedVersion !== 'dev' && UI_VERSION !== 'dev';
  // A new ?v= makes the browser fetch the page instead of using its cached copy
  const reloadUi = (version = installedVersion) => {
    const url = new URL(window.location.href);
    url.searchParams.set('v', version);
    window.location.replace(url);
  };
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

  const loadSensorStatus = useCallback(() => fetch(`${API_BASE}/api/sensors`)
    .then((res) => (res.ok ? res.json() : null))
    .then((st) => { if (st) setSensorStatus(st); return st; })
    .catch((e) => console.error('Sensor status fetch failed', e)), []);
  // On page load; a sensor problem opens the Configuration section in Data tools
  useEffect(() => {
    loadSensorStatus().then((st) => { if (st?.problems.length) setConfigOpen(true); });
  }, [loadSensorStatus]);
  // Fresh sensor states for the Configuration section whenever it's shown
  const openTools = (withConfig = false) => {
    setToolsOpen(true);
    if (withConfig) setConfigOpen(true);
    if (withConfig || configOpen) loadSensorStatus();
  };
  const toggleConfig = () => {
    if (!configOpen) loadSensorStatus();
    setConfigOpen(!configOpen);
  };
  const sensorProblemText = (p) => (p.error === 'not set'
    ? `${p.option} not set`
    : `${p.option}: ${p.entity_id} not found in Home Assistant`);

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
    return sec < 120 ? `(in ${sec} s)` : `(in ${Math.round(sec / 60)} min)`;
  };
  // One status chip per price source, with the same states everywhere:
  // red = the last attempt failed, amber = slots waiting, green = nothing waiting, gray = not set up
  const SYNC_STATES = {
    error: ['dot-neg', 'error'], waiting: ['dot-warn', 'waiting'], ok: ['dot-pos', 'up to date'], off: ['dot-off', 'off'],
  };
  const syncChip = ({ id, title, hint, source, lastAt, nextAt, waiting, noun, error, lastSuccessAt, off }) => {
    const state = off ? 'off' : error ? 'error' : waiting > 0 ? 'waiting' : 'ok';
    const [dot, word] = SYNC_STATES[state];
    const parts = [word, source];
    if (off) parts.push(off);
    else {
      parts.push(lastAt ? `checked ${hm(lastAt)} ${fmtAgo(lastAt)}` : state === 'ok' ? 'nothing to price yet' : 'not checked yet');
      parts.push(nextAt ? `next ${hm(nextAt)} ${fmtIn(nextAt)}` : 'next: not needed');
    }
    if (waiting > 0) parts.push(`${waiting} ${noun} waiting`);
    return (
      <div key={id} className={`chip ${state === 'error' ? 'chip-error' : ''}`}
        title={`${hint} Green: nothing waiting · amber: waiting for prices · red: the last check failed · gray: not set up.`}>
        <span className={`dot ${dot}`} />
        <strong>{title}</strong>
        <span className="muted">{parts.join(' · ')}</span>
        {error && <span className="err">· failed: {error}{lastSuccessAt ? ` (last success ${hm(lastSuccessAt)})` : ''}</span>}
      </div>
    );
  };


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
      up:  { energy: 0, grid_energy: 0, profit: 0, duration: 0, count: 0, backup: 0, cancelled: 0, grid: 0, kratt: 0, ffr: 0, net: 0, priceSum: 0, priceCount: 0, billSpot: 0, fees: 0, requested: 0, netOff: 0, netOn: 0, netEnergy: 0, spotSum: 0, spotEnergy: 0 },
      down:{ energy: 0, grid_energy: 0, profit: 0, duration: 0, count: 0, backup: 0, cancelled: 0, grid: 0, kratt: 0, ffr: 0, net: 0, priceSum: 0, priceCount: 0, billSpot: 0, fees: 0, requested: 0, netOff: 0, netOn: 0, netEnergy: 0, spotSum: 0, spotEnergy: 0 },
      total:{ energy: 0, grid_energy: 0, profit: 0, duration: 0, count: 0, backup: 0, cancelled: 0, grid: 0, kratt: 0, ffr: 0, net: 0, priceSum: 0, priceCount: 0, billSpot: 0, fees: 0, requested: 0, netOff: 0, netOn: 0, netEnergy: 0, spotSum: 0, spotEnergy: 0 },
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

      const gridCost = (feesOn ? entry.grid_cost_fees : entry.grid_cost) || 0;
      const billSpot = -(entry.grid_cost || 0);      // energy at spot (+ VAT on import)
      const feesEur = entry.fees_eur || 0;            // what network and seller fees add/save
      const requested = entry.requested_kwh || 0;
      const hasNet = typeof (feesOn ? entry.net_total_fees : entry.net_total) === 'number';
      const nps = typeof entry.nordpool_price === 'number' ? entry.nordpool_price : null;
      const krattFee = entry.kratt_fee || 0;
      const ffrIncome = entry.ffr_income || 0;
      const netTotal = (feesOn ? entry.net_total_fees : entry.net_total) || 0;
      const ppk = feesOn ? entry.price_per_kwh_fees : entry.price_per_kwh;
      const pricePerKwh = typeof ppk === 'number' ? ppk : null;

      // Totals
      acc.total.energy += energy;
      acc.total.grid_energy += gridEnergy;
      acc.total.profit += profit;
      acc.total.duration += duration;
      acc.total.backup += isBackup ? 1 : 0;
      acc.total.cancelled += isCancelled ? 1 : 0;
      acc.total.grid += gridCost;
      acc.total.netOff += entry.net_total || 0;        // spot + VAT
      acc.total.netOn += entry.net_total_fees || 0;    // with network & seller fees
      acc.total.billSpot += billSpot;
      acc.total.fees += feesEur;
      acc.total.requested += requested;
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
        bucket.billSpot += billSpot;
        bucket.fees += feesEur;
        bucket.requested += requested;
        if (hasNet) bucket.netEnergy += energy;
        if (nps !== null) { bucket.spotSum += nps * energy; bucket.spotEnergy += energy; }
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
  }, [data, feesOn]);

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

  const fmtNum = (v, digits = 2) => (typeof v === 'number' ? v.toFixed(digits) : '–');
  const fmtEur = (v, digits = 2) => {
    if (typeof v !== 'number') return '–';
    const r = Number(v.toFixed(digits));
    return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(digits)} €`;
  };
  const signClass = (v) => {
    const r = typeof v === 'number' ? Number(v.toFixed(2)) : 0;
    return r > 0 ? 'pos' : r < 0 ? 'neg' : 'zero';
  };
  const percent = (count, total) => (total ? `${Math.round((count / total) * 100)}%` : '–');
  const formatDuration = (minutes) => {
    if (!minutes) return '–';
    const h = Math.floor(minutes / 60);
    const m = String(minutes % 60).padStart(2, '0');
    return h ? `${h} h ${m} m` : `${minutes % 60} m`;
  };
  const hm = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '–');
  const marketLabel = (m) => (m === 'AFRR' ? 'aFRR' : m === 'MFRR' ? 'mFRR' : '–');
  const isRamp = (e) => Boolean(e.id?.endsWith('_r'));
  const netOf = (e) => (feesOn ? e.net_total_fees : e.net_total);
  const energyEurOf = (e) => (typeof e.grid_cost === 'number' ? -e.grid_cost : undefined);

  // Delivery over all rows with a request: delivered / requested energy
  const delivery = useMemo(() => {
    let e = 0;
    let r = 0;
    for (const row of data) {
      if ((row.requested_kwh || 0) > 0) { e += row.energy_kwh || 0; r += row.requested_kwh; }
    }
    return r > 0 ? Math.round((e / r) * 100) : null;
  }, [data]);

  const [openRow, setOpenRow] = useState(null);
  // An activation's "show in graph" button opens the graph around it
  const [graphFocus, setGraphFocus] = useState(null);
  const showInGraph = (entry) => setGraphFocus((f) => ({ start: entry.start, end: entry.end, n: (f?.n ?? 0) + 1 }));
  const graphButton = (entry) => (
    <button type="button" className="rowbtn" aria-label="Show in graph" title="Show in graph" onClick={() => showInGraph(entry)}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 3v18h18" /><path d="m7 15 4-4 3 3 5-6" /></svg>
    </button>
  );
  const officialFor = (entry) => {
    const key = `${entry.timeslot}|${entry.signal}`;
    return officialRowId.get(key) === entry.id ? official.get(key) : undefined;
  };
  const kratt = (entry) => {
    const o = officialFor(entry);
    return o ? `${fmtNum(o.kwh)} · ${o.share.toFixed(2)} €` : '–';
  };

  const ranges = [
    ['today', 'Today'], ['yesterday', 'Yesterday'], ['this_week', 'This week'], ['last_week', 'Last week'],
    ['this_month', 'This month'], ['last_month', 'Last month'], ['all', 'All'], ['custom', 'Custom'],
  ];
  const rangeLabel = Object.fromEntries(ranges)[filter] ?? '';
  const hasReport = qwReport.length > 0;
  const signalSplit = {
    up: percent(summary.up.count, summary.total.count),
    down: percent(summary.down.count, summary.total.count),
  };
  const energySplit = summary.total.energy ? (summary.down.energy / summary.total.energy) * 100 : 50;
  // Effective €/kWh: DOWN = what a kWh charged cost you, UP = what a kWh delivered earned you
  // (Kratt payment + energy bill effect, + fees when on). Negative DOWN = you were paid.
  const effPrice = (dir, b) => (b.netEnergy ? (dir === 'DOWN' ? -b.net : b.net) / b.netEnergy : null);
  const spotAvg = (b) => (b.spotEnergy ? b.spotSum / b.spotEnergy : null);
  const fmtKwh = (v) => (typeof v === 'number' ? `${v < 0 ? '−' : ''}${Math.abs(v).toFixed(3)}` : '–');
  const directionRows = [
    ['DOWN', summary.down, signalSplit.down],
    ['UP', summary.up, signalSplit.up],
  ];

  return (
    <div className={darkMode ? 'app dark' : 'app'}>
      <div className="page">
        <header className="topbar">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
            </div>
            <div>
              <h1>mFRR Profit Tracker</h1>
              <div className="muted small">Kratt · Estonia · grid-side metering · v{UI_VERSION}</div>
            </div>
          </div>
          <div className="toolbar">
            <div className="seg" role="group" aria-label="Period">
              {ranges.map(([value, label]) => (
                <button key={value} type="button" className={filter === value ? 'on' : ''} aria-pressed={filter === value} onClick={() => setFilter(value)}>
                  {label}
                </button>
              ))}
            </div>
            <button type="button" className={`toolsbtn ${backfillRunning ? 'busy' : ''}`} aria-haspopup="dialog" onClick={() => openTools()}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></svg>
              <span>Data tools</span>
              {backfillRunning && <span className="num small">{backfill.progress}%</span>}
            </button>
            <button type="button" className={`switch ${feesOn ? 'on' : ''}`} role="switch" aria-checked={feesOn}
              title="Include seller and network fees in the bill effect and net result" onClick={() => setFeesOn(!feesOn)}>
              <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
              Fees
            </button>
            <button type="button" className="iconbtn" aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'} onClick={() => setDarkMode(!darkMode)}>
              {darkMode ? (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
              )}
            </button>
          </div>
        </header>

        {filter === 'custom' && (
          <div className="custom-range">
            <label className="field">From<input type="date" value={customRange.from} onChange={(e) => setCustomRange({ ...customRange, from: e.target.value })} /></label>
            <label className="field">To<input type="date" value={customRange.to} onChange={(e) => setCustomRange({ ...customRange, to: e.target.value })} /></label>
          </div>
        )}

        {staleUi && (
          <div className="chips">
            <button type="button" className="chip chip-warn chip-btn" onClick={reloadUi}>
              <span className="dot dot-warn" />
              <strong>Version {installedVersion} is installed</strong>
              <span>this page is v{UI_VERSION} · reload</span>
            </button>
          </div>
        )}

        {(sensorStatus?.problems.length > 0 || sensorStatus?.ha_error) && (
          <div className="chips">
            <button type="button" className="chip chip-error chip-btn" onClick={() => openTools(true)}>
              <span className="dot dot-neg" />
              <strong>Sensors</strong>
              <span>
                {sensorStatus.ha_error
                  ? sensorStatus.ha_error
                  : `${sensorProblemText(sensorStatus.problems[0])}${sensorStatus.problems.length > 1 ? ` + ${sensorStatus.problems.length - 1} more` : ''}`}
                {' · '}fix in Data tools → Configuration
              </span>
            </button>
          </div>
        )}

        {priceSync && (
          <div className="chips">
            {syncChip({
              id: 'mfrr', title: 'mFRR prices', source: priceSync.source,
              hint: 'mFRR energy prices from the Baltic Transparency Dashboard, fetched only while a finished mFRR slot is missing its price.',
              lastAt: priceSync.last_sync_at, nextAt: priceSync.next_sync_at, waiting: priceSync.pending_slots, noun: 'slot(s)',
              error: priceSync.last_error, lastSuccessAt: priceSync.last_success_at,
            })}
            {priceSync.latest_price_slot && (
              <div className="chip">
                <strong>Latest price</strong>
                <span className="muted num">
                  {fmtSlot(priceSync.latest_price_slot)} · UP {priceSync.latest_up_price ?? '–'} · DOWN {priceSync.latest_down_price ?? '–'} €/MWh
                </span>
              </div>
            )}
            {syncChip({
              id: 'afrr', title: 'aFRR prices', source: 'Volton clearing price',
              hint: 'aFRR income uses an estimate until Volton publishes the clearing price; checked hourly while slots are on the estimate (up to 3 days).',
              lastAt: priceSync.afrr_last_check_at, nextAt: priceSync.afrr_next_check_at,
              waiting: priceSync.afrr_estimated_slots, noun: 'slot(s) on the estimate',
              error: priceSync.afrr_last_error, lastSuccessAt: priceSync.afrr_last_success_at,
            })}
            {syncChip({
              id: 'cbmp', title: 'aFRR market price', source: 'ENTSO-E (CBMP)',
              hint: 'The aFRR cross-border marginal price from ENTSO-E, shown for comparison only: income uses the estimate, Volton or a Qilowatt report.',
              lastAt: priceSync.cbmp_last_check_at, nextAt: priceSync.cbmp_next_check_at,
              waiting: priceSync.cbmp_pending_slots, noun: 'slot(s)',
              error: priceSync.cbmp_last_error, lastSuccessAt: priceSync.cbmp_last_success_at,
              off: !priceSync.cbmp_configured && 'set entsoe_token in Data tools → Configuration',
            })}
          </div>
        )}

        <LiveView apiBase={API_BASE} focus={graphFocus} />

        <div className="kpis">
          <div className="card kpi">
            <div className="muted small">Net result {feesOn ? '· incl. network & seller fees' : '· spot + VAT'}</div>
            <div className={`kpi-value num ${signClass(summary.total.net)}`}>{fmtEur(summary.total.net)}</div>
            <div className="small kpi-compare">
              {feesOn ? 'Without fees' : 'With fees'}{' '}
              <span className={`num ${signClass(feesOn ? summary.total.netOff : summary.total.netOn)}`}>{fmtEur(feesOn ? summary.total.netOff : summary.total.netOn)}</span>
              <span className="muted"> · fees {fmtEur(summary.total.netOn - summary.total.netOff)}</span>
            </div>
            <div className="muted small">
              Activation <span className="num">{fmtEur(summary.total.profit)}</span> · Energy <span className="num">{fmtEur(summary.total.billSpot)}</span>
              {' · '}Fees <span className={`num ${feesOn ? '' : 'excluded'}`}>{fmtEur(summary.total.fees)}</span>{!feesOn && ' (not included)'}
            </div>
          </div>
          <div className="card kpi">
            <div className="muted small">{hasReport ? 'Activation share · ours vs Kratt' : 'Activation share'}</div>
            <div className="kpi-row">
              <span className="kpi-value num">{fmtNum(summary.total.profit)} €</span>
              {hasReport && <span className="kpi-sub num muted">/ {fmtNum(officialTotal.share)} €</span>}
            </div>
            <div className="muted small">
              {hasReport ? (
                <>
                  <span className="num text">{fmtEur(summary.total.profit - officialTotal.share)}</span>
                  {officialTotal.share ? ` (${(((summary.total.profit - officialTotal.share) / officialTotal.share) * 100).toFixed(1)}%)` : ''} vs imported Qilowatt report
                </>
              ) : 'Import a Qilowatt report to compare'}
            </div>
          </div>
          <div className="card kpi">
            <div className="muted small">Regulated energy</div>
            <div className="kpi-value num">{fmtNum(summary.total.energy)} kWh</div>
            <div className="splitbar" aria-hidden="true">
              <div style={{ width: `${energySplit}%`, background: 'var(--down)' }} />
              <div style={{ width: `${100 - energySplit}%`, background: 'var(--up)' }} />
            </div>
            <div className="split-legend small">
              <span className="down-text">DOWN <span className="num">{fmtNum(summary.down.energy)}</span></span>
              <span className="up-text">UP <span className="num">{fmtNum(summary.up.energy)}</span></span>
            </div>
          </div>
          <div className="card kpi">
            <div className="muted small">Activations</div>
            <div className="kpi-value num">{summary.total.count}</div>
            <div className="muted small">
              <span className="num">{formatDuration(summary.total.duration)}</span> in total
              {delivery !== null && <> · avg delivery <span className="num text">{delivery}%</span></>}
            </div>
          </div>
          <div className="card kpi">
            <div className="muted small"><Hint label="Average price · €/kWh" hint={HINTS.effPrice} align="right" /></div>
            {[['DOWN', summary.down, 'charged'], ['UP', summary.up, 'delivered']].map(([dir, b, verb]) => {
              const p = effPrice(dir, b);
              const good = p !== null && (dir === 'DOWN' ? p <= 0 : p >= 0);
              const word = p === null ? '' : dir === 'DOWN' ? (p <= 0 ? 'paid to you' : 'cost') : (p >= 0 ? 'earned' : 'lost');
              return (
                <div className="price-row" key={dir}>
                  <span className={`pill pill-${dir}`}>{dir}</span>
                  <span className={`num price-value ${p === null ? 'zero' : good ? 'pos' : 'neg'}`}>{p === null ? '–' : Math.abs(p).toFixed(3)}</span>
                  <span className="muted small">{p === null ? '' : `${word} per kWh ${verb}`}<br />spot avg <span className="num">{fmtKwh(spotAvg(b))}</span></span>
                </div>
              );
            })}
            <div className="muted small">{feesOn ? 'incl. network & seller fees' : 'spot + VAT, without fees'}</div>
          </div>
        </div>

          <section className="card section direction">
            <div className="section-head">
              <h2>By direction</h2>
              <span className="muted small">{rangeLabel}</span>
            </div>
            <div className="scroll-x">
              <div className="dir-grid">
                <div className="th" />
                <div className="th r"><Hint label="Count · split" hint={`${HINTS.count} ${HINTS.split}`} /></div>
                <div className="th r"><Hint label="Duration" hint={HINTS.duration} /></div>
                <div className="th r"><Hint label="Energy" hint={HINTS.energy} /></div>
                <div className="th r"><Hint label="Activation" hint={HINTS.activation} align="right" /></div>
                <div className="th r"><Hint label="Energy €" hint={HINTS.energyEur} align="right" /></div>
                <div className="th r"><Hint label="Fees" hint={HINTS.feesEur} align="right" /></div>
                <div className="th r"><Hint label="Net" hint={HINTS.net} align="right" /></div>
                <div className="th r"><Hint label="€/kWh" hint={HINTS.effPrice} align="right" /></div>
                <div className="th r"><Hint label="Backup · cancelled" hint={`Backup: ${HINTS.backup} Cancelled: ${HINTS.cancelled}`} align="right" /></div>
                {directionRows.map(([dir, b, split]) => (
                  <div className="dir-row" key={dir}>
                    <div><span className={`pill pill-${dir}`}>{dir}</span></div>
                    <div className="num r">{b.count}<span className="muted">&nbsp;·&nbsp;{split}</span></div>
                    <div className="num r">{formatDuration(b.duration)}</div>
                    <div className="num r">{fmtNum(b.energy)} kWh</div>
                    <div className={`num r ${signClass(b.profit)}`}>{fmtEur(b.profit)}</div>
                    <div className={`num r ${signClass(b.billSpot)}`}>{fmtEur(b.billSpot)}</div>
                    <div className={`num r ${feesOn ? signClass(b.fees) : 'excluded'}`}>{fmtEur(b.fees)}</div>
                    <div className={`num r ${signClass(b.net)}`}>{fmtEur(b.net)}</div>
                    <div className={`num r ${signClass(b.net)}`} title={dir === 'DOWN' ? 'Cost per kWh charged (negative = paid to you)' : 'Earned per kWh delivered'}>{fmtKwh(effPrice(dir, b))}</div>
                    <div className="num r muted">{percent(b.backup, b.count)} · {percent(b.cancelled, b.count)}</div>
                  </div>
                ))}
                <div className="dir-row total">
                  <div>Total</div>
                  <div className="num r">{summary.total.count}</div>
                  <div className="num r">{formatDuration(summary.total.duration)}</div>
                  <div className="num r">{fmtNum(summary.total.energy)} kWh</div>
                  <div className={`num r ${signClass(summary.total.profit)}`}>{fmtEur(summary.total.profit)}</div>
                  <div className={`num r ${signClass(summary.total.billSpot)}`}>{fmtEur(summary.total.billSpot)}</div>
                  <div className={`num r ${feesOn ? signClass(summary.total.fees) : 'excluded'}`}>{fmtEur(summary.total.fees)}</div>
                  <div className={`num r ${signClass(summary.total.net)}`}>{fmtEur(summary.total.net)}</div>
                  <div className="num r muted">–</div>
                  <div className="num r muted">{percent(summary.total.backup, summary.total.count)} · {percent(summary.total.cancelled, summary.total.count)}</div>
                </div>
                {hasReport && (
                  <div className="dir-row report">
                    <div><Hint label="Kratt report" hint={HINTS.report} /></div>
                    <div /><div />
                    <div className="num r">{fmtNum(officialTotal.kwh)} kWh</div>
                    <div className="num r">{fmtNum(officialTotal.share)} €</div>
                    <div /><div /><div /><div /><div />
                  </div>
                )}
              </div>
            </div>
          </section>


        <section className="card activations">
          <div className="section-head padded">
            <h2>Activations {loading && <span className="muted small">· loading…</span>}</h2>
            <div className="legend small muted">
              <span><span className="badge">↗</span> mFRR ramp minute, priced with the next quarter</span>
              <span><span className="badge badge-aFRR">est.</span> estimated aFRR price</span>
            </div>
          </div>

          {data.length === 0 && !loading && <div className="empty muted">No activations in this period.</div>}

          {data.length > 0 && (
            <>
              <div className="scroll-x act-table">
                <div className="act-inner">
                  <div className="act-grid act-head">
                    <div><Hint label="Slot" hint={HINTS.slot} /></div>
                    <div><Hint label="Direction" hint={HINTS.direction} /></div>
                    <div><Hint label="Market" hint={HINTS.market} /></div>
                    <div className="r"><Hint label="Min" hint={HINTS.minutes} /></div>
                    <div className="r"><Hint label="Energy" hint={HINTS.energy} /></div>
                    <div className="r"><Hint label="Requested" hint={HINTS.requested} /></div>
                    <div className="pl"><Hint label="Delivery" hint={HINTS.delivery} /></div>
                    <div className="r"><Hint label="Activation" hint={HINTS.activation} align="right" /></div>
                    <div className="r"><Hint label="Energy €" hint={HINTS.energyEur} align="right" /></div>
                    <div className="r"><Hint label="Fees" hint={HINTS.feesEur} align="right" /></div>
                    <div className="r"><Hint label="Net" hint={HINTS.net} align="right" /></div>
                    <div className="r"><Hint label="Kratt kWh · €" hint={HINTS.kratt} align="right" /></div>
                    <div className="r"><Hint label="Price €/MWh" hint={HINTS.price} align="right" /></div>
                    <div />
                  </div>
                  {data.map((entry, idx) => {
                    const id = entry.id ?? String(idx);
                    const open = openRow === id;
                    const pct = typeof entry.delivery_pct === 'number' ? Math.round(entry.delivery_pct) : null;
                    return (
                      <div className="act-item" key={id}>
                        <div className="act-grid act-row">
                          <div className="slot"><span className="num">{entry.slot_date}</span><span className="num muted">{entry.slot_time}</span></div>
                          <div><span className={`pill pill-${entry.signal}`}>{entry.signal}</span></div>
                          <div className="badges">
                            <span className={`badge badge-${marketLabel(entry.market)}`}>{marketLabel(entry.market)}</span>
                            {isRamp(entry) && <span className="badge" title="mFRR ramp minute, priced with the next quarter">↗</span>}
                          </div>
                          <div className="num r">{entry.duration ?? '–'}</div>
                          <div className="num r">{fmtNum(entry.energy_kwh)}</div>
                          <div className="num r muted">{fmtNum(entry.requested_kwh)}</div>
                          <div className="delivery pl">
                            <div className="meter"><div style={{ width: `${Math.min(pct ?? 0, 100)}%` }} /></div>
                            <span className="num small">{pct === null ? '–' : `${pct}%`}</span>
                          </div>
                          <div className={`num r ${signClass(entry.profit)}`}>{fmtEur(entry.profit)}</div>
                          <div className={`num r ${signClass(energyEurOf(entry))}`}>{fmtEur(energyEurOf(entry))}</div>
                          <div className={`num r ${feesOn ? signClass(entry.fees_eur) : 'excluded'}`}>{fmtEur(entry.fees_eur)}</div>
                          <div className={`num r strong ${signClass(netOf(entry))}`}>{fmtEur(netOf(entry))}</div>
                          <div className="num r muted">{kratt(entry)}</div>
                          <div className="num r" title={entry.price_source === 'estimate' ? 'aFRR price not published yet: estimate' : entry.price_source || undefined}>
                            <span className="price-stack">
                              <span>
                                {entry.mffr_price ?? '–'}
                                {entry.price_source === 'estimate' && <span className="badge badge-aFRR est">est.</span>}
                              </span>
                              {entry.market === 'AFRR' && typeof entry.cbmp_avg === 'number' && (
                                <span className="small muted" title={HINTS.cbmp}>market {Math.round(entry.cbmp_avg)}</span>
                              )}
                            </span>
                          </div>
                          <div className="r row-actions">
                            {graphButton(entry)}
                            <button type="button" className="rowbtn" aria-expanded={open} aria-label="Show details" onClick={() => setOpenRow(open ? null : id)}>
                              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : undefined }}><path d="m6 9 6 6 6-6" /></svg>
                            </button>
                          </div>
                        </div>
                        {open && <RowDetails entry={entry} feesOn={feesOn} hm={hm} fmtNum={fmtNum} fmtEur={fmtEur} />}
                      </div>
                    );
                  })}
                  <div className="act-grid act-total">
                    <div><Hint label="Total" hint={HINTS.totals} /></div>
                    <div className="muted small">{summary.total.count} activations</div>
                    <div />
                    <div className="num r">{summary.total.duration || '–'}</div>
                    <div className="num r">{fmtNum(summary.total.energy)}</div>
                    <div className="num r muted">{fmtNum(summary.total.requested)}</div>
                    <div className="delivery pl">
                      <div className="meter"><div style={{ width: `${Math.min(delivery ?? 0, 100)}%` }} /></div>
                      <span className="num small">{delivery === null ? '–' : `${delivery}%`}</span>
                    </div>
                    <div className={`num r ${signClass(summary.total.profit)}`}>{fmtEur(summary.total.profit)}</div>
                    <div className={`num r ${signClass(summary.total.billSpot)}`}>{fmtEur(summary.total.billSpot)}</div>
                    <div className={`num r ${feesOn ? signClass(summary.total.fees) : 'excluded'}`}>{fmtEur(summary.total.fees)}</div>
                    <div className={`num r ${signClass(summary.total.net)}`}>{fmtEur(summary.total.net)}</div>
                    <div className="num r muted">{hasReport ? `${fmtNum(officialTotal.kwh)} · ${officialTotal.share.toFixed(2)} €` : '–'}</div>
                    <div /><div />
                  </div>
                </div>
              </div>

              <div className="act-cards">
                <div className="act-card act-card-total">
                  <div className="act-card-head"><Hint label="Period total" hint={HINTS.totals} /><span className={`num strong ${signClass(summary.total.net)}`}>{fmtEur(summary.total.net)}</span></div>
                  <div className="act-card-money small muted">
                    <span>Activation <span className="num text">{fmtEur(summary.total.profit)}</span></span>
                    <span>Energy <span className="num text">{fmtEur(summary.total.billSpot)}</span></span>
                    <span>Fees <span className={`num ${feesOn ? 'text' : 'excluded'}`}>{fmtEur(summary.total.fees)}</span></span>
                  </div>
                </div>
                {data.map((entry, idx) => {
                  const id = entry.id ?? String(idx);
                  const open = openRow === id;
                  return (
                    <div className="act-card" key={id}>
                      <div className="act-card-head">
                        <div className="badges">
                          <span className={`pill pill-${entry.signal}`}>{entry.signal}</span>
                          <span className={`badge badge-${marketLabel(entry.market)}`}>{marketLabel(entry.market)}</span>
                          {isRamp(entry) && <span className="badge">↗</span>}
                        </div>
                        <span className="num muted small">{entry.slot_date} {entry.slot_time}</span>
                      </div>
                      <div className="act-card-grid">
                        <div><div className="muted small"><Hint label="Energy" hint={HINTS.energy} /></div><div className="num">{fmtNum(entry.energy_kwh)} kWh</div></div>
                        <div><div className="muted small"><Hint label="Delivery" hint={HINTS.delivery} /></div><div className="num">{typeof entry.delivery_pct === 'number' ? `${Math.round(entry.delivery_pct)}%` : '–'}</div></div>
                        <div className="r"><div className="muted small"><Hint label="Net" hint={HINTS.net} align="right" /></div><div className={`num strong ${signClass(netOf(entry))}`}>{fmtEur(netOf(entry))}</div></div>
                      </div>
                      <div className="act-card-money small muted">
                        <span>Activation <span className="num text">{fmtEur(entry.profit)}</span></span>
                        <span>Energy <span className="num text">{fmtEur(energyEurOf(entry))}</span></span>
                        <span>Fees <span className={`num ${feesOn ? 'text' : 'excluded'}`}>{fmtEur(entry.fees_eur)}</span></span>
                      </div>
                      <div className="act-card-foot small muted">
                        <span>Price <span className="num text">{entry.mffr_price ?? '–'}</span>{entry.price_source === 'estimate' ? ' est.' : ''}</span>
                        <span>Kratt <span className="num text">{kratt(entry)}</span></span>
                        {graphButton(entry)}
                        <button type="button" className="rowbtn" aria-expanded={open} aria-label="Show details" onClick={() => setOpenRow(open ? null : id)}>
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : undefined }}><path d="m6 9 6 6 6-6" /></svg>
                        </button>
                      </div>
                      {open && <RowDetails entry={entry} feesOn={feesOn} hm={hm} fmtNum={fmtNum} fmtEur={fmtEur} />}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </section>
      </div>
      {toolsOpen && (
        <>
          <div className="drawer-backdrop" onClick={() => setToolsOpen(false)} />
          <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="tools-title">
            <div className="drawer-head">
              <h2 id="tools-title">Data tools</h2>
              <button type="button" className="iconbtn" aria-label="Close data tools" onClick={() => setToolsOpen(false)}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
              </button>
            </div>
            <div className="drawer-body">
        <div className="tool">
          <button type="button" className="disclosure" aria-expanded={configOpen} onClick={toggleConfig}>
            <span className="tool-title">Configuration</span>
            <span className="muted small">
              {sensorStatus?.problems.length > 0
                ? <span className="err">{sensorStatus.problems.length} sensor problem{sensorStatus.problems.length > 1 ? 's' : ''}</span>
                : 'add-on options'}
            </span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: configOpen ? 'rotate(180deg)' : undefined }}><path d="m6 9 6 6 6-6" /></svg>
          </button>
          {configOpen && (
            <>
              {sensorStatus?.problems.map((p) => (
                <div key={`${p.option}-${p.entity_id}`} className="small err">{sensorProblemText(p)}</div>
              ))}
              <ConfigPanel apiBase={API_BASE} sensorStatus={sensorStatus} startedAt={installed?.started_at} onRestarted={reloadUi} />
            </>
          )}
        </div>
        <div className="divider" />
        <div className="tool">
          <div className="tool-title">Backfill from Home Assistant</div>
          <div className="tool-form">
            <label className="field">
              From
              <input type="datetime-local" value={backfillRange.from} disabled={backfillRunning}
                onChange={(e) => setBackfillRange({ ...backfillRange, from: e.target.value })} />
            </label>
            <label className="field">
              To
              <input type="datetime-local" value={backfillRange.to} max={toLocalInput(currentSlotStart())} disabled={backfillRunning}
                onChange={(e) => setBackfillRange({ ...backfillRange, to: e.target.value })} />
            </label>
            <button type="button" className="btn" onClick={startBackfill} disabled={backfillRunning || !backfillRange.from || !backfillRange.to}>
              {backfillRunning ? 'Backfilling…' : 'Backfill'}
            </button>
          </div>
          {backfillRunning && (
            <div className="progress">
              <div className="progress-bar"><div style={{ width: `${backfill.progress}%` }} /></div>
              <span className="muted small">{backfill.phase} · {backfill.progress}%</span>
            </div>
          )}
          {backfillRunning && (
            <div className="muted small">
              You can leave this page: the backfill keeps running in the add-on, and its progress shows here and on the
              Data tools button when you come back.
            </div>
          )}
          {backfill?.state === 'done' && (
            <div className="ok small">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
              {backfill.message} · {fmtSlot(backfill.from).split('–')[0]} → {fmtSlot(backfill.to).split('–')[0]}
            </div>
          )}
          {backfill?.state === 'error' && <div className="err small">{backfill.message}</div>}
          {backfillError && <div className="err small">{backfillError}</div>}
          <div className="muted small">
            Replays Home Assistant history through the tracker; rows in the range are recomputed. History is kept for <code>purge_keep_days</code> (10 days by default).
            The backfill runs in the add-on, so you can close this page or go elsewhere while it works.
          </div>
        </div>
        <div className="divider" />
        <div className="tool">
          <div className="tool-title">Qilowatt report</div>
          <label className="btn-ghost">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 16V4M6 10l6-6 6 6M4 20h16" /></svg>
            Import revenue or signals CSV
            <input className="visually-hidden" type="file" accept=".csv,text/csv" multiple
              onChange={(e) => { importQwReports([...e.target.files]); e.target.value = ''; }} />
          </label>
          {qwImport && qwImport.map((line) => (
            <div key={line} className={`small ${line.startsWith('✓') ? 'ok' : 'err'}`}>{line}</div>
          ))}
          <div className="muted small">Official per-slot energy and revenue are shown next to the tracker&apos;s own figures.</div>
        </div>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

function RowDetails({ entry, feesOn, hm, fmtNum, fmtEur }) {
  const gridCost = feesOn ? entry.grid_cost_fees : entry.grid_cost;
  const ppk = feesOn ? entry.price_per_kwh_fees : entry.price_per_kwh;
  const yesNo = (v) => (v === undefined || v === null ? '–' : v ? 'Yes' : 'No');
  return (
    <div className="details">
      <div><div className="muted small"><Hint label="Grid" hint={HINTS.grid} /></div><div className="num">{fmtNum(entry.grid_kwh)} kWh</div></div>
      <div><div className="muted small"><Hint label={feesOn ? 'Bill effect (with fees)' : 'Bill effect'} hint={HINTS.bill} /></div><div className="num">{typeof gridCost === 'number' ? fmtEur(-gridCost) : '–'}</div></div>
      <div><div className="muted small"><Hint label="Network rate" hint={HINTS.rate} /></div><div>{{ day: 'Day', night: 'Night / weekend', day_peak: 'Day peak', holiday_peak: 'Weekend peak' }[entry.tariff_period] ?? '–'}</div></div>
      <div><div className="muted small"><Hint label="NPS" hint={HINTS.nps} /></div><div className="num">{typeof entry.nordpool_price === 'number' ? `${(entry.nordpool_price * 1000).toFixed(2)} €/MWh` : '–'}</div></div>
      <div><div className="muted small"><Hint label="Baseline" hint={HINTS.baseline} /></div><div className="num">{typeof entry.baseline_w === 'number' ? `${Math.round(entry.baseline_w)} W` : '–'}</div></div>
      <div><div className="muted small"><Hint label="Start → end" hint={HINTS.span} /></div><div className="num">{hm(entry.start)} → {hm(entry.end)}</div></div>
      {entry.market === 'AFRR' && (
        <div><div className="muted small"><Hint label="aFRR market price (CBMP)" hint={HINTS.cbmp} /></div>
          <div className="num">{typeof entry.cbmp_avg === 'number' ? `${entry.cbmp_avg.toFixed(2)} €/MWh · ${entry.cbmp_points} pts` : entry.cbmp_points === 0 ? 'no prices published' : '–'}</div></div>
      )}
      <div><div className="muted small"><Hint label="€/MWh net" hint={HINTS.perMwh} /></div><div className="num">{typeof ppk === 'number' ? (ppk * 1000).toFixed(2) : '–'}</div></div>
      <div><div className="muted small"><Hint label="Backup" hint={HINTS.backupRow} /></div><div>{yesNo(entry.was_backup)}</div></div>
      <div><div className="muted small"><Hint label="Cancelled" hint={HINTS.cancelledRow} /></div><div>{yesNo(entry.cancelled)}</div></div>
    </div>
  );
}

export default App;
