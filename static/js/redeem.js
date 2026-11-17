/* ==================== 掃碼核銷頁 ==================== */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const CSRF = window.__CSRF__ || '';

  function authFetch(url, opts) {
    opts = opts || {};
    const headers = Object.assign(
      { 'Content-Type': 'application/json' }, opts.headers || {}
    );
    if (CSRF) headers['X-CSRF-Token'] = CSRF;
    return fetch(url, Object.assign({}, opts, { headers, credentials: 'same-origin' }));
  }

  // ---- 元素 ----
  const cam = $('cam');
  const camWrap = document.querySelector('.rdm-cam-wrap');
  const canvas = $('frame-canvas');
  const startBtn = $('start-btn');
  const stopBtn = $('stop-btn');
  const scanStatus = $('scan-status');
  const resultEl = $('result');
  const scannerCard = $('scanner-card');

  // ---- 相機 / 掃描狀態 ----
  let stream = null;
  let scanning = false;
  let rafId = null;

  function setStatus(msg, isErr) {
    scanStatus.textContent = msg || '';
    scanStatus.classList.toggle('err', !!isErr);
  }

  async function startScan() {
    hideResult();
    setStatus('正在開啟相機…');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus('此瀏覽器不支援相機，請改用較新的手機瀏覽器。', true);
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' }, audio: false
      });
    } catch (e) {
      const n = (e && e.name) || '';
      let hint = '無法開啟相機。';
      if (n === 'NotAllowedError') hint = '相機權限被拒。請到瀏覽器網站設定允許相機後重試。';
      else if (n === 'NotFoundError') hint = '找不到相機裝置。';
      else if (location.protocol !== 'https:' && location.hostname !== 'localhost')
        hint = '相機需在 HTTPS 下才能使用，請用 https 網址開啟。';
      setStatus(hint, true);
      return;
    }
    cam.srcObject = stream;
    try { await cam.play(); } catch (e) { /* iOS 偶發，忽略 */ }
    camWrap.classList.remove('idle');
    camWrap.classList.add('scanning');
    startBtn.hidden = true;
    stopBtn.hidden = false;
    scanning = true;
    setStatus('掃描中… 將 QR 對準框內');
    rafId = requestAnimationFrame(scanLoop);
  }

  function stopScan() {
    scanning = false;
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    cam.srcObject = null;
    camWrap.classList.remove('scanning');
    camWrap.classList.add('idle');
    startBtn.hidden = false;
    stopBtn.hidden = true;
  }

  function scanLoop() {
    if (!scanning) return;
    if (cam.readyState === cam.HAVE_ENOUGH_DATA && cam.videoWidth) {
      const w = cam.videoWidth, h = cam.videoHeight;
      canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(cam, 0, 0, w, h);
      let code = null;
      try {
        const img = ctx.getImageData(0, 0, w, h);
        if (window.jsQR) code = jsQR(img.data, w, h, { inversionAttempts: 'dontInvert' });
      } catch (e) { /* getImageData 偶發跨域/尺寸錯誤，跳過此影格 */ }
      if (code && code.data) { onDecode(code.data); return; }
    }
    rafId = requestAnimationFrame(scanLoop);
  }

  async function onDecode(text) {
    if (!scanning) return;
    scanning = false;             // 防止同一張 QR 重複觸發
    setStatus('辨識成功，查詢訂單中…');
    stopScan();
    try {
      const r = await authFetch('/api/redeem/lookup', {
        method: 'POST', body: JSON.stringify({ qr: text })
      });
      const d = await r.json().catch(() => ({}));
      if (!d.ok) { renderError(d.error || '查詢失敗'); return; }
      renderSummary(d);
    } catch (e) {
      renderError('連線失敗，請重試');
    }
  }

  // ---- 結果渲染（user-controlled 內容一律用 textContent，避免 XSS）----
  function hideResult() {
    resultEl.hidden = true;
    resultEl.textContent = '';
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function sumRow(k, v, vClass) {
    const row = el('div', 'rdm-sum-row');
    row.appendChild(el('span', 'k', k));
    const vv = el('span', 'v' + (vClass ? ' ' + vClass : ''));
    if (v instanceof Node) vv.appendChild(v); else vv.textContent = v;
    row.appendChild(vv);
    return row;
  }

  function packsLines(o) {
    const packs = (o.packs || []);
    const lines = [];
    packs.forEach((p, i) => {
      const bits = [];
      if (p.preferences && p.preferences.length && p.preferences[0] !== '農場隨機搭配')
        bits.push(p.preferences.join('、'));
      if (p.special_items && p.special_items.length)
        bits.push('特殊：' + p.special_items.map((s) => s.item_name).join('、'));
      if (p.pack_remarks && p.pack_remarks !== '無') bits.push(p.pack_remarks);
      const qtyMark = (p.quantity > 1) ? ' ×' + p.quantity : '';
      lines.push('[包' + (i + 1) + qtyMark + '] ' + (bits.length ? bits.join(' ｜ ') : '農場隨機搭配'));
    });
    (o.items || []).forEach((it) => {
      lines.push('[' + (it.source || '') + '] ' + (it.name || '') + ' ×' + (Number(it.quantity) || 1) + (it.unit || ''));
    });
    return lines;
  }

  function banner(kind, icon, text) {
    const b = el('div', 'rdm-banner ' + kind);
    b.appendChild(el('span', 'ic', icon));
    b.appendChild(el('span', null, text));
    return b;
  }

  function renderSummary(d) {
    const o = d.order || {};
    const token = d.token || '';
    resultEl.textContent = '';

    // 狀態判定
    const picked = o.is_picked_up;
    const status = o.order_status || 'active';
    const redeemable = !!d.redeemable;

    if (redeemable) {
      resultEl.appendChild(banner('ok', '🛒', '待核銷 — 請核對後確認取貨'));
    } else if (status === 'cancelled') {
      resultEl.appendChild(banner('bad', '✗', '此訂單已取消，無法核銷'));
    } else if (status === 'refunded') {
      resultEl.appendChild(banner('bad', '✗', '此訂單已退貨，無法核銷'));
    } else if (picked) {
      const at = o.picked_up_at ? '（' + o.picked_up_at + '）' : '';
      resultEl.appendChild(banner('warn', '✓', '此訂單已取貨' + at));
    } else {
      resultEl.appendChild(banner('warn', '!', '此訂單目前無法核銷'));
    }

    // 金額（最顯眼）
    resultEl.appendChild(sumRow('應收金額', 'NT$ ' + (o.amount || 0), 'amount'));

    // 付款方式 chip：未接單優先顯示，其次才是方式
    const payNode = el('span');
    if (!o.is_confirmed) {
      payNode.appendChild(el('span', 'pay-chip unpaid', '尚未確認訂單'));
    } else if (o.payment_method === 'cash') {
      payNode.appendChild(el('span', 'pay-chip cash', '取貨付現'));
    } else if (o.payment_method === 'transfer') {
      payNode.appendChild(el('span', 'pay-chip cash', '轉帳'));
    } else {
      payNode.textContent = o.payment_method || '—';
    }
    resultEl.appendChild(sumRow('付款方式', payNode));

    // 取貨地點：讓店員一眼看出客人是不是跑錯廠
    if (o.pickup_label) {
      resultEl.appendChild(sumRow('取貨地點', o.pickup_label));
    }

    resultEl.appendChild(sumRow('顧客', (o.name || '—') + (o.phone_tail ? '（…' + o.phone_tail + '）' : '')));
    if (o.total_quantity) resultEl.appendChild(sumRow('份數', o.total_quantity + ' 份'));
    resultEl.appendChild(sumRow('訂單編號', o.order_id || '—', 'oid'));
    if (o.expected_ship_date) resultEl.appendChild(sumRow('預計出貨', o.expected_ship_date));

    // 品項摘要
    const lines = packsLines(o);
    if (lines.length) {
      const box = el('div', 'rdm-items');
      lines.forEach((ln) => box.appendChild(el('div', 'pack', ln)));
      resultEl.appendChild(box);
    }

    // 顧客備註（如「免單」對收現金極重要 → 醒目顯示）
    if (o.remarks) {
      const rm = el('div', 'rdm-remark');
      rm.appendChild(el('span', 'rdm-remark-label', '備註'));
      rm.appendChild(el('span', 'rdm-remark-text', o.remarks));
      resultEl.appendChild(rm);
    }

    // 動作
    const actions = el('div', 'rdm-result-actions');
    if (redeemable) {
      const confirmBtn = el('button', 'btn-primary big', '確認核銷（已收現金）');
      confirmBtn.addEventListener('click', () => doConfirm(o.order_id, token, confirmBtn));
      actions.appendChild(confirmBtn);
    }
    const againBtn = el('button', redeemable ? 'btn-ghost' : 'btn-primary', '繼續掃下一筆');
    againBtn.addEventListener('click', () => { hideResult(); startScan(); });
    actions.appendChild(againBtn);
    resultEl.appendChild(actions);

    resultEl.hidden = false;
    setStatus('');
    resultEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function doConfirm(orderId, token, btn) {
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = '核銷中…';
    try {
      const r = await authFetch('/api/redeem/confirm', {
        method: 'POST', body: JSON.stringify({ order_id: orderId, qr_token: token })
      });
      const d = await r.json().catch(() => ({}));
      if (d.ok) { renderDone(orderId); }
      else {
        btn.disabled = false; btn.textContent = orig;
        // 已被別台核銷 / 狀態變更 → 顯示原因
        renderError(d.error || '核銷失敗，請重試');
      }
    } catch (e) {
      btn.disabled = false; btn.textContent = orig;
      renderError('連線失敗，請重試');
    }
  }

  function renderDone(orderId) {
    resultEl.textContent = '';
    resultEl.appendChild(banner('ok', '✅', '核銷成功！已標記取貨'));
    resultEl.appendChild(sumRow('訂單編號', orderId || '—', 'oid'));
    const actions = el('div', 'rdm-result-actions');
    const againBtn = el('button', 'btn-primary big', '繼續掃下一筆');
    againBtn.addEventListener('click', () => { hideResult(); startScan(); });
    actions.appendChild(againBtn);
    resultEl.appendChild(actions);
    resultEl.hidden = false;
    resultEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function renderError(msg) {
    resultEl.textContent = '';
    resultEl.appendChild(banner('bad', '⚠️', msg));
    const actions = el('div', 'rdm-result-actions');
    const againBtn = el('button', 'btn-primary', '重新掃描');
    againBtn.addEventListener('click', () => { hideResult(); startScan(); });
    actions.appendChild(againBtn);
    resultEl.appendChild(actions);
    resultEl.hidden = false;
    setStatus('');
  }

  // ---- 登出 + 閒置倒數 ----
  const IDLE_MIN = window.__IDLE_MIN__ || 30;
  let idleLeft = IDLE_MIN * 60;
  const idleTimerEl = $('idle-timer');
  function tickIdle() {
    idleLeft -= 1;
    if (idleLeft <= 0) { location.href = '/login'; return; }
    if (idleTimerEl) {
      const m = Math.floor(idleLeft / 60), s = idleLeft % 60;
      idleTimerEl.textContent = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    }
  }
  setInterval(tickIdle, 1000);
  ['click', 'touchstart', 'keydown'].forEach((ev) =>
    document.addEventListener(ev, () => { idleLeft = IDLE_MIN * 60; }, { passive: true }));

  // ---- 綁定 ----
  startBtn.addEventListener('click', startScan);
  stopBtn.addEventListener('click', () => { stopScan(); setStatus('已停止掃描'); });
  const logoutBtn = $('logout-btn');
  if (logoutBtn) logoutBtn.addEventListener('click', async () => {
    stopScan();
    try { await authFetch('/api/logout', { method: 'POST' }); } catch (e) {}
    location.href = '/login';
  });
  window.addEventListener('beforeunload', stopScan);

  camWrap.classList.add('idle');
})();
