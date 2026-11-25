/* netriage 前端：真实测量 + 诊断渲染。
 * 测量全部走同源接口：/api/ping 取 RTT，/api/download 拉字节，/api/upload 推字节。
 * 不依赖任何第三方测速服务，部署在哪台机器就测到哪台机器。
 */
'use strict';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DEMO_DIAL = [
  'Nov 25 19:02:11 OpenWrt daemon.notice pppd[2841]: Connect: pppoe-wan <--> eth0',
  'Nov 25 19:02:11 OpenWrt daemon.info pppd[2841]: sent [PADI Host-Uniq 0x0b2a]',
  'Nov 25 19:02:12 OpenWrt daemon.info pppd[2841]: rcvd [PADO]',
  'Nov 25 19:02:12 OpenWrt daemon.notice pppd[2841]: local  IP address 100.64.18.77',
  'Nov 25 20:41:07 OpenWrt daemon.notice pppd[2841]: Modem hangup',
  'Nov 25 20:41:07 OpenWrt daemon.notice pppd[2841]: Connection terminated.',
  'Nov 25 20:41:14 OpenWrt daemon.err pppd[2841]: LCP: timeout sending Config-Requests',
  'Nov 25 21:15:02 OpenWrt daemon.notice pppd[2841]: Modem hangup',
].join('\n');

const DEMO_LQC = 'RSRP=-96 RSRQ=-11 SINR=17 band=5G rx_power=-24.6';

const state = {
  measure: {},
  result: null,
  running: false,
  abort: null,
  chart: [],
};

const DEMO_MEASURE = {
  down_mbps: 318.4,
  up_mbps: 61.2,
  down_single_mbps: 88.6,
  down_multi_mbps: 318.4,
  latency_idle_ms: 18.4,
  latency_loaded_ms: 322.0,
  bufferbloat_ms: 303.6,
  jitter_ms: 9.2,
  loss_pct: 0.8,
};

/* ---------------- 基础 ---------------- */

async function api(path, options) {
  const res = await fetch(path, Object.assign({ cache: 'no-store' }, options || {}));
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}

function num(id, fallback) {
  const value = parseFloat($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function fmt(value, digits) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value.toFixed(digits === undefined ? 1 : digits);
}

function setMetric(id, value, digits, cls) {
  const el = $(id);
  el.textContent = fmt(value, digits);
  el.className = cls || '';
}

function setStage(text) { $('stage').textContent = text; }
function setProgress(pct) { $('bar').style.width = Math.max(0, Math.min(100, pct)) + '%'; }

/* ---------------- 图表 ---------------- */

const canvas = $('chart');
const ctx = canvas.getContext('2d');

function fitCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = 180;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  drawChart();
}

function drawChart() {
  const width = canvas.clientWidth;
  const height = 180;
  ctx.clearRect(0, 0, width, height);

  const pad = 10;
  const points = state.chart;
  const values = points.map((p) => p.v);
  const maxValue = Math.max(10, ...values) * 1.15;

  ctx.strokeStyle = 'rgba(139,162,174,.16)';
  ctx.lineWidth = 1;
  for (let i = 1; i <= 3; i += 1) {
    const y = pad + (height - pad * 2) * (i / 4);
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }

  if (points.length < 2) {
    ctx.fillStyle = 'rgba(139,162,174,.45)';
    ctx.font = '12px ui-monospace, monospace';
    ctx.fillText('等待测量数据…', pad + 4, height / 2);
    return;
  }

  const stepX = (width - pad * 2) / Math.max(points.length - 1, 1);
  const mapY = (v) => height - pad - (height - pad * 2) * Math.min(v / maxValue, 1);

  ctx.beginPath();
  points.forEach((p, i) => {
    const x = pad + stepX * i;
    const y = mapY(p.v);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = 'rgba(45,212,191,.9)';
  ctx.lineWidth = 1.6;
  ctx.stroke();

  ctx.lineTo(pad + stepX * (points.length - 1), height - pad);
  ctx.lineTo(pad, height - pad);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad, 0, height - pad);
  grad.addColorStop(0, 'rgba(45,212,191,.20)');
  grad.addColorStop(1, 'rgba(45,212,191,.02)');
  ctx.fillStyle = grad;
  ctx.fill();

  const last = points[points.length - 1];
  ctx.fillStyle = 'rgba(231,240,244,.85)';
  ctx.font = '12px ui-monospace, monospace';
  ctx.fillText(last.v.toFixed(1) + ' Mbps', width - pad - 74, pad + 12);
}

function pushSample(value) {
  state.chart.push({ v: Math.max(0, value), t: performance.now() });
  if (state.chart.length > 400) state.chart.shift();
  drawChart();
}

/* ---------------- 测量原语 ---------------- */

async function pingOnce() {
  const started = performance.now();
  const res = await fetch('/api/ping?r=' + Math.random(), { cache: 'no-store' });
  await res.arrayBuffer();
  return performance.now() - started;
}

async function idleLatency(count) {
  const samples = [];
  let fail = 0;
  for (let i = 0; i < count; i += 1) {
    try {
      samples.push(await pingOnce());
    } catch (err) {
      fail += 1;
    }
    await sleep(40);
  }
  if (!samples.length) return { avg: null, jitter: null, loss: null };
  const avg = samples.reduce((a, b) => a + b, 0) / samples.length;
  let jitter = 0;
  for (let i = 1; i < samples.length; i += 1) jitter += Math.abs(samples[i] - samples[i - 1]);
  jitter /= Math.max(samples.length - 1, 1);
  return { avg, jitter, loss: (fail / count) * 100, min: Math.min(...samples) };
}

async function downloadWorker(deadline, onBytes, signal) {
  for (let guard = 0; guard < 30; guard += 1) {
    if (performance.now() >= deadline || signal.aborted) return;
    let res;
    try {
      res = await fetch('/api/download?mb=24&r=' + Math.random(), { cache: 'no-store', signal });
    } catch (err) {
      return;
    }
    if (!res.body) {
      try { const buf = await res.arrayBuffer(); onBytes(buf.byteLength); } catch (err) { return; }
      continue;
    }
    const reader = res.body.getReader();
    try {
      while (true) {
        if (performance.now() >= deadline || signal.aborted) {
          try { await reader.cancel(); } catch (err) { /* 已结束 */ }
          return;
        }
        const chunk = await reader.read();
        if (chunk.done) break;
        onBytes(chunk.value.byteLength);
      }
    } catch (err) {
      return;
    }
  }
}

async function runDownload(streams, seconds, signal) {
  const started = performance.now();
  const deadline = started + seconds * 1000;
  let total = 0;
  let lastBytes = 0;
  const lastAt = { v: started };
  const onBytes = (n) => { total += n; };

  const timer = setInterval(() => {
    const now = performance.now();
    const inst = ((total - lastBytes) * 8 / 1e6) / Math.max((now - lastAt.v) / 1000, 0.001);
    lastBytes = total;
    lastAt.v = now;
    pushSample(inst);
  }, 250);

  const workers = [];
  for (let i = 0; i < streams; i += 1) workers.push(downloadWorker(deadline, onBytes, signal));
  await Promise.all(workers);
  clearInterval(timer);

  const elapsed = (performance.now() - started) / 1000;
  return { mbps: (total * 8 / 1e6) / Math.max(elapsed, 0.001), bytes: total };
}

function makeBlob(megabytes) {
  const chunk = 65536;
  const count = Math.floor(megabytes * 1024 * 1024 / chunk);
  const parts = [];
  for (let i = 0; i < count; i += 1) {
    const buf = new Uint8Array(chunk);
    crypto.getRandomValues(buf);
    parts.push(buf);
  }
  return new Blob(parts, { type: 'application/octet-stream' });
}

function uploadOnce(blob, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?r=' + Math.random());
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.onload = () => resolve(xhr.status);
    xhr.onerror = () => reject(new Error('upload error'));
    xhr.onabort = () => reject(new Error('upload abort'));
    if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(blob);
  });
}

async function runUpload(streams, seconds, signal) {
  const blob = makeBlob(4);
  const started = performance.now();
  const deadline = started + seconds * 1000;
  let bytes = 0;

  const workers = [];
  for (let i = 0; i < streams; i += 1) {
    workers.push((async () => {
      while (performance.now() < deadline && !signal.aborted) {
        try {
          const status = await uploadOnce(blob, signal);
          if (status !== 200) return;
          bytes += blob.size;
        } catch (err) {
          return;
        }
      }
    })());
  }
  await Promise.all(workers);
  const elapsed = (performance.now() - started) / 1000;
  return { mbps: (bytes * 8 / 1e6) / Math.max(elapsed, 0.001), bytes };
}

async function probeWhile(running, signal) {
  const values = [];
  let done = false;
  running.then(() => { done = true; }).catch(() => { done = true; });
  while (!done && !signal.aborted) {
    try { values.push(await pingOnce()); } catch (err) { /* 忽略单次失败 */ }
    await sleep(350);
  }
  return values;
}

/* ---------------- 测速主流程 ---------------- */

async function startTest() {
  if (state.running) return;
  state.running = true;
  state.abort = new AbortController();
  state.chart = [];
  drawChart();

  $('btnStart').disabled = true;
  $('btnStop').disabled = false;
  $('btnDiag').disabled = true;

  const signal = state.abort.signal;
  const seconds = parseInt($('duration').value, 10);
  const planDown = num('planDown', 0);
  const planUp = num('planUp', 0);
  const measure = {};

  try {
    setProgress(4);
    setStage('① 测空闲延迟（10 次探测）');
    const idle = await idleLatency(10);
    if (signal.aborted) throw new Error('aborted');
    measure.latency_idle_ms = idle.avg;
    measure.jitter_ms = idle.jitter;
    measure.loss_pct = idle.loss;
    setMetric('mLatency', idle.avg, 1);
    setMetric('mJitter', idle.jitter, 1);
    setMetric('mLoss', idle.loss, 2);

    setProgress(25);
    setStage('② 单流下行 ' + seconds + ' 秒');
    const single = await runDownload(1, seconds, signal);
    measure.down_single_mbps = single.mbps;

    setProgress(50);
    setStage('③ 四流下行 + 满载延迟探测');
    const multiRun = runDownload(4, seconds, signal);
    const probes = await probeWhile(multiRun, signal);
    const multi = await multiRun;
    measure.down_multi_mbps = multi.mbps;
    measure.down_mbps = multi.mbps;
    if (probes.length) {
      const tail = probes.slice(Math.min(2, probes.length - 1));
      const loaded = tail.reduce((a, b) => a + b, 0) / tail.length;
      measure.latency_loaded_ms = loaded;
      if (idle.avg !== null) measure.bufferbloat_ms = Math.max(0, loaded - idle.avg);
    }

    setProgress(75);
    setStage('④ 上行 ' + seconds + ' 秒');
    const up = await runUpload(3, seconds, signal);
    measure.up_mbps = up.mbps;

    setProgress(100);
    setStage('⑤ 汇总并诊断');
    state.measure = measure;
    renderMetrics(measure, planDown, planUp);

    const payload = buildPayload();
    const res = await api('/api/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    renderDiag(res.result);
    setStage('完成 · 用时约 ' + Math.round(performance.now() / 1000) + ' 秒');
  } catch (err) {
    if (err && err.message === 'aborted') setStage('已停止');
    else setStage('测量出错：' + (err && err.message ? err.message : String(err)));
  } finally {
    state.running = false;
    $('btnStart').disabled = false;
    $('btnStop').disabled = true;
    $('btnDiag').disabled = false;
  }
}

function renderMetrics(m, planDown, planUp) {
  const ratio = planDown ? (m.down_mbps / planDown) * 100 : null;
  const pick = (value, good, mid) => (value === null || value === undefined ? '' : (value >= good ? 'good' : (value >= mid ? 'mid' : 'low')));
  const pickLow = (value, good, mid) => (value === null || value === undefined ? '' : (value <= good ? 'good' : (value <= mid ? 'mid' : 'low')));

  setMetric('mDown', m.down_mbps, 1, pick(m.down_mbps, planDown * 0.9, planDown * 0.7));
  setMetric('mUp', m.up_mbps, 1, pick(m.up_mbps, planUp * 0.9, planUp * 0.7));
  setMetric('mSingle', m.down_single_mbps, 1);
  setMetric('mLatency', m.latency_idle_ms, 1, pickLow(m.latency_idle_ms, 20, 50));
  setMetric('mJitter', m.jitter_ms, 1, pickLow(m.jitter_ms, 6, 15));
  setMetric('mLoss', m.loss_pct, 2, pickLow(m.loss_pct, 0.5, 2));
  setMetric('mBloat', m.bufferbloat_ms, 0, pickLow(m.bufferbloat_ms, 50, 150));
  setMetric('mRatio', ratio, 0, pick(ratio, 90, 70));
}

/* ---------------- 诊断渲染 ---------------- */

function renderDiag(result) {
  state.result = result;
  const out = $('diagOut');
  const health = result.health;
  const tone = health >= 85 ? 'good' : (health >= 60 ? 'mid' : 'low');

  const causes = result.causes.length
    ? result.causes.map((c) => {
      const level = c.confidence >= 0.6 ? 'high' : (c.confidence >= 0.4 ? 'mid' : 'low');
      const barClass = level === 'low' ? 'low' : '';
      return [
        '<div class="cause ' + level + '">',
        '<div class="cause-top"><strong>' + esc(c.name) + '</strong>',
        '<span>' + esc(c.layer) + ' · 置信 ' + Math.round(c.confidence * 100) + '%</span></div>',
        '<div class="bar"><i class="' + barClass + '" style="width:' + Math.round(c.confidence * 100) + '%"></i></div>',
        '<ul class="ev">' + c.evidence.map((t) => '<li>' + esc(t) + '</li>').join('') + '</ul>',
        '<ul class="acts">' + c.actions.map((t) => '<li>' + esc(t) + '</li>').join('') + '</ul>',
        '</div>',
      ].join('');
    }).join('')
    : '<div class="diag-empty">没有命中任何根因规则，指标看起来正常。</div>';

  const rows = result.checks.map((c) => {
    const value = c.value === null ? '未测' : (c.value + ' ' + c.unit);
    return '<tr><td>' + esc(c.name) + '</td><td class="val">' + esc(value) +
      '</td><td><span class="tag ' + c.status + '">' + statusText(c.status) + '</span></td></tr>';
  }).join('');

  const notes = result.notes && result.notes.length
    ? '<ul class="notes">' + result.notes.map((n) => '<li>' + esc(n) + '</li>').join('') + '</ul>'
    : '';

  out.classList.remove('diag-empty');
  out.innerHTML = [
    '<div class="diag-head">',
    '<div class="health"><div><b class="' + tone + '">' + health + '</b><small>健康分</small></div></div>',
    '<div class="verdict"><strong>' + esc(result.verdict) + '</strong><p>' + esc(result.summary) + '</p></div>',
    '</div>',
    '<div class="causes">' + causes + '</div>',
    '<table class="checks"><thead><tr><th>检查项</th><th>实测</th><th>判定</th></tr></thead><tbody>' + rows + '</tbody></table>',
    notes,
  ].join('');
}

function statusText(status) {
  return { ok: '正常', warn: '提示', crit: '严重', unknown: '未测' }[status] || status;
}

function esc(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ---------------- 载荷与工单 ---------------- */

function buildPayload() {
  return {
    plan: { down: num('planDown', 0), up: num('planUp', 0), type: 'FTTH' },
    measure: state.measure,
    wifi: {
      rssi: num('wRssi', null),
      snr: num('wSnr', null),
      retry_pct: num('wRetry', null),
      channel_util: num('wUtil', null),
      tx_rate: num('wRate', null),
      band: $('wBand').value || null,
    },
    dial_log: $('dialLog').value,
    lqc: $('lqc').value,
    account: $('account').value,
    address: $('address').value,
    isp: $('isp').value,
    contact: $('contact').value,
  };
}

async function makeTicket() {
  const payload = buildPayload();
  if (!payload.dial_log) payload.dial_log = '';
  const body = Object.assign({}, payload, { result: state.result });
  const res = await api('/api/ticket', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  $('ticket').textContent = res.ticket;
  $('btnCopy').disabled = false;
  $('btnDownload').disabled = false;
}

function copyTicket() {
  const text = $('ticket').textContent;
  if (!text) return;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => setStage('工单已复制到剪贴板'));
  } else {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); setStage('工单已复制'); } catch (err) { setStage('复制失败，请手动选择文本'); }
    area.remove();
  }
}

function downloadTicket() {
  const text = $('ticket').textContent;
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'netriage-报障单-' + new Date().toISOString().slice(0, 10) + '.txt';
  a.click();
  URL.revokeObjectURL(url);
}

/* ---------------- 事件绑定 ---------------- */

function bind() {
  $('btnStart').addEventListener('click', startTest);
  $('btnStop').addEventListener('click', () => {
    if (state.abort) state.abort.abort();
  });

  $('btnDiag').addEventListener('click', async () => {
    try {
      const res = await api('/api/diagnose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      renderDiag(res.result);
    } catch (err) {
      setStage('诊断失败：' + err.message);
    }
  });

  $('btnDiagDemo').addEventListener('click', async () => {
    $('dialLog').value = DEMO_DIAL;
    $('lqc').value = DEMO_LQC;
    $('wRssi').value = -71;
    $('wSnr').value = 19;
    $('wRetry').value = 12;
    $('wUtil').value = 57;
    $('wRate').value = 780;
    $('wBand').value = '5G';
    state.measure = Object.assign({}, DEMO_MEASURE);
    renderMetrics(state.measure, num('planDown', 0), num('planUp', 0));
    try {
      const res = await api('/api/diagnose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      renderDiag(res.result);
    } catch (err) {
      setStage('示例诊断失败：' + err.message);
    }
  });

  $('btnTicket').addEventListener('click', () => {
    makeTicket().catch((err) => setStage('工单生成失败：' + err.message));
  });
  $('btnCopy').addEventListener('click', copyTicket);
  $('btnDownload').addEventListener('click', downloadTicket);

  document.querySelectorAll('[data-demo]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.demo === 'dial') $('dialLog').value = DEMO_DIAL;
      if (btn.dataset.demo === 'lqc') $('lqc').value = DEMO_LQC;
    });
  });

  $('fileDial').addEventListener('change', (event) => readInto(event, 'dialLog'));
  $('fileLqc').addEventListener('change', (event) => readInto(event, 'lqc'));

  window.addEventListener('resize', fitCanvas);
}

function readInto(event, targetId) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => { $(targetId).value = String(reader.result || ''); };
  reader.readAsText(file);
}

async function boot() {
  fitCanvas();
  try {
    const meta = await api('/api/meta');
    $('metaVer').textContent = 'v' + meta.version;
    $('metaHost').textContent = meta.host || 'local';
  } catch (err) { /* 元信息失败不影响使用 */ }
  bind();
}

boot();
