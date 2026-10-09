// frontend/src/ConfigPanel.jsx — the add-on options, edited as a form or as YAML (Data tools → Configuration).
// Both views edit the same options; switching converts through the backend, saving restarts the add-on.
import { useEffect, useRef, useState } from 'react';

const POWER_UNITS = { w: 1, kw: 1000, mw: 1000000 };
const GROUPS = [
  ['Sensors', (k) => k.startsWith('sensor_')],
  ['Kratt and prices', (k) => k === 'kratt_share' || k.startsWith('afrr_') || k.startsWith('mfrr_')],
  ['aFRR market price (ENTSO-E)', (k) => k.startsWith('entsoe_')],
  ['Electricity fees', (k) => k.startsWith('fee_')],
  ['Home Assistant', () => true],
];

// Options whose change can apply to all history or only from now on
const isPriceKey = (k) => k === 'kratt_share' || k.startsWith('afrr_') || k.startsWith('fee_');

// Form values: text inputs hold strings, lists one string per row, switches booleans
const toValues = (fields, options) => Object.fromEntries(fields.map((f) => {
  const v = options[f.key];
  if (f.kind === 'list') return [f.key, Array.isArray(v) && v.length ? v.map(String) : ['']];
  if (f.kind === 'bool') return [f.key, Boolean(v)];
  return [f.key, v === undefined || v === null ? '' : String(v)];
}));

// Options from the form; empty optional fields are left out. Throws with a readable message.
const toOptions = (fields, values, extra) => {
  const options = {};
  for (const f of fields) {
    const v = values[f.key];
    if (f.kind === 'bool') { options[f.key] = Boolean(v); continue; }
    if (f.kind === 'list') {
      const items = v.map((s) => s.trim()).filter(Boolean);
      if (!items.length && !f.optional) throw new Error(`${f.name}: add at least one`);
      if (items.length) options[f.key] = items;
      continue;
    }
    const text = String(v ?? '').trim();
    if (!text) {
      if (!f.optional) throw new Error(`${f.name} is required`);
      continue;
    }
    if (f.kind === 'number') {
      const n = Number(text.replace(',', '.'));
      if (Number.isNaN(n)) throw new Error(`${f.name}: not a number`);
      options[f.key] = n;
    } else {
      options[f.key] = text;
    }
  }
  return { ...options, ...extra };   // options the schema doesn't know stay as they are
};

const post = async (url, body) => {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
};

export default function ConfigPanel({ apiBase, sensorStatus, startedAt, onRestarted }) {
  const [cfg, setCfg] = useState(null);          // GET /api/config
  const [mode, setMode] = useState('form');
  const [values, setValues] = useState({});
  const [extra, setExtra] = useState({});        // options outside the schema
  const [yamlText, setYamlText] = useState('');
  const [result, setResult] = useState(null);
  const [sensorQuery, setSensorQuery] = useState('');
  const [scope, setScope] = useState('now');    // how changed prices and fees apply
  const yamlRef = useRef(null);

  const apply = (data) => {
    setCfg(data);
    if (!data.editable) return;
    const known = new Set(data.fields.map((f) => f.key));
    setValues(toValues(data.fields, data.options));
    setExtra(Object.fromEntries(Object.entries(data.options).filter(([k]) => !known.has(k))));
    setYamlText(data.yaml);
  };
  useEffect(() => {
    fetch(`${apiBase}/api/config`)
      .then((res) => res.json().then((data) => (res.ok ? data : { editable: false, message: data.detail || `Error ${res.status}` })))
      .then(apply)
      .catch((e) => setCfg({ editable: false, message: String(e) }));
  }, [apiBase]);

  if (!cfg) return <div className="muted small">Loading…</div>;
  if (!cfg.editable) return <div className="muted small">{cfg.message}</div>;

  const entities = sensorStatus?.entities ?? [];
  const entityById = Object.fromEntries(entities.map((e) => [e.entity_id, e]));
  const set = (key, value) => setValues((v) => ({ ...v, [key]: value }));
  const formOptions = () => toOptions(cfg.fields, values, extra);
  const loaded = toValues(cfg.fields, cfg.options);
  const dirty = mode === 'form' ? JSON.stringify(values) !== JSON.stringify(loaded) : yamlText !== cfg.yaml;
  // In the YAML view any edit may touch prices, so the choice is shown for every change
  const priceChange = mode === 'form'
    ? cfg.fields.some((f) => isPriceKey(f.key) && JSON.stringify(values[f.key]) !== JSON.stringify(loaded[f.key]))
    : dirty;

  const switchTo = async (next) => {
    if (next === mode) return;
    setResult(null);
    try {
      if (next === 'yaml') {
        setYamlText((await post(`${apiBase}/api/config/convert`, { options: formOptions() })).yaml);
      } else {
        const { options } = await post(`${apiBase}/api/config/convert`, { yaml: yamlText });
        const known = new Set(cfg.fields.map((f) => f.key));
        setValues(toValues(cfg.fields, options));
        setExtra(Object.fromEntries(Object.entries(options).filter(([k]) => !known.has(k))));
      }
      setMode(next);
    } catch (e) {
      setResult({ ok: false, text: e.message });
    }
  };

  // Save, then wait for the restarted add-on (a new started_at) and load its page
  const save = async () => {
    setResult(null);
    try {
      const body = mode === 'form' ? { options: formOptions() } : { yaml: yamlText };
      await post(`${apiBase}/api/config`, priceChange ? { ...body, scope } : body);
    } catch (e) {
      setResult({ ok: false, text: e.message });
      return;
    }
    setResult({ ok: true, text: 'Saved. Restarting the add-on…' });
    for (let i = 0; i < 90; i += 1) {
      await new Promise((r) => setTimeout(r, 2000));
      const v = await fetch(`${apiBase}/api/version`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (v && v.started_at !== startedAt) { onRestarted(v.version); return; }
    }
    setResult({ ok: false, text: 'The add-on didn\'t come back within 3 minutes. Check its log in Home Assistant.' });
  };
  const undo = () => { apply(cfg); setResult(null); };

  // Current state under a sensor input, or why it can't be used
  const note = (id) => {
    const e = entityById[id.trim()];
    if (!id.trim() || !entities.length) return null;
    if (!e) return <span className="small err">Not found in Home Assistant</span>;
    return <span className="small">{e.name ? `${e.name} · ` : ''}{e.state}{e.unit ? ` ${e.unit}` : ''}</span>;
  };
  const entityInput = (value, onChange, label) => (
    <input type="text" list="ha-sensors" value={value} placeholder="sensor.…" aria-label={label}
      spellCheck={false} autoComplete="off" onChange={(ev) => onChange(ev.target.value)} />
  );

  const pkgKey = 'fee_network_package';
  const pkg = cfg.packages[values[pkgKey]];
  const presetRates = pkg?.rates;

  const renderField = (f) => {
    const v = values[f.key];
    // A Võrk package brings its own network rates; the custom rate fields only apply to "custom"
    if (cfg.network_options.includes(f.key) && presetRates) return null;
    const label = (
      <span className="config-label">
        {f.name}{f.optional && <span className="unit"> optional</span>}
        <code className="config-key">{f.key}</code>
      </span>
    );
    const help = f.description && <span className="muted small">{f.description}</span>;
    if (f.kind === 'list') {
      // Net grid power right now, as the tracker sums it (W, + import / − export)
      const watts = v.map((id) => {
        const e = entityById[id.trim()];
        const n = Number(e?.state);
        return e && e.state !== '' && Number.isFinite(n) ? n * (POWER_UNITS[(e.unit || 'W').toLowerCase()] ?? 1) : null;
      });
      const net = watts.length && watts.every((w) => w !== null) ? watts.reduce((a, b) => a + b, 0) : null;
      return (
        <div key={f.key} className="field">
          {label}
          {v.map((id, i) => (
            <div key={i} className="config-row-wrap">
              <div className="config-row">
                {entityInput(id, (value) => set(f.key, v.map((p, j) => (j === i ? value : p))), `${f.name} ${i + 1}`)}
                <button type="button" className="iconbtn" aria-label={`Remove ${f.name} ${i + 1}`} disabled={v.length === 1}
                  onClick={() => set(f.key, v.filter((_, j) => j !== i))}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
                </button>
              </div>
              {f.entity && note(id)}
            </div>
          ))}
          <button type="button" className="btn-link config-add" onClick={() => set(f.key, [...v, ''])}>+ Add sensor</button>
          {net !== null && <span className="small num">Net grid now: {Math.round(Math.abs(net))} W {net >= 0 ? 'import' : 'export'}</span>}
          {help}
        </div>
      );
    }
    if (f.kind === 'select') {
      const labelOf = (c) => (f.key === pkgKey ? (c === 'custom' ? 'Custom' : cfg.packages[c]?.label.replace('Elektrilevi ', '') ?? c) : c);
      return (
        <div key={f.key} className="field">
          {label}
          <div className="seg seg-sm" role="group" aria-label={f.name}>
            {f.choices.map((c) => (
              <button key={c} type="button" className={v === c ? 'on' : ''} aria-pressed={v === c} onClick={() => set(f.key, c)}>{labelOf(c)}</button>
            ))}
          </div>
          {f.key === pkgKey && pkg && (
            <span className="small">
              {pkg.label}{pkg.note ? ` · ${pkg.note}` : ''}
              {presetRates && ` · network day ${presetRates.elektrilevi_day} · night ${presetRates.elektrilevi_night}`}
              {presetRates?.elektrilevi_day_peak > 0 && ` · peaks ${presetRates.elektrilevi_day_peak} / ${presetRates.elektrilevi_holiday_peak}`}
              {presetRates && ' s/kWh excl. VAT'}
            </span>
          )}
          {help}
        </div>
      );
    }
    if (f.kind === 'bool') {
      return (
        <div key={f.key} className="field">
          {label}
          <button type="button" className={`switch ${v ? 'on' : ''}`} role="switch" aria-checked={v} aria-label={f.name}
            onClick={() => set(f.key, !v)}>
            <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
            {v ? 'On' : 'Off'}
          </button>
          {help}
        </div>
      );
    }
    return (
      <label key={f.key} className="field">
        {label}
        {f.entity ? entityInput(v, (value) => set(f.key, value), f.name) : (
          <input type={f.kind === 'password' ? 'password' : 'text'} inputMode={f.kind === 'number' ? 'decimal' : undefined}
            value={v} autoComplete="off" spellCheck={false}
            placeholder={cfg.defaults[f.key] !== undefined ? `${cfg.defaults[f.key]} (default)` : undefined}
            onFocus={(ev) => { if (f.kind === 'password' && v === cfg.mask) ev.target.select(); }}
            onChange={(ev) => set(f.key, ev.target.value)} />
        )}
        {f.entity && note(v)}
        {help}
      </label>
    );
  };

  // YAML view: put a sensor ID where the cursor is
  const insertSensor = () => {
    const id = sensorQuery.trim();
    const ta = yamlRef.current;
    if (!id || !ta) return;
    const start = ta.selectionStart ?? yamlText.length;
    const end = ta.selectionEnd ?? start;
    setYamlText(yamlText.slice(0, start) + id + yamlText.slice(end));
    setSensorQuery('');
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(start + id.length, start + id.length); });
  };

  const placed = new Set();
  return (
    <>
      <div className="seg seg-sm" role="group" aria-label="Editor view">
        {[['form', 'Form'], ['yaml', 'YAML']].map(([m, text]) => (
          <button key={m} type="button" className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => switchTo(m)}>{text}</button>
        ))}
      </div>
      <datalist id="ha-sensors">
        {entities.map((e) => (
          <option key={e.entity_id} value={e.entity_id} label={`${e.name ?? e.entity_id} · ${e.state}${e.unit ? ` ${e.unit}` : ''}`} />
        ))}
      </datalist>
      {mode === 'form' ? GROUPS.map(([title, match]) => {
        const group = cfg.fields.filter((f) => !placed.has(f.key) && match(f.key));
        group.forEach((f) => placed.add(f.key));
        if (!group.length) return null;
        return (
          <fieldset key={title} className="config-group">
            <legend className="tool-title">{title}</legend>
            {group.map(renderField)}
          </fieldset>
        );
      }) : (
        <>
          <textarea ref={yamlRef} className="config-yaml" value={yamlText} rows={Math.min(30, yamlText.split('\n').length + 2)}
            spellCheck={false} aria-label="Add-on configuration (YAML)" onChange={(e) => setYamlText(e.target.value)} />
          <div className="tool-form">
            <label className="field">
              Insert a sensor at the cursor
              <input type="text" list="ha-sensors" value={sensorQuery} placeholder="Search Home Assistant sensors…"
                spellCheck={false} autoComplete="off" onChange={(e) => setSensorQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); insertSensor(); } }} />
            </label>
            <button type="button" className="btn-link" onClick={insertSensor} disabled={!sensorQuery.trim()}>Insert</button>
          </div>
        </>
      )}
      {priceChange && (
        <div className="field config-scope">
          <span className="config-label">{mode === 'form' ? 'Changed prices and fees apply to' : 'If prices or fees changed, they apply to'}</span>
          <div className="seg seg-sm" role="group" aria-label="Apply changed prices and fees to">
            {[['now', 'From now on'], ['all', 'All history']].map(([s, text]) => (
              <button key={s} type="button" className={scope === s ? 'on' : ''} aria-pressed={scope === s} onClick={() => setScope(s)}>{text}</button>
            ))}
          </div>
          <span className="muted small">
            {scope === 'now'
              ? 'Activations so far keep the values they were calculated with; the current 15-minute slot and later use the new ones.'
              : 'Every activation is recalculated with the new values, also the aFRR estimates still waiting for a published price.'}
          </span>
        </div>
      )}
      <div className="tool-form">
        <button type="button" className="btn" onClick={save} disabled={result?.ok}>Save &amp; restart</button>
        {dirty && !result?.ok && <button type="button" className="btn-link" onClick={undo}>Undo changes</button>}
      </div>
      {result && <div className={`small ${result.ok ? 'ok' : 'err config-msg'}`}>{result.text}</div>}
      <div className="muted small">
        The same options as the add-on&apos;s Configuration tab in Home Assistant. Saving restarts the add-on
        (a few seconds), and the page reloads when it&apos;s back. Prices and fees changed in Home Assistant&apos;s
        Configuration tab apply from the next start on.
      </div>
    </>
  );
}
