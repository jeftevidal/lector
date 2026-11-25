const $ = (q) => document.querySelector(q);
const ui = {bookPath: null, file: null, inspect: null, job: null, state: null, result: null, run: null, log: [], provider: 'claude', model: 'sonnet', other: false,
  st: {kind: 'idle', pct: 0, parts: ['Pronto para iniciar']}, modal: null, usage: {}, official: {}, officialOn: false, exhausted: {}, fallback: '', quota: null, q: null, filter: 'uncertain', installFor: null, batchSel: 'default', timer: null, stream: null};
let lastUpdate = 0;
let updateTicker = null;
const LANGS = [['en', 'Inglês (en)'], ['zh', 'Chinês (zh)']];
const INSTALL = {
  claude: 'Instale o Claude Code com npm install -g @anthropic-ai/claude-code e entre na sua conta executando claude uma vez.',
  agy: 'Instale o Antigravity CLI (agy), entre na sua conta executando agy uma vez e confirme que o comando agy responde no terminal.',
  codex: 'Instale o Codex CLI com npm install -g @openai/codex e entre na sua conta executando codex uma vez.',
  ollama: 'Baixe o Ollama em ollama.com e baixe um modelo com ollama pull <modelo>. Tudo roda localmente.'
};
const BATCH_MODES = [['less', 'Mais rápido', '2×', 'Para um modelo mais forte, como opus. Envia até 3 lotes em paralelo.'],
  ['default', 'Recomendado', '1×', 'Valores do provedor em models.json.'],
  ['more', 'Mais seguro', '0,5×', 'Para um modelo mais fraco, como haiku ou ollama.']];
const fmt = (n) => Number(n || 0).toLocaleString('pt-BR');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const base = (p) => String(p || '').split(/[\\/]/).pop();
const json = (body) => ({headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const api = async (url, options = {}) => {
  const r = await fetch(url, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Falha HTTP ${r.status}`);
  return body;
};
const put = (url, body) => api(url, {method: 'PUT', ...json(body)});
const post = (url, body) => api(url, {method: 'POST', ...json(body)});
function fail(e) {
  console.error(e);
  if (ui.run) return;
  const busy = /em andamento/.test(e.message || '');
  setStatus({kind: busy ? 'stopped' : 'error', pct: ui.st.pct, parts: busy ? ['Aguarde', 'a tradução anterior ainda está encerrando o lote atual; tente Retomar em instantes'] : ['Erro', e.message || String(e)]});
}

/* ---------- status row ---------- */
function stopTicker() { if (updateTicker) { clearInterval(updateTicker); updateTicker = null; } lastUpdate = 0; document.querySelectorAll('.upd').forEach(n => n.textContent = ''); }
function renderUpdatedAt() {
  if (!lastUpdate) return;
  const s = Math.max(0, Math.floor((Date.now() - lastUpdate) / 1000));
  document.querySelectorAll('.upd').forEach(n => n.textContent = `atualizado há ${s} s`);
}
function statusHTML(real) {
  const s = ui.st, [main, ...rest] = s.parts;
  const sub = real ? rest : rest.filter(t => !t.includes('min restante'));  // no modal o restante já aparece nas métricas do topo
  const id = (name) => real ? ` id="${name}"` : '';
  const action = {run: `<button class="secondary"${id('cancel')} data-act="cancel">Cancelar</button>`,
    stopped: `<button class="primary"${id('resume')} data-act="resume">Retomar</button>`,
    done: `<button class="primary"${id('open-output')} data-act="open">Abrir local do arquivo</button>`,
    blocked: `<button class="secondary" data-act="open">Abrir pasta do relatório</button>`}[s.kind] || '';
  return `<div class="sr-top"><strong class="sr-pct"${id('job-percent')}>${s.pct}%</strong><div class="sr-text"><b${id('job-status')}>${esc(main)}</b>${sub.map(t => `<span>${esc(t)}</span>`).join('')}${s.kind === 'run' ? `<span class="upd"${id('updated-at')}></span>` : ''}</div><div class="status-actions">${action}</div></div><div class="progress"><i${id('progress-fill')} style="width:${s.pct}%"></i></div>`;
}
function setStatus(st) {
  ui.st = st;
  $('#status-row').innerHTML = statusHTML(true);
  renderUpdatedAt();
  if (ui.modal === 'status-detalhes') renderStatusModal();
}
function runPct(r) { return r.total ? Math.min(100, Math.round(r.done * 100 / r.total)) : 0; }
function runEta(r) {
  if (!(r.done > 0 && r.done < r.total && r.elapsed)) return null;
  return Math.max(1, Math.ceil(r.elapsed * (r.total - r.done) / r.done / 60));
}
function stoppedStatus(r) {
  const batch = r.batchIdx && r.batchTotal ? `parou no lote ${r.batchIdx} de ${r.batchTotal}` : 'pronta para retomar';
  const parts = [r.quota || ui.quota ? (ui.quota?.reason === 'unavailable' ? 'Interrompida - motor indisponível' : 'Interrompida - cota esgotada') : 'Interrompida', batch];
  return {kind: 'stopped', pct: runPct(r), parts};
}
function runningStatus(r) {
  const eta = runEta(r), parts = ['Em andamento'];
  if (eta) parts.push(`~${eta} min restante`);
  if (r.reviewing) parts.push('revisando incertos');
  if (r.engineNote) parts.push(r.engineNote);
  return {kind: 'run', pct: runPct(r), parts};
}

/* ---------- main screen ---------- */
const opts = () => ({provider: ui.provider, model: ui.other ? $('#custom-model').value : ui.model, source_lang: $('#source-lang').value, target_lang: $('#target-lang').value,
  main_content_only: $('#main-only').checked, reuse_cache: $('#reuse-cache').checked, review_uncertain: $('#review-uncertain').checked, batch_mode: ui.state?.preferences?.batch_mode || 'default', fallback: ui.fallback});
function setSelect(sel, value, detected) {
  if (!value) return;
  let v = value;
  if (![...sel.options].some(o => o.value === v)) {
    const root = v.split('-')[0], match = [...sel.options].find(o => o.value === root);
    if (match) v = match.value; else { sel.add(new Option(detected ? `Detectado (${v})` : `${v} (${v})`, v)); }
  }
  sel.value = v;
  syncDD();
}
function fillLangs() {
  $('#source-lang').replaceChildren(...LANGS.map(([v, t]) => new Option(t, v)));
  $('#source-lang').value = 'en';
  syncDD();
}
function ddHTML(options, selected) {
  const cur = options.find(([v]) => v === selected) || options[0];
  return `<button type="button" class="dd-btn" aria-haspopup="listbox" aria-expanded="false">${esc(cur[1])}</button><div class="dd-menu" role="listbox" hidden>${options.map(([v, t]) => `<button type="button" class="dd-opt${v === cur[0] ? ' sel' : ''}" role="option" aria-selected="${v === cur[0]}" data-v="${esc(v)}">${esc(t)}</button>`).join('')}</div>`;
}
function placeMenu(menu, btn) {
  const r = btn.getBoundingClientRect(), room = Math.max(120, innerHeight - r.bottom - 16);
  Object.assign(menu.style, {position: 'fixed', left: `${r.left}px`, top: `${r.bottom + 6}px`, width: `${r.width}px`, right: 'auto', bottom: 'auto', maxHeight: `${Math.min(260, room)}px`});
}
function closeDDs() {
  document.querySelectorAll('.dd-menu:not([hidden])').forEach(m => { m.hidden = true; m.previousElementSibling.setAttribute('aria-expanded', 'false'); });
}
function syncDD() {
  const sel = $('#source-lang');
  $('#source-dd').innerHTML = ddHTML([...sel.options].map(o => [o.value, o.textContent]), sel.value);
}
function pickDD(name, v) {
  if (name === 'source') { $('#source-lang').value = v; $('#source-lang').dispatchEvent(new Event('change')); syncDD(); return; }
  ui.fallback = v; renderFallback();
  const modalDD = document.querySelector('[data-dd=fallback-modal]');
  if (modalDD) modalDD.innerHTML = ddHTML(fallbackList(), ui.fallback);
  put('/api/preferences', {fallback: ui.fallback}).catch(fail);
}
function providerList() { return ui.state?.providers || {}; }
function resetMs(text) {
  const part = (re) => { const m = re.exec(text || ''); return m ? Number(m[1]) : 0; };
  return ((part(/(\d+)\s*h/) * 60 + part(/(\d+)\s*m(?!s)/)) * 60 + part(/(\d+)\s*s/)) * 1000;
}
function fmtDur(ms) {
  const m = Math.max(1, Math.round(ms / 60000)), h = Math.floor(m / 60);
  return h ? `${h}h${String(m % 60).padStart(2, '0')}m` : `${m}m`;
}
function fmtTokens(n) { return n >= 1e6 ? `${(n / 1e6).toFixed(1).replace('.', ',')} M` : n >= 1e3 ? `${Math.round(n / 1e3)} mil` : String(n); }
function usageText(name) {
  const u = ui.usage[name]?.['5h'];
  if (!u || !u.calls) return '';
  const tokens = u.in + u.out;
  return tokens ? `${fmtTokens(tokens)} tokens - 5 h` : `${u.calls} chamadas - 5 h`;
}
function usageTip(name) {
  const w = ui.usage[name];
  if (!w) return 'Sem uso registrado pelo Lector.';
  const line = (k, label) => `${label}: ${w[k].calls} chamadas - ${fmtTokens(w[k].in)} entrada - ${fmtTokens(w[k].out)} saída${w[k].cost ? ` - US$ ${w[k].cost.toFixed(2)}` : ''}`;
  return `${line('5h', 'Últimas 5 h')}\n${line('24h', 'Últimas 24 h')}\n${line('7d', 'Últimos 7 dias')}\n(Só o que o Lector gastou; não é a cota oficial do provedor.)`;
}
function officialInfo(name) {
  const o = ui.official[name];
  if (!o || !ui.officialOn) return null;
  const when = (w) => w?.resets_at ? ` (volta em ${fmtDur(w.resets_at * 1000 - Date.now())})` : '';
  const lines = [];
  if (o.session) lines.push(`Janela principal: ${Math.round(o.session.used_percent)}% usado${when(o.session)}`);
  if (o.weekly) lines.push(`Janela semanal: ${Math.round(o.weekly.used_percent)}% usado${when(o.weekly)}`);
  if (o.error) lines.push(`Cota oficial indisponível: ${o.error}`);
  return {pct: o.session ? o.session.used_percent : null, reset: o.session?.resets_at ? o.session.resets_at * 1000 : 0, tip: lines.join('\n')};
}
async function fetchOfficial(force = false) {
  try {
    const r = await api(`/api/usage/official${force ? '?refresh=true' : ''}`);
    ui.officialOn = r.enabled; ui.official = r.providers || {};
    $('#official-usage').checked = r.enabled;
    renderProviders();
  } catch (e) { console.error(e); }
}
async function fetchUsage() {
  try { ui.usage = (await api('/api/usage')).usage || {}; renderProviders(); } catch (e) { console.error(e); }
}
function tickExhausted() {
  let changed = false;
  for (const [name, ex] of Object.entries(ui.exhausted)) if (ex.until && ex.until <= Date.now()) { delete ui.exhausted[name]; changed = true; }
  if (changed || Object.keys(ui.exhausted).length) renderProviders();
}
function renderProviders() {
  const ORDER = ['claude', 'codex', 'agy', 'ollama'], rank = (n) => (ORDER.indexOf(n) + 1 || 99);
  $('#provider-list').innerHTML = Object.entries(providerList()).sort((a, b) => rank(a[0]) - rank(b[0])).map(([name, p]) => {
    const off = officialInfo(name);
    const full = off && off.pct !== null && off.pct >= 100;
    const ex = ui.exhausted[name] || (full ? {reset: '', until: off.reset} : null), used = usageText(name);
    const back = ex?.until ? ` - volta em ${fmtDur(ex.until - Date.now())}` : ex?.reset ? ` - volta em ${esc(ex.reset)}` : '';
    const bar = off && off.pct !== null ? `<span class="mbar${off.pct >= 90 ? ' hot' : ''}"><i style="width:${off.pct}%"></i></span>${Math.round(off.pct)}% usado${off.reset ? ' - volta em ' + fmtDur(off.reset - Date.now()) : ''}` : '';
    const right = ex ? `<span class="missing">cota esgotada${back}</span>` : bar ? `<span class="ok">${bar}</span>` : p.installed ? `<span class="ok">${used ? esc(used) : 'instalado'}</span>` : `<span class="missing">não encontrado</span><button type="button" class="link" data-install="${esc(name)}">Como instalar</button>`;
    return `<div class="prow has-tip${name === ui.provider ? ' sel' : ''}${ui.exhausted[name] ? ' quota' : ''}" role="radio" aria-checked="${name === ui.provider}" tabindex="0" data-p="${esc(name)}" data-tip="${esc((off ? off.tip + '\n\n' : '') + usageTip(name))}"><span>${esc(name)}</span><span class="right">${right}</span></div>`;
  }).join('');
}
function renderModels() {
  const p = providerList()[ui.provider], models = p?.models?.length ? p.models : ['pseudo'];
  if (!ui.other && !models.includes(ui.model)) { if (ui.model) ui.other = true; else ui.model = models[0]; }
  $('#model-list').innerHTML = models.map(m => `<button type="button" class="mpill${!ui.other && m === ui.model ? ' sel' : ''}" role="radio" aria-checked="${!ui.other && m === ui.model}" data-m="${esc(m)}">${esc(m)}</button>`).join('')
    + `<button type="button" class="mpill${ui.other ? ' sel' : ''}" role="radio" aria-checked="${ui.other}" data-m="__other__">outro…</button>`;
  $('#custom-model').hidden = !ui.other;
  if (ui.other && !$('#custom-model').value && ui.model) $('#custom-model').value = ui.model;
  updateBatchSummary();
  renderFallback();
}
function currentModel() { return ui.other ? $('#custom-model').value : ui.model; }
function fallbackList() {
  const cur = `${ui.provider}|${currentModel()}`;
  const list = [['', 'Perguntar antes de trocar']];
  for (const [name, p] of Object.entries(providerList())) {
    if (!p.installed) continue;
    for (const m of (p.models?.length ? p.models : [])) if (`${name}|${m}` !== cur) list.push([`${name}|${m}`, `Trocar para ${name} - ${m}`]);
  }
  return list;
}
function renderFallback() {
  const box = $('#fallback-dd');
  if (!box) return;
  const list = fallbackList();
  if (!list.some(([v]) => v === ui.fallback)) ui.fallback = '';
  box.innerHTML = ddHTML(list, ui.fallback);
}
function updateBatchSummary() {
  const p = providerList()[ui.provider], mult = {less: 2, default: 1, more: .5}[ui.state?.preferences?.batch_mode || 'default'];
  $('#batch-summary').textContent = `${Math.max(1, Math.floor((p?.batch_segments || 60) * mult))} segmentos - ${p?.timeout || 600} s`;
}
function updateTerms() { $('#term-count').textContent = `${Object.keys(ui.state.glossary.translate_map || {}).length} termos`; }
function reviewCounts() {
  const sum = ui.result?.summary, finds = ui.result?.findings || [];
  return {u: sum ? sum.uncertain : 0, w: sum ? sum.warn : finds.filter(f => f.severity === 'WARN').length};
}
function updateReview() {
  const {u, w} = reviewCounts(), el = $('#review-summary');
  el.textContent = u || w ? `${u} incertos - ${w} avisos` : '0 pendências';
  el.className = u || w ? 'orange' : 'muted';
}
async function refresh() {
  ui.state = await api('/api/state');
  const prefs = ui.state.preferences, names = Object.keys(ui.state.providers);
  ui.provider = names.includes(prefs.provider) ? prefs.provider : names[0];
  ui.model = prefs.model || '';
  $('#target-lang').value = 'pt-BR';
  $('#reuse-cache').checked = prefs.reuse_cache !== false;
  $('#review-uncertain').checked = prefs.review_uncertain !== false;
  ui.fallback = prefs.fallback || '';
  $('#main-only').checked = Boolean(ui.state.glossary.main_content_only);
  renderProviders(); renderModels(); updateTerms(); updateReview();
  const last = ui.state.last_job, active = ui.state.active_job;
  if (active && !ui.run) { ui.job = active.id; ui.log = []; ui.result = null; beginRun(); }
  else if (!ui.run && last?.status === 'Concluído') {
    setStatus({kind: 'done', pct: 100, parts: ['Concluído', `${fmt(last.total || last.done || 0)} segmentos`, `${last.errors || 0} erros`]});
  } else if (!ui.run && last?.status === 'QA bloqueou') {
    setStatus({kind: 'blocked', pct: 100, parts: ['QA bloqueou', 'tradução pronta, EPUB não gerado - veja o relatório']});
  } else if (!ui.run && last?.status === 'Parada') {
    ui.quota = last.quota ? {provider: last.quota_provider, model: last.quota_model, engine: last.quota_engine, reset: last.quota_reset, reason: last.quota_reason} : null;
    if (last.quota && last.quota_provider && last.quota_reason !== 'unavailable') {
      const until = last.quota_reset ? Date.parse(last.updated) + resetMs(last.quota_reset) : 0;
      ui.exhausted[last.quota_provider] = {reset: last.quota_reset, until: until > Date.now() ? until : 0};
      if (until && until <= Date.now()) delete ui.exhausted[last.quota_provider];
    }
    renderProviders();
    const total = last.total || 0;
    setStatus(stoppedStatus({done: last.done || 0, total, batchIdx: last.batch, batchTotal: last.batches}));
  } else if (!ui.run) setStatus(ui.st);
  if (!ui.file && !ui.bookPath && last?.input) { ui.bookPath = last.input; reinspect(); }
}
async function inspectFile() {
  if (!ui.file && !ui.bookPath) return;
  const form = new FormData();
  if (ui.file) form.append('file', ui.file); else form.append('path', ui.bookPath);
  form.append('options', JSON.stringify(opts()));
  ui.inspect = await api('/api/inspect', {method: 'POST', body: form});
  const i = ui.inspect;
  if (i.book_path && ui.file) ui.bookPath = i.book_path;
  if (i.language && !$('#source-lang').dataset.edited) setSelect($('#source-lang'), i.language, true);
  $('#filename').textContent = i.filename.replace(/-[0-9a-f]{10}[.]epub$/, '.epub');
  $('#bookmeta').textContent = `EPUB ${i.epub_version} - ${i.chapter_count} capítulos`;
  $('#segments').textContent = fmt(i.segments);
  $('#calls').textContent = `~${fmt(i.calls)}`;
  $('#cache-summary').textContent = `${fmt(i.exact_cache)} já em cache - restam ~${fmt(i.pending)}`;
  $('#start').disabled = false;
}
let reinspectTimer = null;
const reinspect = () => {  // debounce: várias trocas seguidas viram uma inspeção só
  clearTimeout(reinspectTimer);
  reinspectTimer = setTimeout(() => { if (ui.file || ui.bookPath) inspectFile().catch(fail); }, 250);
};
function selectProvider(name) {
  if (!name || name === ui.provider) return;
  ui.provider = name; ui.model = ''; ui.other = false; $('#custom-model').value = '';
  renderProviders(); renderModels(); reinspect();
}
function selectModel(m) {
  if (m === '__other__') { ui.other = true; renderModels(); $('#custom-model').focus(); return; }
  ui.other = false; ui.model = m; renderModels(); reinspect();
}

/* ---------- job ---------- */
async function startJob(resumePath = null, extra = {}) {
  if (!ui.file && !resumePath && !ui.bookPath) return;
  const form = new FormData();
  if (resumePath) form.append('path', resumePath); else if (ui.bookPath) form.append('path', ui.bookPath); else form.append('file', ui.file);
  form.append('options', JSON.stringify({...opts(), ...extra}));
  await put('/api/preferences', opts());
  const job = await api('/api/jobs', {method: 'POST', body: form});
  ui.job = job.id; ui.log = []; ui.result = null; ui.quota = null; delete ui.exhausted[ui.provider]; renderProviders(); updateReview();
  beginRun();
}
function beginRun() {
  ui.run = {total: ui.inspect?.segments || 0, done: 0, cache: 0, chapters: ui.inspect?.chapters || [], cdone: {}, cerr: {}, active: null, batchIdx: 0, batchTotal: 0, elapsed: 0, ended: false};
  setStatus({kind: 'run', pct: 0, parts: ['Em andamento', 'iniciando']});
  $('#start').disabled = true;
  stopTicker(); lastUpdate = Date.now(); updateTicker = setInterval(renderUpdatedAt, 1000);
  attachStream(ui.job);
}
function attachStream(id) {
  if (ui.stream) ui.stream.close();
  const stream = new EventSource(`/api/jobs/${id}/events`);
  ui.stream = stream;
  stream.onmessage = (e) => onJobEvent(JSON.parse(e.data), stream).catch(fail);
  stream.onerror = () => {
    // queda momentânea: o EventSource reconecta sozinho (Last-Event-ID evita repetir eventos)
    if (ui.run?.ended || stream.readyState !== EventSource.CLOSED) return;
    stopTicker(); ui.run = null;
    setStatus({kind: 'error', pct: ui.st.pct, parts: ['Conexão com a tradução perdida']});
    $('#start').disabled = !(ui.file || ui.bookPath);
  };
}
async function onJobEvent(d, stream) {
  const r = ui.run;
  if (!r || r.ended) return;
  ui.log.push(d); lastUpdate = Date.now(); renderUpdatedAt();
  if (d.chapter_errors != null && d.file) r.cerr[d.file] = d.chapter_errors;
  if (d.chapter_progress) for (const [f, [dn, er]] of Object.entries(d.chapter_progress)) { r.cdone[f] = dn; r.cerr[f] = er; }
  if (d.event === 'batch_completed' && Date.now() - (ui.usageAt || 0) > 5000) { ui.usageAt = Date.now(); fetchUsage(); }
  if (d.event === 'review_started') r.reviewing = true;
  if (d.event === 'review_completed' || d.event === 'review_skipped') r.reviewing = false;
  if (d.event === 'engine_switched') {
    r.engineNote = `trocou para ${d.to}`;
    const from = String(d.from || '').split(':')[0].replace('claude-cli', 'claude');
    if (from) { ui.exhausted[from] = ui.exhausted[from] || {reset: '', until: 0}; renderProviders(); }
  }
  if (d.event === 'quota_exhausted') {
    ui.q = null; r.quota = d; ui.quota = {provider: d.provider, model: d.model, engine: d.engine, reset: d.reset, reason: d.reason, message: d.message};
    if (d.provider && d.reason !== 'unavailable') ui.exhausted[d.provider] = {reset: d.reset, until: d.reset ? Date.now() + resetMs(d.reset) : 0};
    renderProviders(); renderFallback();
    r.cancelRequested = true; setStatus(stoppedStatus(r)); details('cota-esgotada-detalhes');
  }
  if (d.event === 'plan') { r.chapters = d.chapters || r.chapters; r.total = d.total_segments ?? r.total; }
  else if (d.event !== 'finished' && d.total_segments != null) r.total = d.total_segments;
  if (d.event !== 'finished') {
    if (d.done_segments != null) r.done = d.done_segments;
    if (d.file) { r.active = d.file; if (d.chapter_done != null) r.cdone[d.file] = d.chapter_done; }
    if (d.index != null && d.total != null) { r.batchIdx = d.index; r.batchTotal = d.total; }
    if (d.elapsed_seconds != null) r.elapsed = d.elapsed_seconds;
    if (d.event === 'batch_completed') r.batches = (r.batches || 0) + 1;
    if (d.event === 'cache_summary') r.cache = (d.exact || 0) + (d.cross_model || 0);
  }
  if (d.event === 'cancelled') { setStatus(stoppedStatus(r)); }
  else if (d.event === 'error') {
    r.ended = true; stream.close(); stopTicker(); ui.run = null; $('#start').disabled = !(ui.file || ui.bookPath);
    setStatus({kind: 'error', pct: runPct(r), parts: ['Erro', d.message || 'falha na tradução']});
  } else if (d.event === 'finished' && d.status) {  // só o evento final do job (traz status); o do tradutor se chama translation_finished
    r.ended = true; stream.close(); stopTicker(); $('#start').disabled = !(ui.file || ui.bookPath);
    ui.result = await api(`/api/jobs/${ui.job}/result?filter=uncertain&limit=300`);
    ui.segView = {filter: 'uncertain', segments: ui.result.segments || []};
    const errors = ui.result.summary?.errors || 0;
    ui.state.last_job = (await api('/api/state')).last_job;
    updateReview();
    if (d.status === 'Concluído') { r.done = r.total; setStatus({kind: 'done', pct: 100, parts: ['Concluído', `${fmt(r.total)} segmentos`, `${errors} erros`]}); }
    else if (d.status === 'Parada') setStatus(stoppedStatus(r));
    else if (d.status === 'QA bloqueou') {
      const blocks = (ui.result.findings || []).filter(f => f.severity === 'BLOCK').length;
      setStatus({kind: 'blocked', pct: 100, parts: ['QA bloqueou', `${blocks} bloqueios - tradução pronta, EPUB não gerado`]});
    } else setStatus({kind: 'error', pct: runPct(r), parts: [d.status || 'Erro', 'veja Revisar']});
    r.status = d.status; ui.finished = r;
  } else if (d.event !== 'quota_exhausted') setStatus(r.cancelRequested ? stoppedStatus(r) : runningStatus(r));
  if (!r.ended) ui.run = r; else if (d.event === 'finished') ui.run = null;
  if (ui.modal === 'status-detalhes') renderStatusModal();
}

/* ---------- overlays ---------- */
const POS = {'status-detalhes': 'pos-bottom', 'glossario-detalhes': 'pos-c1-2', 'lotes-detalhes': 'pos-c2', 'como-instalar-detalhes': 'pos-c2',
  'cota-esgotada-detalhes': 'pos-c2', 'simulacao-detalhes': 'pos-c3', 'revisao-detalhes': 'pos-c3', 'revisao-expandida': 'pos-all', 'cancelar-detalhes': 'pos-cancel'};
function ghosts() {
  const stage = $('#stage');
  stage.querySelectorAll('.ghost').forEach(n => n.remove());
  for (const sel of ['.page-header', '#status-row']) {
    const c = $(sel).cloneNode(true);
    c.removeAttribute('id'); c.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));
    c.classList.add('ghost'); c.setAttribute('aria-hidden', 'true'); c.setAttribute('inert', ''); c.removeAttribute('tabindex');
    stage.prepend(c);
  }
}
function showModal(id, html) {
  ghosts();
  ui.modal = id;
  if (ui.timer) { clearInterval(ui.timer); ui.timer = null; }
  $('#modal').className = `modal ${POS[id] || 'pos-c2'}`;
  $('#modal-content').innerHTML = html;
  $('#backdrop').classList.add('open');
  $('#backdrop').setAttribute('aria-hidden', 'false');
}
function hideModal() {
  if (ui.timer) { clearInterval(ui.timer); ui.timer = null; }
  ui.modal = null;
  $('#backdrop').classList.remove('open');
  $('#backdrop').setAttribute('aria-hidden', 'true');
}
const actions = (html) => `<div class="modal-actions">${html}</div>`;
function details(id) {
  const b = {
    'status-detalhes': statusModalHTML, 'glossario-detalhes': glossaryHTML, 'lotes-detalhes': batchHTML, 'simulacao-detalhes': simulationHTML,
    'revisao-detalhes': reviewHTML, 'revisao-expandida': expandedHTML, 'como-instalar-detalhes': installHTML, 'cancelar-detalhes': cancelHTML, 'cota-esgotada-detalhes': quotaHTML}[id];
  if (!b) return;
  showModal(id, b());
  if (id === 'status-detalhes') ui.timer = setInterval(renderStatusModal, 1000);
}

/* status */
function eventLine(d) {
  const t = new Date(d.ts || d.timestamp || Date.now()).toLocaleTimeString('pt-BR');
  const text = {batch_started: `lote ${d.index} enviado - ${d.segments ?? '?'} segmentos`, batch_completed: `lote ${d.index} ok`,
    cache_summary: `cache: ${d.exact ?? 0} acertos exatos`, retry: `lote ${d.index} nova tentativa ${d.attempt ?? ''}`,
    cancelled: `cancelado no lote ${d.index}`, review_started: `revisão: ${d.count} trechos incertos enviados ao modelo`,
    review_completed: `revisão concluída: ${d.changed} de ${d.count} ajustados`, review_skipped: `revisão ignorada - ${d.message || ''}`, error: `erro - ${d.message || ''}`}[d.event];
  return text ? `${t}  ${text}` : null;
}
function chapterErrors(r, file) {
  if (ui.result?.summary) return ui.result.summary.errors_by_file?.[file] || 0;
  return r.cerr?.[file] || 0;
}
function statusModalHTML() {
  const insp = ui.inspect, cachedNow = (insp?.chapters || []).reduce((n, c) => n + Math.min(c.total, c.cached || 0), 0);
  const r = ui.run || ui.finished || {total: insp?.segments || 0, done: cachedNow, cache: (insp?.exact_cache || 0) + (insp?.cross_cache || 0),
    chapters: insp?.chapters || [], cdone: {}, elapsed: 0};
  const avg = r.batches ? Math.round(r.elapsed / r.batches) : null, eta = ui.run ? runEta(r) : null;
  const done = ui.st.kind === 'done';
  const rows = (r.chapters || []).map(c => {
    const errs = chapterErrors(r, c.file), d = done ? c.total - errs : Math.min(c.total, Math.max(r.cdone?.[c.file] || 0, c.cached || 0)), pct = c.total ? Math.round((errs ? d : d + errs) * 100 / c.total) : 0;
    let label = d + errs >= c.total && c.total ? 'Concluído' : (ui.run && r.active === c.file ? 'Traduzindo' : d > 0 ? 'Parcial' : 'Na fila');
    let cell = `<span class="st${label === 'Traduzindo' ? ' on' : ''}">${label}</span>`;
    if (errs) cell = ui.run ? '<span class="st err">Com erro</span>' : `<span class="st err"><button type="button" class="link has-tip" data-retry="${esc(c.file)}" data-tip="${errs} segmento(s) com erro neste capítulo: só eles serão traduzidos de novo; o resto vem do cache">Retraduzir</button></span>`;
    return `<div class="ch"><span>${esc(base(c.file))}</span>${cell}<div class="progress ${errs ? 'red' : label === 'Concluído' ? 'green' : 'blue'} thin"><i style="width:${pct}%"></i></div><span class="cnt">${d}/${c.total}</span></div>`;
  }).join('') || '<p class="muted">Carregue um EPUB para ver os capítulos.</p>';
  const lines = ui.log.map(eventLine).filter(Boolean).slice(-3).reverse().map(l => `<div>${esc(l)}</div>`).join('') || '<div>Os eventos aparecerão aqui durante a tradução.</div>';
  return `<div class="m4"><div class="mm"><strong>${fmt(done ? r.total : r.done)}/${fmt(r.total)}</strong><span>segmentos</span></div><div class="mm"><strong>${fmt(r.cache)}</strong><span>acertos de cache</span></div><div class="mm"><strong>${avg ? avg + ' s' : '—'}</strong><span>por lote</span></div><div class="mm"><strong>${eta ? `~${eta} min` : '—'}</strong><span>restante</span></div></div><hr class="divider"><h2>Capítulos</h2><div class="chapters modal-body">${rows}</div><div class="log-strip">${lines}</div><hr class="divider"><div class="status-row plain" id="modal-status">${statusHTML(false)}</div>`;
}
function renderStatusModal() { if (ui.modal === 'status-detalhes' && document.querySelector('.tip')?.hidden !== false) { $('#modal-content').innerHTML = statusModalHTML(); renderUpdatedAt(); } }

/* glossary */
function chipHTML(v) { return `<span class="chip"><span class="chip-text">${esc(v)}</span><button type="button" class="remove" aria-label="Remover">×</button></span>`; }
function termRow(a = '', b = '') { return `<div class="term-row"><input value="${esc(a)}" placeholder="Original"><input value="${esc(b)}" placeholder="Tradução"><button type="button" class="remove" aria-label="Remover">×</button></div>`; }
function glossaryHTML() {
  const g = ui.state.glossary;
  return `<div class="modal-body"><div class="modal-cols"><section><h2>Mapa de tradução - translate_map</h2><div id="terms">${Object.entries(g.translate_map || {}).map(([a, b]) => termRow(a, b)).join('')}</div><button type="button" class="link" id="add-term">Adicionar termo</button></section>
<section><h2>Manter no original - keep_in_source</h2><div class="chips" id="keep-chips">${(g.keep_in_source || []).map(chipHTML).join('')}</div><div class="chip-add"><input id="keep-input" placeholder="Adicionar termo"><button type="button" class="link" data-chip="keep">Adicionar</button></div>
<h2>Nunca traduzir - never_translate</h2><div class="chips" id="never-chips">${(g.never_translate || []).map(chipHTML).join('')}</div><div class="chip-add"><input id="never-input" placeholder="Adicionar termo"><button type="button" class="link" data-chip="never">Adicionar</button></div></section></div></div>${actions('<button class="primary" id="save-glossary">Salvar glossário</button>')}`;
}
function addChip(kind) {
  const input = $(`#${kind}-input`), v = input.value.trim();
  if (v) { $(`#${kind}-chips`).insertAdjacentHTML('beforeend', chipHTML(v)); input.value = ''; }
}
async function saveGlossary() {
  const map = {};
  document.querySelectorAll('#terms .term-row').forEach(row => { const [a, b] = row.querySelectorAll('input'); if (a.value.trim()) map[a.value.trim()] = b.value.trim(); });
  const chips = (id) => [...document.querySelectorAll(`#${id} .chip-text`)].map(x => x.textContent.trim());
  const body = {translate_map: map, keep_in_source: chips('keep-chips'), never_translate: chips('never-chips'), main_content_only: $('#main-only').checked};
  await put('/api/glossary', body);
  Object.assign(ui.state.glossary, body);
  updateTerms(); hideModal(); reinspect();
}

/* batches */
function batchInfo(mode) {
  const b = ui.inspect?.batches?.[mode];
  if (b) return b;
  const p = providerList()[ui.provider] || {}, m = {less: 2, default: 1, more: .5}[mode];
  return {segments: Math.max(1, Math.floor((p.batch_segments || 60) * m)), words: Math.max(1, Math.floor((p.batch_words || 2000) * m)), calls: null};
}
function batchHTML() {
  ui.batchSel = ui.state.preferences.batch_mode || 'default';
  return `<h2 class="big">Tamanho dos lotes</h2><div class="modal-body" id="batch-cards">${batchCards()}</div>${actions('<button class="primary" id="save-batch">Aplicar</button>')}`;
}
function batchCards() {
  return BATCH_MODES.map(([v, t, tag, desc]) => {
    const i = batchInfo(v), sel = v === ui.batchSel;
    return `<div class="rcard${sel ? ' sel' : ''}" role="radio" aria-checked="${sel}" tabindex="0" data-mode="${v}"><span class="dot"></span><div><div class="rc-head"><b>${t}</b><span>${tag}</span></div><p>${desc}</p><p class="meta">${fmt(i.segments)} segmentos - ${fmt(i.words)} palavras - ~${i.calls == null ? '—' : fmt(i.calls)} chamadas</p></div></div>`;
  }).join('');
}
async function applyBatch() {
  ui.state.preferences = await put('/api/preferences', {batch_mode: ui.batchSel});
  updateBatchSummary(); hideModal(); reinspect();
}

/* simulation / review */
function simulationHTML() {
  const i = ui.inspect;
  const body = i ? `<p class="muted">Estimativa local, sem chamar o modelo.</p><div class="m3"><div class="mm"><strong>${fmt(i.segments)}</strong><span>segmentos</span></div><div class="mm"><strong>${fmt(i.words)}</strong><span>palavras</span></div><div class="mm"><strong>~${fmt(i.calls)}</strong><span>chamadas estimadas</span></div></div><hr class="divider"><h2>Cache</h2><p>${fmt(i.exact_cache)} exatos - ${fmt(i.cross_cache)} de outros modelos</p><p class="muted">Restam ~${fmt(i.pending)} segmentos para traduzir</p>` : '<p class="muted">Carregue um EPUB para simular.</p>';
  return `<h2 class="big">Simulação</h2><div class="modal-body">${body}</div>${actions(`<button class="primary" data-action="start"${i ? '' : ' disabled'}>Iniciar tradução</button>`)}`;
}
const sevLabel = (s) => ({WARN: 'Aviso', BLOCK: 'Bloqueio', INFO: 'Info'}[s] || s);
function findingHTML(f) { return `<div class="card-mini"><b class="orange-label">${esc(sevLabel(f.severity))}</b><small>${esc(base(f.file))}</small><p>${esc(f.detail || f.check || '')}</p></div>`; }
function segIndex(s) { return s.n; }
function reviewHTML() {
  const {u, w} = reviewCounts(), finds = (ui.result?.findings || []).slice(0, 2), unc = (ui.result?.segments || []).filter(s => s.status === 'uncertain').slice(0, 2);
  const cards = finds.map(findingHTML).join('') + unc.map(s => `<div class="card-mini"><b class="orange-label">Incerto</b><small>seg ${segIndex(s)}</small><p>${esc(s.source)}</p></div>`).join('');
  return `<h2 class="big">Revisar</h2><p class="${u || w ? 'orange-label' : 'muted'}">${u} incertos - ${w} avisos</p><div class="modal-body">${cards || '<p class="muted">Nenhum achado disponível ainda. Execute uma tradução para revisar.</p>'}</div>${actions('<button class="primary" data-open="revisao-expandida">Abrir Revisão e QA</button>')}`;
}
function filteredSegments() { return ui.segView && ui.segView.filter === ui.filter ? ui.segView.segments : null; }
async function loadView(filter) {
  ui.filter = filter;
  if (!ui.segView || ui.segView.filter !== filter) {
    if (ui.modal === 'revisao-expandida') $('#modal-content').innerHTML = expandedHTML();
    const r = await api(`/api/jobs/${ui.job}/result?filter=${filter}&limit=300`);
    ui.segView = {filter, segments: r.segments || []};
  }
  if (ui.modal === 'revisao-expandida') $('#modal-content').innerHTML = expandedHTML();
}
function expandedHTML() {
  const finds = ui.result?.findings || [], has = Boolean(ui.result?.stats);
  const u = ui.result?.summary?.uncertain || 0, view = filteredSegments();
  const pills = [['uncertain', `incertos (${u})`], ['warn', 'avisos'], ['all', 'todos']].map(([v, t]) => `<button type="button" class="fpill${ui.filter === v ? ' on' : ''}" data-filter="${v}">${t}</button>`).join('');
  const rows = view === null ? '<p class="muted">Carregando…</p>' : view.slice(0, 300).map(s => `<div class="seg-row"><div><p>${esc(s.source)}</p><small class="${s.status === 'uncertain' ? '' : 'neutral'}">${s.status === 'uncertain' ? 'incerto' : esc(s.status)} - seg ${segIndex(s)}</small></div><div><p>${esc(s.translation || '')}</p></div></div>`).join('')
    || `<p class="muted">${has ? 'Nenhum segmento para este filtro.' : 'Nenhuma tradução concluída ainda.'}</p>`;
  return `<div class="modal-body"><div class="modal-cols" style="grid-template-columns:1fr 2fr"><section><h2>Achados de QA</h2>${finds.slice(0, 6).map(findingHTML).join('') || '<p class="muted">Nenhum achado.</p>'}<button type="button" class="link" id="report-link"${has ? '' : ' disabled'}>Ver todos no relatório</button></section>
<section><h2>Revisão de segmentos</h2><div class="filters">${pills}</div><div class="seg-head"><span>Original</span><span>Tradução</span></div><div id="segment-list">${rows}</div></section></div></div>${actions(`<button class="secondary" id="download-copy"${has ? '' : ' disabled'}>Salvar cópia…</button><button class="primary" id="open-folder"${has ? '' : ' disabled'}>Abrir pasta</button>`)}`;
}

/* install */
function installHTML() {
  const name = ui.installFor || ui.provider, p = providerList()[name] || {};
  return `<h2 class="big">Como instalar</h2><div class="modal-body"><p><b>${esc(name)}</b> - <span class="vstatus${p.installed ? '' : ' bad'}" id="verify-status">${p.installed ? `instalado - ${esc(p.path)}` : 'não encontrado'}</span></p><p class="muted" id="install-provider">${esc(INSTALL[name] || 'Instale a CLI do provedor e entre na sua conta.')}</p><label class="field" id="install-bin">Binário<input id="binary-path" value="${esc(p.binary || name)}" placeholder="C:\\caminho\\para\\executavel.exe"></label><p>Modelos sugeridos: <span class="muted" id="suggested-models">${esc((p.models || []).join(', '))}</span></p></div>${actions('<button class="primary" id="verify-binary">Verificar de novo</button>')}`;
}
async function verifyBinary() {
  const name = ui.installFor || ui.provider;
  const r = await post('/api/providers/verify', {provider: name, binary: $('#binary-path').value});
  const s = (await api('/api/state')).providers;
  ui.state.providers = s; renderProviders(); renderModels();
  if (r.installed) hideModal();
  else { const el = $('#verify-status'); if (el) { el.textContent = 'não encontrado'; el.classList.add('bad'); } }
}

/* quota */
function quotaHTML() {
  const q = ui.quota || {}, others = Object.entries(providerList()).filter(([, p]) => p.installed);
  ui.q = ui.q || {provider: others.find(([n]) => n !== q.provider)?.[0] || ui.provider, model: ''};
  const qp = providerList()[ui.q.provider], models = qp?.models || [];
  if (!models.includes(ui.q.model)) ui.q.model = models[0] || '';
  const rows = others.map(([n, p]) => `<div class="prow${n === ui.q.provider ? ' sel' : ''}${ui.exhausted[n] ? ' quota' : ''}" role="radio" tabindex="0" data-qp="${esc(n)}"><span>${esc(n)}</span><span class="right">${ui.exhausted[n] ? '<span class="missing">cota esgotada</span>' : ''}</span></div>`).join('');
  const pills = models.map(m => `<button type="button" class="mpill${m === ui.q.model ? ' sel' : ''}" data-qm="${esc(m)}">${esc(m)}</button>`).join('');
  const down = q.reason === 'unavailable';
  const note = down ? `<b>${esc(q.engine || 'O modelo atual')}</b> não respondeu em vários lotes seguidos${q.message ? ` (${esc(q.message)})` : ''}. A tradução parou para não gastar tempo e cota à toa. O que já foi traduzido está salvo no cache e será reaproveitado.`
    : `<b>${esc(q.engine || 'O modelo atual')}</b> atingiu o limite de uso${q.reset ? ` (volta em ${esc(q.reset)})` : ''}. A tradução parou. O que já foi traduzido está salvo no cache e será reaproveitado.`;
  return `<h2 class="big">${down ? 'Motor indisponível' : 'Cota esgotada'}</h2><div class="modal-body"><p class="quota-note">${note}</p><div class="field-label">Continuar com</div><div class="provider-list q-block">${rows}</div><div class="model-list q-block">${pills}</div><div class="field fallback">Se a cota acabar de novo<div class="dd" data-dd="fallback-modal">${ddHTML(fallbackList(), ui.fallback)}</div></div></div>${actions('<button class="secondary" id="quota-close">Agora não</button><button class="primary" id="quota-continue">Continuar tradução</button>')}`;
}
function quotaContinue() {
  ui.provider = ui.q.provider; ui.model = ui.q.model; ui.other = false; $('#custom-model').value = '';
  renderProviders(); renderModels(); hideModal();
  const input = ui.state?.last_job?.input;
  if (input) startJob(input).catch(fail);
}

/* cancel */
function cancelHTML() {
  const r = ui.run || {done: 0, total: 0, batchIdx: 0, batchTotal: 0}, pct = runPct(r);
  return `<h2 class="big">Cancelar tradução?</h2><p class="muted">Lote ${r.batchIdx} de ${r.batchTotal} - ${fmt(r.done)}/${fmt(r.total)} segmentos. O que já foi traduzido fica salvo e você pode retomar de onde parou.</p><div class="progress blue"><i style="width:${pct}%"></i></div><p style="margin-top:8px">${pct}%</p>${actions('<button class="secondary" id="continue-job">Continuar traduzindo</button><button class="primary" id="confirm-cancel">Cancelar tradução</button>')}`;
}
async function confirmCancel() {
  await post(`/api/jobs/${ui.job}/cancel`);
  hideModal();
  if (ui.run) { ui.run.cancelRequested = true; setStatus(stoppedStatus(ui.run)); }
}

/* ---------- wiring ---------- */
const openFolder = () => { const path = ui.result?.stats?.output_path || ui.state?.last_job?.output; if (path) post('/api/open-folder', {path}).catch(fail); };
document.addEventListener('click', (e) => {
  const t = e.target;
  const ddb = t.closest('.dd-btn');
  if (ddb) { const menu = ddb.nextElementSibling, open = menu.hidden; closeDDs(); menu.hidden = !open; ddb.setAttribute('aria-expanded', String(open)); if (open) placeMenu(menu, ddb); return; }
  const ddo = t.closest('.dd-opt');
  if (ddo) { const name = ddo.closest('.dd').dataset.dd; closeDDs(); pickDD(name, ddo.dataset.v); return; }
  closeDDs();
  const inst = t.closest('[data-install]');
  if (inst) { e.stopPropagation(); ui.installFor = inst.dataset.install; details('como-instalar-detalhes'); return; }
  const qp = t.closest('[data-qp]'); if (qp) { ui.q.provider = qp.dataset.qp; ui.q.model = ''; $('#modal-content').innerHTML = quotaHTML(); return; }
  const qm = t.closest('[data-qm]'); if (qm) { ui.q.model = qm.dataset.qm; $('#modal-content').innerHTML = quotaHTML(); return; }
  const retry = t.closest('[data-retry]'); if (retry) { if (!ui.run && ui.state?.last_job?.input) { hideModal(); startJob(ui.state.last_job.input, {retry_files: [retry.dataset.retry], retry_errors: true}).catch(fail); } return; }
  const prow = t.closest('.prow'); if (prow) { selectProvider(prow.dataset.p); return; }
  const mp = t.closest('.mpill'); if (mp) { selectModel(mp.dataset.m); return; }
  const open = t.closest('[data-open]');
  if (open) { e.preventDefault(); if (open.dataset.open === 'como-instalar-detalhes') ui.installFor = null; details(open.dataset.open); return; }
  if (t.closest('[data-action=start]')) { hideModal(); startJob().catch(fail); return; }
  const act = t.closest('[data-act]');
  if (act) {
    e.stopPropagation();
    if (act.dataset.act === 'cancel') details('cancelar-detalhes');
    else if (act.dataset.act === 'resume') {
      if (ui.quota?.model && ui.quota.provider === ui.provider && ui.quota.model === currentModel()) { ui.q = null; details('cota-esgotada-detalhes'); return; }
      if (ui.state?.last_job?.input) { if (ui.modal) hideModal(); startJob(ui.state.last_job.input).catch(fail); } }
    else if (act.dataset.act === 'open') openFolder();
    return;
  }
  if (t === $('#backdrop') || t === $('#stage')) { hideModal(); return; }
  if (!ui.modal) return;
  // delegated modal handlers (work for every click, no once-only listeners)
  if (t.closest('#modal')) {
    if (t.matches('.remove')) t.closest('.term-row,.chip')?.remove();
    else if (t.closest('#add-term')) { $('#terms').insertAdjacentHTML('beforeend', termRow()); $('#terms .term-row:last-child input').focus(); }
    else if (t.closest('[data-chip]')) addChip(t.closest('[data-chip]').dataset.chip);
    else if (t.closest('#save-glossary')) saveGlossary().catch(fail);
    else if (t.closest('[data-mode]')) { ui.batchSel = t.closest('[data-mode]').dataset.mode; $('#batch-cards').innerHTML = batchCards(); }
    else if (t.closest('#save-batch')) applyBatch().catch(fail);
    else if (t.closest('#verify-binary')) verifyBinary().catch(fail);
    else if (t.closest('#quota-continue')) quotaContinue();
    else if (t.closest('#quota-close')) hideModal();
    else if (t.closest('#continue-job')) hideModal();
    else if (t.closest('#confirm-cancel')) confirmCancel().catch(fail);
    else if (t.closest('[data-filter]')) loadView(t.closest('[data-filter]').dataset.filter).catch(fail);
    else if (t.closest('#download-copy')) window.open(`/api/jobs/${ui.job}/download`);
    else if (t.closest('#open-folder')) openFolder();
    else if (t.closest('#report-link')) window.open(`/api/jobs/${ui.job}/report`);
  }
});
window.addEventListener('resize', closeDDs);
// tooltip sempre abaixo do elemento (posição fixa: não é cortado pelo card)
const tip = document.createElement('div');
tip.className = 'tip'; tip.hidden = true; document.body.appendChild(tip);
function showTip(el) {
  tip.textContent = el.dataset.tip; tip.hidden = false;
  const r = el.getBoundingClientRect(), width = Math.min(Math.max(r.width, 260), innerWidth - 16);
  tip.style.width = `${width}px`;
  Object.assign(tip.style, {left: `${Math.max(8, Math.min(r.left, innerWidth - width - 8))}px`, top: `${Math.max(8, Math.min(r.bottom + 8, innerHeight - 8 - tip.offsetHeight))}px`});
}
document.addEventListener('mouseover', (e) => { const el = e.target.closest?.('.has-tip'); if (el) showTip(el); else tip.hidden = true; });
document.addEventListener('focusin', (e) => { const el = e.target.closest?.('.has-tip'); if (el) showTip(el); });
document.addEventListener('focusout', () => { tip.hidden = true; });
document.addEventListener('scroll', () => { tip.hidden = true; }, true);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { if (document.querySelector('.dd-menu:not([hidden])')) { closeDDs(); return; } hideModal(); return; }
  if (e.key === 'Enter' && e.target.matches?.('#keep-input,#never-input')) { e.preventDefault(); addChip(e.target.id.replace('-input', '')); return; }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('.prow,[data-mode]')) { e.preventDefault(); e.target.click(); }
});
$('#choose').onclick = (e) => { e.preventDefault(); e.stopPropagation(); $('#file').click(); };
const resetIdle = () => { if (!ui.run) { ui.finished = null; setStatus({kind: 'idle', pct: 0, parts: ['Pronto para iniciar']}); } };
$('#file').onchange = (e) => { ui.file = e.target.files[0]; ui.bookPath = null; resetIdle(); reinspect(); };
$('#dropzone').ondragover = (e) => e.preventDefault();
$('#dropzone').ondrop = (e) => { e.preventDefault(); ui.file = e.dataTransfer.files[0]; ui.bookPath = null; resetIdle(); reinspect(); };
$('#custom-model').onchange = reinspect;
$('#source-lang').onchange = () => { $('#source-lang').dataset.edited = 'true'; reinspect(); };
$('#target-lang').onchange = reinspect;
$('#reuse-cache').onchange = reinspect;
$('#review-uncertain').onchange = () => put('/api/preferences', {review_uncertain: $('#review-uncertain').checked}).catch(fail);
$('#main-only').onchange = async () => { await put('/api/glossary', {main_content_only: $('#main-only').checked}).catch(fail); reinspect(); };
$('#start').onclick = () => startJob().catch(fail);
$('#simulate').onclick = async () => { try { if (ui.file || ui.bookPath) await inspectFile(); } catch (e) { fail(e); } details('simulacao-detalhes'); };
$('#status-row').onclick = (e) => { if (!e.target.closest('button')) details('status-detalhes'); };
$('#status-row').onkeydown = (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === $('#status-row')) { e.preventDefault(); details('status-detalhes'); } };
fillLangs();
setStatus(ui.st);
refresh().then(fetchUsage).then(() => fetchOfficial()).catch(fail);
setInterval(tickExhausted, 30000);
setInterval(() => { if (ui.officialOn) fetchOfficial(); }, 120000);
$('#official-usage').onchange = async () => {
  const on = $('#official-usage').checked;
  await put('/api/preferences', {official_usage: on}).catch(fail);
  if (on) fetchOfficial(true); else { ui.officialOn = false; ui.official = {}; renderProviders(); }
};
