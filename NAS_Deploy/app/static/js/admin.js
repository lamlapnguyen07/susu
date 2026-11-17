// ============= Admin Dashboard JS =============

const state = {
  shipDates: [],          // [{date, total, unnotified, unpicked, picked}, ...]
  selectedDate: 'all',    // 'all' | 'none' | 'YYYY-MM-DD'
  showClosed: false,
  showVoided: false,      // 是否顯示已取消/已退貨
  payStatus: '',          // '' | 'pending' | 'paid'
  pickup: '',             // '' | 'tamsui' | 'qiyan'
  searchQ: '',            // 訂單搜尋字（姓名/電話/編號）；有值時跨所有日期搜
  orders: [],
  selected: new Set(),    // selected order_ids
  viewWeekStart: getWeekStart(new Date()),  // 目前檢視中的那一週的「週一」Date 物件
};

// 取貨地點短名（值與 app.py 的 PICKUP_LOCATIONS 一致）
const PICKUP_TEXT = { tamsui: '淡水廠', qiyan: '奇岩廠' };

// ============= 週起始日（週一）計算工具 =============
function getWeekStart(d) {
  const monday = new Date(d);
  const dow = monday.getDay();           // 0=日 1=一 ... 6=六
  const diff = dow === 0 ? -6 : 1 - dow; // 從這天回推到當週週一
  monday.setDate(monday.getDate() + diff);
  monday.setHours(0, 0, 0, 0);
  return monday;
}

function toISODate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

function formatWeekRange(start) {
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return `${start.getMonth()+1}月${start.getDate()}日 ~ ${end.getMonth()+1}月${end.getDate()}日`;
}

const $ = (id) => document.getElementById(id);

// HTML escape — 防 stored XSS（顧客名稱／備註等 user-controlled 內容）
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// CSRF: 所有 state-changing 請求都要附上 X-CSRF-Token
const CSRF = window.__CSRF__ || '';
function authFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  // Only attach CSRF to mutating requests
  const method = (opts.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    headers['X-CSRF-Token'] = CSRF;
  }
  return fetch(url, Object.assign({}, opts, { headers, credentials: 'same-origin' }));
}

// ============= Toast =============
function toast(msg, type = '') {
  const stack = $('toast-stack');
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity 280ms, transform 280ms';
    el.style.opacity = '0';
    el.style.transform = 'translateX(16px)';
    setTimeout(() => el.remove(), 320);
  }, 2400);
}

// ============= Confirm modal =============
function confirmAction(title, body, onOk) {
  $('confirm-title').textContent = title;
  $('confirm-body').textContent = body;
  $('confirm-modal').classList.add('open');
  const ok = $('confirm-ok');
  const cancel = $('confirm-cancel');
  const close = () => $('confirm-modal').classList.remove('open');
  const handler = () => { close(); onOk(); ok.removeEventListener('click', handler); };
  ok.addEventListener('click', handler);
  cancel.onclick = close;
}

// ============= Idle timeout =============
const IDLE_MIN = window.__IDLE_MIN__ || 15;
let lastActivity = Date.now();
['mousemove','keydown','click','scroll','touchstart'].forEach(ev => {
  document.addEventListener(ev, () => { lastActivity = Date.now(); }, { passive: true });
});

function updateIdleTimer() {
  const elapsed = (Date.now() - lastActivity) / 1000;
  const remain = Math.max(0, IDLE_MIN * 60 - elapsed);
  const mm = Math.floor(remain / 60);
  const ss = Math.floor(remain % 60);
  const el = $('idle-timer');
  el.textContent = `自動登出 ${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
  el.classList.toggle('warn', remain < 120);
  if (remain <= 0) {
    authFetch('/api/logout', { method: 'POST' }).finally(() => location.href = '/login');
  }
}
setInterval(updateIdleTimer, 1000);

// Heartbeat every 2 min so server session also refreshes
setInterval(() => {
  authFetch('/api/heartbeat', { method: 'POST' }).catch(()=>{});
}, 120000);

// ============= 出貨日期 list =============
async function loadShipDates() {
  try {
    const r = await fetch('/api/orders/ship-dates');
    const data = await r.json();
    state.shipDates = data;
    renderShipDates();
  } catch (e) {
    console.error(e);
  }
}

// ============= 收款狀態統計 / 篩選 =============
async function loadPaySummary() {
  try {
    const r = await fetch('/api/orders/pay-summary');
    const d = await r.json();
    const n = d.pending_total || 0;
    const badge = $('pay-pending-badge');
    if (badge) {
      badge.textContent = n;
      badge.classList.toggle('zero', n === 0);
    }
  } catch (e) { /* 靜默 */ }
}

function setPayFilter(val) {
  state.payStatus = val;
  state.selected.clear();
  // 更新分頁 active 樣式
  document.querySelectorAll('.pay-tab').forEach(t => {
    t.classList.toggle('active', (t.dataset.pay || '') === val);
  });
  // 提示文字
  const hint = $('pay-hint');
  if (hint) {
    hint.textContent = val === 'pending'
      ? '只看「未收款 + 待對帳」的訂單 — 這些就是還沒收到錢的'
      : val === 'paid'
        ? '只看已收款（現金 / 轉帳）的訂單'
        : '';
  }
  loadOrders();
}

function formatDateLabel(iso) {
  if (!iso) return { main: '未指定', weekday: '' };
  const d = new Date(iso + 'T00:00:00');
  const today = new Date(); today.setHours(0,0,0,0);
  const tmr = new Date(today); tmr.setDate(tmr.getDate() + 1);
  let prefix = '';
  if (d.getTime() === today.getTime()) prefix = '今日 · ';
  else if (d.getTime() === tmr.getTime()) prefix = '明日 · ';
  const wkday = ['日','一','二','三','四','五','六'][d.getDay()];
  return {
    main: prefix + `${d.getMonth()+1}月${d.getDate()}日`,
    weekday: `週${wkday}`,
    isToday: d.getTime() === today.getTime(),
    isPast: d < today,
  };
}

function renderShipDates() {
  const list = $('date-list');
  list.innerHTML = '';

  // 更新週導覽列顯示
  const wkRangeEl = $('wk-range');
  if (wkRangeEl) wkRangeEl.textContent = formatWeekRange(state.viewWeekStart);

  // 計算本週範圍 [週一, 週日]
  const weekStart = state.viewWeekStart;
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);
  weekEnd.setHours(23, 59, 59, 999);

  // 「回到本週」按鈕：viewWeekStart 不是當前真正的這週時才顯示
  const thisWeek = getWeekStart(new Date());
  const todayBtn = $('wk-today');
  if (todayBtn) {
    todayBtn.hidden = (weekStart.getTime() === thisWeek.getTime());
  }

  // 過濾：只保留落在本週內的出貨日（無出貨日的訂單仍歸到「未指定」）
  const inWeek = state.shipDates.filter(d => {
    if (!d.date) return false;
    const dt = new Date(d.date + 'T00:00:00');
    return dt >= weekStart && dt <= weekEnd;
  });
  const noShipDate = state.shipDates.filter(d => !d.date);

  // 「全部」加總用「整體」(不只本週)，這樣使用者切週時仍能一鍵看全部
  const allTotal = state.shipDates.reduce((s, d) => s + d.total, 0);
  const allUnnotified = state.shipDates.reduce((s, d) => s + d.unnotified, 0);
  const allUnpicked = state.shipDates.reduce((s, d) => s + d.unpicked, 0);

  const items = [
    { key: 'all', main: '全部訂單', weekday: '', total: allTotal, unnotified: allUnnotified, unpicked: allUnpicked },
    ...inWeek.map(d => {
      const lbl = formatDateLabel(d.date);
      return {
        key: d.date || 'none',
        main: lbl.main,
        weekday: lbl.weekday,
        isToday: lbl.isToday,
        ...d,
      };
    }),
    ...noShipDate.map(d => {
      const lbl = formatDateLabel(null);
      return {
        key: 'none',
        main: lbl.main,
        weekday: lbl.weekday,
        ...d,
      };
    }),
  ];

  // 若本週完全沒訂單 → 顯示提示
  if (inWeek.length === 0 && noShipDate.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'date-list-empty';
    empty.textContent = '本週沒有訂單';
    list.appendChild(empty);
    // 仍要 render「全部訂單」項
    items.length = 1;
  }

  items.forEach((it) => {
    const div = document.createElement('div');
    div.className = 'date-item';
    if (state.selectedDate === it.key) div.classList.add('active');
    if (it.isToday) div.classList.add('today');
    div.innerHTML = `
      <div class="row1">
        <span class="date-main">${it.main}${it.weekday ? `<span class="weekday">${it.weekday}</span>` : ''}</span>
        <span class="total">${it.total} 筆</span>
      </div>
      <div class="row2">
        <span class="stat notify"><span class="num">${it.unnotified}</span>待通知</span>
        <span class="stat pickup"><span class="num">${it.unpicked}</span>待取貨</span>
      </div>
    `;
    div.addEventListener('click', () => {
      state.selectedDate = it.key;
      state.selected.clear();
      renderShipDates();
      loadOrders();
    });
    list.appendChild(div);
  });
}

// ============= 訂單列表 =============
async function loadOrders() {
  const tbody = $('order-tbody');
  tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:var(--ink-soft);font-style:italic;">載入中…</td></tr>';

  const params = new URLSearchParams();
  if (state.searchQ) {
    // 搜尋模式：只送 q（+ show_voided），後端會跨所有日期/含已結案搜
    params.set('q', state.searchQ);
    if (state.showVoided) params.set('show_voided', '1');
  } else {
    if (state.showClosed) params.set('show_closed', '1');
    if (state.showVoided) params.set('show_voided', '1');
    if (state.selectedDate !== 'all') params.set('ship_date', state.selectedDate);
    if (state.payStatus) params.set('pay_status', state.payStatus);
    if (state.pickup) params.set('pickup', state.pickup);
  }

  try {
    const r = await fetch('/api/orders?' + params.toString());
    const orders = await r.json();
    state.orders = Array.isArray(orders) ? orders : [];
    renderOrders();
    updateMainHead();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align:center;padding:40px;color:var(--tomato);">載入失敗：${e.message}</td></tr>`;
  }
}

function updateMainHead() {
  const total = state.orders.length;
  const unnotified = state.orders.filter(o => !o.is_notified && !o.is_picked_up).length;
  const unpicked = state.orders.filter(o => !o.is_picked_up).length;
  $('stat-total').textContent = total;
  $('stat-unnotified').textContent = unnotified;
  $('stat-unpicked').textContent = unpicked;

  // Title
  if (state.searchQ) {
    $('filter-title').innerHTML = `搜尋「${esc(state.searchQ)}」<span class="sub" style="color:var(--ochre);font-style:italic;">— ${total} 筆</span>`;
  } else if (state.selectedDate === 'all') {
    $('filter-title').innerHTML = '全部訂單 <span class="sub" style="color:var(--ochre);font-style:italic;">— All</span>';
  } else if (state.selectedDate === 'none') {
    $('filter-title').innerHTML = '未指定出貨日 <span class="sub" style="color:var(--ochre);font-style:italic;">— Unscheduled</span>';
  } else {
    const lbl = formatDateLabel(state.selectedDate);
    $('filter-title').innerHTML = `${lbl.main} <span class="sub" style="color:var(--ochre);font-style:italic;">${lbl.weekday}</span>`;
  }
}

function renderOrders() {
  const tbody = $('order-tbody');
  if (state.orders.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="11">
        <div class="empty-state">
          <h3>此區段尚無訂單</h3>
          <p>切換側邊欄的出貨日期，或勾選「顯示已結案訂單」</p>
        </div>
      </td></tr>`;
    return;
  }

  tbody.innerHTML = '';
  state.orders.forEach(o => {
    const tr = document.createElement('tr');
    if (o.is_picked_up) tr.classList.add('closed');
    if (state.selected.has(o.order_id)) tr.classList.add('selected');

    const details = o.order_details || {};
    // order_details 欄位存的是完整 payload（雙層）：{customer_info, order_details:{packs...}}
    // packs / total_quantity 在內層；相容只有內層的舊資料 → 找不到內層就退回頂層
    const inner = (details.order_details && typeof details.order_details === 'object')
                  ? details.order_details : details;
    const packs = inner.packs || [];
    const totalQty = o.total_quantity || inner.total_quantity ||
                     packs.reduce((s, p) => s + (p.quantity || 1), 0);

    // Remarks（user-controlled 內容全部 escape）
    const remarksParts = [];
    const orderRemarks = (details.customer_info || {}).remarks;
    if (orderRemarks && orderRemarks !== '無') remarksParts.push(`<div>${esc(orderRemarks)}</div>`);
    packs.forEach((p, i) => {
      const bits = [];
      if (p.preferences && p.preferences.length && p.preferences[0] !== '農場隨機搭配')
        bits.push(esc(p.preferences.join('、')));
      if (p.special_items && p.special_items.length)
        bits.push('特殊：' + esc(p.special_items.map(s => s.item_name).join('、')));
      if (p.pack_remarks && p.pack_remarks !== '無') bits.push(esc(p.pack_remarks));
      if (bits.length) {
        const qtyMark = (p.quantity > 1) ? ` ×${p.quantity}` : '';
        remarksParts.push(`<div class="pack">[包${i+1}${qtyMark}] ${bits.join(' ｜ ')}</div>`);
      }
    });
    const remarksHtml = remarksParts.join('') || '<span class="muted" style="font-style:italic;">無</span>';

    // Ship date — 可即時編輯
    const todayISO = new Date().toISOString().slice(0, 10);
    const shipDateVal = o.expected_ship_date || '';
    const shipDateHtml = `
      <input type="date" class="ship-date-input" data-oid="${o.order_id}"
        value="${shipDateVal}" min="${todayISO}"
        title="點選日期可即時設定／修改預計出貨日">
      ${shipDateVal ? `<div class="ship-date-day">${formatDateLabel(shipDateVal).weekday}</div>` : '<div class="ship-date-day none">尚未指定</div>'}
    `;

    // Status pill — 已取消/已退貨優先（終態），否則照原本三態
    const ost = o.order_status || 'active';
    let statusHtml;
    if (ost === 'cancelled') {
      const who = o.cancelled_by === 'customer' ? '顧客' : '後台';
      statusHtml = `<span class="status-pill voided-cancel" title="${who}取消">✗ 已取消</span>`;
    } else if (ost === 'refunded') {
      statusHtml = '<span class="status-pill voided-refund">↺ 已退貨</span>';
    } else if (o.is_picked_up) {
      statusHtml = '<span class="status-pill picked">已結案</span>';
    } else if (o.is_notified) {
      statusHtml = '<span class="status-pill notified dot">已通知</span>';
    } else {
      statusHtml = '<span class="status-pill pending dot">待通知</span>';
    }

    // 收款：方式與狀態各一個下拉（方式＝現金/轉帳；狀態＝未收款/待對帳/已收款）
    const st = o.payment_status || 'unpaid';
    const statusClass = st === 'paid' ? 'st-paid' : st === 'pending_check' ? 'st-pending' : '';

    // 取消 / 退貨的訂單不顯示收款下拉（否則會誤導成「未付款」）→ 改顯示靜態「—」
    let paymentCellHtml;
    if (ost === 'cancelled' || ost === 'refunded') {
      paymentCellHtml = `<span class="payment-void" title="此訂單已${ost === 'cancelled' ? '取消' : '退貨'}，無收款">—</span>`;
    } else {
      paymentCellHtml = `
        <div class="pay-cell">
          <select class="pay-method" data-oid="${esc(o.order_id)}">
            <option value="cash"     ${o.payment_method === 'cash' ? 'selected' : ''}>現金</option>
            <option value="transfer" ${o.payment_method === 'transfer' ? 'selected' : ''}>轉帳</option>
          </select>
          <select class="pay-status ${statusClass}" data-oid="${esc(o.order_id)}">
            <option value="unpaid"        ${st === 'unpaid' ? 'selected' : ''}>未收款</option>
            <option value="pending_check" ${st === 'pending_check' ? 'selected' : ''}>⏳ 待對帳</option>
            <option value="paid"          ${st === 'paid' ? 'selected' : ''}>已收款 ✓</option>
          </select>
        </div>`;
    }

    // 取貨地點：可即時修改；已取貨／已取消的單改成靜態文字
    const loc = o.pickup_location || '';
    let pickupCellHtml;
    if (ost !== 'active' || o.is_picked_up) {
      pickupCellHtml = `<span>${esc(PICKUP_TEXT[loc] || '未指定')}</span>`;
    } else {
      pickupCellHtml = `
        <select class="pickup-sel ${loc ? '' : 'unset'}" data-oid="${esc(o.order_id)}">
          <option value="" ${loc ? '' : 'selected'} disabled>未指定</option>
          <option value="tamsui" ${loc === 'tamsui' ? 'selected' : ''}>淡水廠</option>
          <option value="qiyan"  ${loc === 'qiyan' ? 'selected' : ''}>奇岩廠</option>
        </select>`;
    }

    tr.innerHTML = `
      <td class="center">
        <label class="check" style="margin: 0; justify-content: center;">
          <input type="checkbox" class="row-check" data-oid="${esc(o.order_id)}"
            ${state.selected.has(o.order_id) ? 'checked' : ''}
            ${o.is_picked_up ? 'disabled' : ''}>
          <span class="box"></span>
        </label>
      </td>
      <td>
        <div class="oid">#${esc(o.order_id)}</div>
        <div style="font-size: 11.5px; color: var(--ink-soft); font-style: italic; margin-top: 2px;">${esc(o.order_time || '')}</div>
      </td>
      <td>
        <div class="name">${esc(o.customer_name)}<span class="phone">${esc(o.customer_phone)}${o.line_id ? ' · LINE ✓' : ''}</span></div>
      </td>
      <td class="center"><span class="qty">${totalQty}<span class="unit">包</span></span></td>
      <td class="right"><span class="price"><span class="ccy">NT$</span>${o.grand_total}</span></td>
      <td>${shipDateHtml}</td>
      <td>${pickupCellHtml}</td>
      <td><div class="remarks">${remarksHtml}</div></td>
      <td>${paymentCellHtml}</td>
      <td class="center">${statusHtml}</td>
      <td class="right">
        <div class="row-actions">
          ${ost === 'active' && !o.is_confirmed && !o.is_picked_up ? `<button class="btn btn-accent btn-sm" data-action="confirm-order" data-oid="${esc(o.order_id)}" title="確認接單：LINE 通知顧客（帶 QR 來取貨付款）+ 生成 QR">確認訂單</button>` : ''}
          ${ost === 'active' && o.is_confirmed && !o.is_notified && !o.is_picked_up ? `<button class="btn btn-ghost btn-sm" data-action="notify" data-oid="${esc(o.order_id)}" title="通知顧客可以來取貨了">推播</button>` : ''}
          ${ost === 'active' && !o.is_picked_up ? `<button class="btn btn-primary btn-sm" data-action="pickup" data-oid="${esc(o.order_id)}">取貨</button>` : ''}
          ${ost === 'active' && !o.is_picked_up ? `<button class="btn btn-danger btn-sm" data-action="admin-cancel" data-oid="${esc(o.order_id)}" title="取消此訂單">取消</button>` : ''}
          ${ost === 'active' && o.is_picked_up ? `<button class="btn btn-sm row-refund-btn" data-action="refund" data-oid="${esc(o.order_id)}" title="標記此單已退貨">退貨</button>` : ''}
        </div>
      </td>
    `;
    if (ost === 'cancelled' || ost === 'refunded') tr.classList.add('voided');
    tbody.appendChild(tr);
  });

  // Bind row interactions
  tbody.querySelectorAll('.row-check').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const oid = e.target.dataset.oid;
      if (e.target.checked) state.selected.add(oid);
      else state.selected.delete(oid);
      e.target.closest('tr').classList.toggle('selected', e.target.checked);
      updateSelCount();
    });
  });

  // 出貨日 inline 編輯
  tbody.querySelectorAll('.ship-date-input').forEach(inp => {
    inp.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const newDate = e.target.value || null;
      try {
        const r = await authFetch(`/api/orders/${oid}/ship-date`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ ship_date: newDate })
        });
        const j = await r.json();
        if (j.ok) {
          toast(newDate ? `出貨日已設為 ${newDate}` : '已清除出貨日', 'success');
          // 立刻同步側邊欄統計
          loadShipDates();
          // 局部更新此列的週幾顯示
          const td = e.target.closest('td');
          const dayEl = td.querySelector('.ship-date-day');
          if (newDate) {
            dayEl.classList.remove('none');
            dayEl.textContent = formatDateLabel(newDate).weekday;
          } else {
            dayEl.classList.add('none');
            dayEl.textContent = '尚未指定';
          }
          // 同步本地 state
          const order = state.orders.find(x => x.order_id === oid);
          if (order) order.expected_ship_date = newDate;
        } else {
          toast(j.error || '更新失敗', 'error');
        }
      } catch { toast('網路錯誤', 'error'); }
    });
  });

  // 付款方式下拉：只送 method，不動 status
  tbody.querySelectorAll('select.pay-method').forEach(sel => {
    sel.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const method = e.target.value;
      const prev = state.orders.find(x => x.order_id === oid)?.payment_method;
      try {
        const r = await authFetch(`/api/orders/${oid}/payment`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ method })
        });
        const j = await r.json();
        if (!j.ok) { e.target.value = prev || 'cash'; toast('更新失敗', 'error'); return; }
        const order = state.orders.find(x => x.order_id === oid);
        if (order) order.payment_method = method;
      } catch {
        e.target.value = prev || 'cash';
        toast('網路錯誤', 'error');
      }
    });
  });

  // 收款狀態下拉：只送 status，不動 method
  tbody.querySelectorAll('select.pay-status').forEach(sel => {
    sel.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const status = e.target.value;
      const prev = state.orders.find(x => x.order_id === oid)?.payment_status;
      e.target.className = 'pay-status ' + (
        status === 'paid' ? 'st-paid' : status === 'pending_check' ? 'st-pending' : ''
      );
      try {
        const r = await authFetch(`/api/orders/${oid}/payment`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ status })
        });
        const j = await r.json();
        if (!j.ok) { e.target.value = prev || 'unpaid'; toast('更新失敗', 'error'); return; }
        const order = state.orders.find(x => x.order_id === oid);
        if (order) order.payment_status = status;
        loadPaySummary();
        // 若正在篩選收款狀態，且這筆已不符合條件 → 重新載入讓它離開列表
        if (state.payStatus) {
          const stillMatch = state.payStatus === 'pending'
            ? (status === 'unpaid' || status === 'pending_check')
            : (status === 'paid');
          if (!stillMatch) loadOrders();
        }
      } catch {
        e.target.value = prev || 'unpaid';
        toast('網路錯誤', 'error');
      }
    });
  });

  // 取貨地點下拉
  tbody.querySelectorAll('select.pickup-sel').forEach(sel => {
    sel.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const loc = e.target.value;
      const prev = state.orders.find(x => x.order_id === oid)?.pickup_location || '';
      e.target.classList.remove('unset');
      try {
        const r = await authFetch(`/api/orders/${oid}/pickup-location`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ pickup_location: loc })
        });
        const j = await r.json();
        if (!j.ok) { e.target.value = prev; toast(j.error || '更新失敗', 'error'); return; }
        const order = state.orders.find(x => x.order_id === oid);
        if (order) order.pickup_location = loc;
        toast('取貨地點已更新');
        if (state.pickup && state.pickup !== loc) loadOrders();
      } catch {
        e.target.value = prev;
        toast('網路錯誤', 'error');
      }
    });
  });

  tbody.querySelectorAll('button[data-action]').forEach(b => {
    b.addEventListener('click', () => {
      const oid = b.dataset.oid;
      if (b.dataset.action === 'notify') doNotify([oid]);
      else if (b.dataset.action === 'pickup') {
        confirmAction('確認取貨', `將訂單 #${oid} 標記為「已結案」？`, () => doPickup([oid]));
      }
      else if (b.dataset.action === 'confirm-order') {
        confirmAction('確認訂單', `確認接受訂單 #${oid}？\n將標記為已接單、LINE 通知顧客（帶 QR 來取貨付款），並生成 QR。`,
                      () => doConfirmOrder(oid));
      }
      else if (b.dataset.action === 'admin-cancel') {
        confirmAction('取消訂單', `確定取消訂單 #${oid}？\n取消後無法復原。`,
                      () => doVoidOrder(oid, 'admin-cancel', '訂單已取消'));
      }
      else if (b.dataset.action === 'refund') {
        confirmAction('標記退貨', `確定將 #${oid} 標記為「已退貨」？\n此操作無法復原。`,
                      () => doVoidOrder(oid, 'refund', '已標記退貨'));
      }
    });
  });

  updateSelCount();
}

function updateSelCount() {
  const chip = $('sel-count');
  const n = state.selected.size;
  chip.textContent = n === 0 ? '未選' : `已選 ${n} 筆`;
  chip.classList.toggle('has-sel', n > 0);

  // 表頭全選 checkbox 狀態
  const visibleSelectable = state.orders.filter(o => !o.is_picked_up);
  const allChecked = visibleSelectable.length > 0 &&
                     visibleSelectable.every(o => state.selected.has(o.order_id));
  $('head-check').checked = allChecked;
}

// ============= 批次操作 =============
function selectByPredicate(pred) {
  state.selected.clear();
  state.orders.forEach(o => {
    if (!o.is_picked_up && pred(o)) state.selected.add(o.order_id);
  });
  // Update checkboxes
  document.querySelectorAll('.row-check').forEach(cb => {
    cb.checked = state.selected.has(cb.dataset.oid);
    cb.closest('tr').classList.toggle('selected', cb.checked);
  });
  updateSelCount();
}

async function doNotify(orderIds) {
  if (orderIds.length === 0) return;
  try {
    const r = await authFetch('/api/orders/notify', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ order_ids: orderIds })
    });
    const j = await r.json();
    if (j.ok) {
      toast(`已排入 ${j.count} 則 LINE 通知`, 'success');
      state.selected.clear();
      await loadOrders();
      await loadShipDates();
    } else {
      toast(j.error || '通知失敗', 'error');
    }
  } catch { toast('網路錯誤', 'error'); }
}

async function doPickup(orderIds) {
  if (orderIds.length === 0) return;
  try {
    const r = await authFetch('/api/orders/pickup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ order_ids: orderIds })
    });
    const j = await r.json();
    if (j.ok) {
      toast(`已將 ${j.count} 筆標記為已結案`, 'success');
      state.selected.clear();
      await loadOrders();
      await loadShipDates();
      loadPaySummary();
    } else {
      toast(j.error || '操作失敗', 'error');
    }
  } catch { toast('網路錯誤', 'error'); }
}

// 「確認訂單」（現金接單）：標記現金 + 推 LINE「下單成功帶 QR 來取貨」+ 生 QR 存本地
async function doConfirmOrder(orderId) {
  try {
    const r = await authFetch(`/api/orders/${encodeURIComponent(orderId)}/confirm-order`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    const j = await r.json();
    if (j.ok) {
      toast(j.queued
        ? '已確認訂單，QR 已生成並排入 LINE 通知'
        : '已確認訂單，QR 已生成（LINE 通知佇列入庫失敗，請看 server log）',
        'success');
      await loadOrders();
      loadPaySummary();
    } else {
      toast(j.error || '操作失敗', 'error');
    }
  } catch { toast('網路錯誤', 'error'); }
}

// 後台取消 / 退貨（共用：action = 'admin-cancel' 或 'refund'）
async function doVoidOrder(orderId, action, okMsg) {
  try {
    const r = await authFetch(`/api/orders/${encodeURIComponent(orderId)}/${action}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),   // note 預留（之後可加輸入框）
    });
    const j = await r.json();
    if (j.ok) {
      toast(okMsg, 'success');
      state.selected.delete(orderId);
      await loadOrders();
      await loadShipDates();
      loadPaySummary();
    } else {
      toast(j.error || '操作失敗', 'error');
    }
  } catch { toast('網路錯誤', 'error'); }
}

// ============= 匯出當日出貨單 (Word) =============
async function exportShipping() {
  const d = state.selectedDate;
  if (d === 'all') {
    toast('請先在左側選一個出貨日期，再匯出出貨單', 'error');
    return;
  }
  const btn = $('export-shipping-btn');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '產生中…'; }
  try {
    const r = await authFetch('/api/orders/export-shipping?date=' + encodeURIComponent(d), { method: 'GET' });
    if (!r.ok) {
      let msg = '匯出失敗';
      try { const j = await r.json(); msg = j.error || msg; } catch {}
      toast(msg, 'error');
      return;
    }
    const blob = await r.blob();
    const fname = '出貨單_' + (d === 'none' ? '未指定' : d.replace(/-/g, '')) + '.docx';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast('出貨單已匯出', 'success');
  } catch {
    toast('匯出失敗，請重試', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// ============= 初始化 =============
document.addEventListener('DOMContentLoaded', () => {
  // 匯出出貨單
  $('export-shipping-btn') && $('export-shipping-btn').addEventListener('click', exportShipping);

  // 訂單搜尋（姓名 / 電話 / 編號；邊打邊搜，300ms debounce）
  const searchInput = $('order-search');
  const searchClear = $('order-search-clear');
  if (searchInput) {
    let searchTimer = null;
    searchInput.addEventListener('input', () => {
      const v = searchInput.value.trim();
      if (searchClear) searchClear.hidden = (v === '');
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        if (v === state.searchQ) return;
        state.searchQ = v;
        loadOrders();
      }, 300);
    });
  }
  if (searchClear) {
    searchClear.addEventListener('click', () => {
      if (searchInput) searchInput.value = '';
      searchClear.hidden = true;
      state.searchQ = '';
      loadOrders();
    });
  }

  // 批次選取按鈕
  $('sel-all').addEventListener('click', () => selectByPredicate(() => true));
  $('sel-unnotified').addEventListener('click', () => selectByPredicate(o => !o.is_notified));
  $('sel-unpicked').addEventListener('click', () => selectByPredicate(o => !o.is_picked_up));
  $('sel-clear').addEventListener('click', () => selectByPredicate(() => false));

  // 表頭全選
  $('head-check').addEventListener('change', (e) => {
    if (e.target.checked) selectByPredicate(() => true);
    else selectByPredicate(() => false);
  });

  // 批次動作
  $('bulk-notify').addEventListener('click', () => {
    const ids = [...state.selected].filter(oid => {
      const o = state.orders.find(o => o.order_id === oid);
      return o && !o.is_notified && !o.is_picked_up;
    });
    if (ids.length === 0) { toast('沒有可發送通知的訂單', 'error'); return; }
    confirmAction(
      `推播 ${ids.length} 則 LINE 通知`,
      '確認後將自動排入發送佇列，由 LINE Bot 推播給顧客',
      () => doNotify(ids)
    );
  });

  $('bulk-pickup').addEventListener('click', () => {
    const ids = [...state.selected].filter(oid => {
      const o = state.orders.find(o => o.order_id === oid);
      return o && !o.is_picked_up;
    });
    if (ids.length === 0) { toast('沒有可結案的訂單', 'error'); return; }
    confirmAction(
      `結案 ${ids.length} 筆訂單`,
      '確認這些訂單都已完成取貨並結案？',
      () => doPickup(ids)
    );
  });

  // 收款狀態篩選分頁
  document.querySelectorAll('.pay-tab').forEach(tab => {
    tab.addEventListener('click', () => setPayFilter(tab.dataset.pay || ''));
  });

  // 取貨地點篩選
  document.querySelectorAll('.pickup-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      state.pickup = btn.dataset.pickup || '';
      state.selected.clear();
      document.querySelectorAll('.pickup-btn').forEach(b => {
        b.classList.toggle('active', b === btn);
      });
      loadOrders();
    });
  });

  // 顯示已結案 toggle
  $('show-closed').addEventListener('change', (e) => {
    state.showClosed = e.target.checked;
    loadOrders();
  });

  // 顯示已取消/退貨 toggle
  const showVoidedCb = $('show-voided');
  if (showVoidedCb) showVoidedCb.addEventListener('change', (e) => {
    state.showVoided = e.target.checked;
    loadOrders();
  });

  // 登出
  $('logout-btn').addEventListener('click', async () => {
    await authFetch('/api/logout', { method: 'POST' });
    location.href = '/login';
  });

  // 週導覽列：上下週 / 上下月 / 點中間開日期選擇器 / 回本週
  const moveWeek = (deltaDays) => {
    const next = new Date(state.viewWeekStart);
    next.setDate(next.getDate() + deltaDays);
    state.viewWeekStart = getWeekStart(next);
    renderShipDates();
  };
  $('wk-prev-week') && $('wk-prev-week').addEventListener('click', () => moveWeek(-7));
  $('wk-next-week') && $('wk-next-week').addEventListener('click', () => moveWeek(7));
  $('wk-prev-month') && $('wk-prev-month').addEventListener('click', () => moveWeek(-28));
  $('wk-next-month') && $('wk-next-month').addEventListener('click', () => moveWeek(28));

  const wkLabel = $('wk-label');
  const wkDateInput = $('wk-date-input');
  if (wkLabel && wkDateInput) {
    wkLabel.addEventListener('click', () => {
      // 預設值給目前那週的週一
      wkDateInput.value = toISODate(state.viewWeekStart);
      // 觸發原生日曆 — showPicker 是現代瀏覽器支援的 API；fallback 到 focus
      try {
        if (typeof wkDateInput.showPicker === 'function') wkDateInput.showPicker();
        else wkDateInput.focus();
      } catch (e) { wkDateInput.focus(); }
    });
    wkDateInput.addEventListener('change', () => {
      const v = wkDateInput.value;       // YYYY-MM-DD
      if (!v) return;
      const picked = new Date(v + 'T00:00:00');
      if (isNaN(picked.getTime())) return;
      state.viewWeekStart = getWeekStart(picked);
      renderShipDates();
    });
  }

  $('wk-today') && $('wk-today').addEventListener('click', () => {
    state.viewWeekStart = getWeekStart(new Date());
    renderShipDates();
  });

  // 行動裝置：側邊欄抽屜
  const sidebar = document.querySelector('.sidebar');
  const sbToggle = $('mobile-sidebar-toggle');
  if (sbToggle && sidebar) {
    sbToggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      sbToggle.classList.toggle('open');
    });
    // 選了任一日期就自動關
    sidebar.addEventListener('click', (e) => {
      if (e.target.closest('.date-item')) {
        sidebar.classList.remove('open');
        sbToggle.classList.remove('open');
      }
    });
  }

  // 載入資料
  loadShipDates();
  loadPaySummary();
  loadOrders();

  // 30 秒自動刷新（保留勾選狀態）
  function refreshAll() {
    loadShipDates();
    loadPaySummary();
    loadOrders();
  }
  setInterval(refreshAll, 30000);

  // 切回此分頁時立即重載：核銷／取貨等操作常在「核銷頁」或別的分頁完成，
  // 回到後台不該還停在 30 秒前的舊畫面（否則已結案的單仍顯示「取貨」鈕）。
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshAll();
  });
  // 用瀏覽器「上一頁」返回時（bfcache 還原頁面，不會重跑初始化）→ 強制重載
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) refreshAll();
  });

  updateIdleTimer();
});
