// ============= POS frontend =============

// 商品資料改由後台 CMS API 提供（/api/products）；保留注入資料當離線後援
let VEG = window.__VEG_DATA__ || { standard: [], special: [] };
const SPECIAL_PRICE = 150;  // 特殊品項預設加價（CMS 沒填價格時的後援值）
const MAX_STD_PER_PACK = 3; // 一包最多 3 種一般萵苣
const MAX_PACKS_PER_DAY = 10; // 每帳號每天最多 10 包（防刷單；後端為權威把關，此處僅友善提示）

// 從 CMS 載入商品 → 依 kind 拆成 standard / special，含供應狀態與單品價
async function loadVeg() {
  try {
    const r = await fetch('/api/products');
    if (!r.ok) throw new Error('http ' + r.status);
    const data = await r.json();
    const std = [], spc = [];
    (Array.isArray(data) ? data : []).forEach((p) => {
      const item = {
        name: p.name || '',
        photo: (p.photos && p.photos[0]) || '',
        available: p.is_available !== false,
        price: Number(p.price) || 0,
      };
      if (p.kind === 'special') spc.push(item); else std.push(item);
    });
    VEG = { standard: std, special: spc };
  } catch (e) {
    // 後援：沿用 HTML 注入的靜態清單（全部視為供應中）
    const fb = window.__VEG_DATA__ || { standard: [], special: [] };
    const norm = (list) => (list || []).map((x) => ({
      name: x.name || '', photo: x.photo || '', available: true, price: Number(x.price) || 0,
    }));
    VEG = { standard: norm(fb.standard), special: norm(fb.special) };
  }
  renderVegGrid();
  updatePackPrice();
}

// 查某個特殊品項的加價（找不到就用預設）
function specPriceOf(name) {
  const sp = (VEG.special || []).find((s) => s.name === name);
  return sp ? (Number(sp.price) || SPECIAL_PRICE) : SPECIAL_PRICE;
}
const LIFF_ID = window.__LIFF_ID__ || '';
const CSRF_TOKEN = window.__CSRF__ || '';
const CART_KEY = 'veg_cart';   // 購物車 localStorage key（讓 LINE 登入 redirect 後 cart 不會被吃掉）

const state = {
  cart: [],
  selectedStd: new Set(),
  selectedSpec: new Set(),
  comboMode: false,
  qty: 1,
  // LINE 登入狀態（未登入 = null；登入後 = { line_id, display_name, picture_url, bound_name, bound_phone }）
  line: null,
  // 伺服器回的所有訂單（登入後才有資料）；前端依 payment_method / is_picked_up 分類
  serverOrders: [],
};

// 訂單分類邏輯 — 現金模式：兩個 bucket（準備中 / 已完成）
function categorizeOrder(o) {
  // 已取消 / 已退貨 / 已取貨 → 已完成（歷史）
  if (o.order_status === 'cancelled' || o.order_status === 'refunded') return 'done';
  if (o.is_picked_up) return 'done';
  // 其餘（訂單成立、備貨中、可取貨）→ 準備中
  return 'preparing';
}

const $ = (id) => document.getElementById(id);

// HTML escape（保險用；目前 user 內容都走 textContent）
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                  .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
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

// ============= 日期顯示 =============
function setTodayLabel() {
  const d = new Date();
  const wkday = ['日','一','二','三','四','五','六'][d.getDay()];
  $('today-label').textContent =
    `${d.getFullYear()}年 ${d.getMonth()+1}月 ${d.getDate()}日　週${wkday}`;
}

// ============= 渲染萵苣 grid =============
function renderVegGrid() {
  // 組合包置頂橫條
  const comboCheck = $('combo-check');
  const comboBanner = $('combo-banner');
  comboCheck.addEventListener('change', (e) => {
    state.comboMode = e.target.checked;
    comboBanner.classList.toggle('selected', e.target.checked);
    if (e.target.checked) {
      state.selectedStd.clear();
      document.querySelectorAll('#std-grid .veggie-card').forEach(card => {
        card.classList.remove('selected');
      });
    }
    document.querySelectorAll('#std-grid .veggie-card').forEach(card => {
      card.classList.toggle('disabled', state.comboMode);
    });
    updatePackPrice();
  });

  // 一般萵苣 — 照片卡
  const grid = $('std-grid');
  grid.innerHTML = '';
  if (!VEG.standard.length) {
    grid.innerHTML = '<div class="veg-grid-empty">商品載入中…</div>';
  }
  VEG.standard.forEach((veg) => {
    const off = !veg.available;
    const card = document.createElement('div');
    card.className = 'veggie-card' + (off ? ' unavailable' : '');
    card.dataset.veg = veg.name;
    const photoHtml = veg.photo
      ? `<img src="${esc(veg.photo)}" alt="${esc(veg.name)}" loading="lazy" onerror="this.parentElement.classList.add('no-img'); this.remove();">`
      : `<div class="photo-fallback">${esc(veg.name.slice(0,1))}</div>`;
    card.innerHTML = `
      <div class="photo">${photoHtml}${off ? '<span class="veg-off-tag">暫無供應</span>' : ''}</div>
      <div class="check-mark" aria-hidden="true"></div>
      <div class="name">${esc(veg.name)}</div>
    `;
    card.addEventListener('click', () => {
      if (off) { toast(`「${veg.name}」目前暫無供應`, ''); return; }
      if (card.classList.contains('disabled')) return;
      const isSelected = card.classList.contains('selected');
      // 限制：一包最多 3 項
      if (!isSelected && state.selectedStd.size >= MAX_STD_PER_PACK) {
        toast(`一包最多選 ${MAX_STD_PER_PACK} 項萵苣，要更多請加入購物車再組第二包`, 'error');
        return;
      }
      const sel = card.classList.toggle('selected');
      if (sel) {
        state.selectedStd.add(veg.name);
        if (state.comboMode) {
          state.comboMode = false;
          comboCheck.checked = false;
          comboBanner.classList.remove('selected');
        }
      } else {
        state.selectedStd.delete(veg.name);
      }
      updatePackPrice();
    });
    grid.appendChild(card);
  });

  // 特殊品項 — 小縮圖 + 加價（供應狀態由 CMS 控制）
  const list = $('spec-list');
  list.innerHTML = '';
  VEG.special.forEach((sp) => {
    const off = !sp.available;
    const price = Number(sp.price) || SPECIAL_PRICE;
    const row = document.createElement('label');
    row.className = 'special-row check' + (off ? ' disabled' : '');
    const thumb = sp.photo
      ? `<img src="${esc(sp.photo)}" alt="${esc(sp.name)}" loading="lazy" onerror="this.style.display='none'">`
      : '';
    row.innerHTML = `
      <input type="checkbox" data-spec="${esc(sp.name)}"${off ? ' disabled' : ''}>
      <span class="box"></span>
      <div class="spec-thumb">${thumb}</div>
      <span class="name">${esc(sp.name)} <span class="meta-tag">${off ? '暫無供應' : '特殊品項'}</span></span>
      <span class="spec-price">+NT$ ${price}</span>
    `;
    const chk = row.querySelector('input[type=checkbox]');
    if (!off) {
      chk.addEventListener('change', (e) => {
        row.classList.toggle('active', e.target.checked);
        if (e.target.checked) state.selectedSpec.add(sp.name);
        else state.selectedSpec.delete(sp.name);
        updatePackPrice();
      });
    } else {
      // 防誤觸：點 row 完全沒反應
      row.addEventListener('click', (e) => e.preventDefault());
    }
    list.appendChild(row);
  });
}

// ============= 即時包價 =============
function calcPackPrice() {
  const customized = !state.comboMode && state.selectedStd.size > 0;
  let p = customized ? 250 : 200;
  state.selectedSpec.forEach((name) => { p += specPriceOf(name); });
  return p;
}
function updatePackPrice() {
  const total = calcPackPrice() * state.qty;
  $('add-cart-price').textContent = total;
}

// ============= 數量 stepper =============
function bindStepper() {
  const input = $('qty-val');
  const setQty = (n) => {
    const v = Math.max(1, Math.min(99, parseInt(n, 10) || 1));
    state.qty = v;
    input.value = v;
    updatePackPrice();
  };
  $('qty-minus').addEventListener('click', () => setQty(state.qty - 1));
  $('qty-plus').addEventListener('click', () => setQty(state.qty + 1));
  input.addEventListener('input', () => {
    const raw = input.value.replace(/[^\d]/g, '');
    input.value = raw;
    if (raw !== '') {
      state.qty = Math.max(1, Math.min(99, parseInt(raw, 10)));
      updatePackPrice();
    }
  });
  input.addEventListener('blur', () => setQty(input.value));
  input.addEventListener('focus', () => input.select());
}

// ============= 加入購物車 =============
function addPackToCart() {
  // 每帳號每天最多 10 包 → 購物車總包數先擋（後端會用「今天已下單包數」再次把關）
  const cartPacks = state.cart.reduce((s, p) => s + p.quantity, 0);
  if (cartPacks + state.qty > MAX_PACKS_PER_DAY) {
    const room = Math.max(0, MAX_PACKS_PER_DAY - cartPacks);
    toast(room > 0
      ? `每個帳號每天最多 ${MAX_PACKS_PER_DAY} 包，這次最多再加 ${room} 包`
      : `購物車已達每日上限 ${MAX_PACKS_PER_DAY} 包`, 'error');
    return;
  }
  let prefs;
  if (state.comboMode || state.selectedStd.size === 0) {
    prefs = ['組合包（農場隨機搭配）'];
  } else {
    prefs = [...state.selectedStd];
  }
  const specials = [...state.selectedSpec].map(name => ({ item_name: name, price: specPriceOf(name) }));
  const remarksEl = $('pack-remarks');           // 「這包備註」已移除 → 容錯
  const remarks = remarksEl ? remarksEl.value.trim() : '';
  const subtotal = calcPackPrice();
  const pack = {
    preferences: prefs,
    special_items: specials,
    pack_remarks: remarks,
    quantity: state.qty,
    subtotal: subtotal,
    line_total: subtotal * state.qty,
  };

  // 找購物車內是否已有「完全相同」的包 → 同一個就疊加數量
  const existingIdx = state.cart.findIndex(p => isSamePack(p, pack));
  if (existingIdx >= 0) {
    const existing = state.cart[existingIdx];
    existing.quantity += pack.quantity;
    existing.line_total = existing.subtotal * existing.quantity;
    renderCart();
    resetBuilder();
    bumpFab();
    toast(`同款組合已疊加 +${pack.quantity}（共 ${existing.quantity} 包 / NT$ ${existing.line_total}）`, 'success');
    return;
  }

  // 不同包 → 新增一筆
  state.cart.push(pack);
  renderCart();
  resetBuilder();
  bumpFab();
  toast(`已加入第 ${state.cart.length} 包 × ${pack.quantity}（NT$ ${pack.line_total}）`, 'success');
}

/**
 * 判斷兩包是否「完全相同」（可疊加）：
 *  - 偏好（一般萵苣或組合包）內容一致（無序比較）
 *  - 特殊品項一致（無序比較）
 *  - 備註文字一致（trim 後）
 *  - 單包金額一致（保險）
 */
function isSamePack(a, b) {
  if ((a.subtotal || 0) !== (b.subtotal || 0)) return false;
  if ((a.pack_remarks || '').trim() !== (b.pack_remarks || '').trim()) return false;
  const aPref = [...(a.preferences || [])].sort().join('|');
  const bPref = [...(b.preferences || [])].sort().join('|');
  if (aPref !== bPref) return false;
  const aSpec = [...(a.special_items || [])].map(x => x.item_name).sort().join('|');
  const bSpec = [...(b.special_items || [])].map(x => x.item_name).sort().join('|');
  if (aSpec !== bSpec) return false;
  return true;
}

function resetBuilder() {
  document.querySelectorAll('#std-grid .veggie-card').forEach(c => {
    c.classList.remove('selected', 'disabled');
  });
  $('combo-check').checked = false;
  $('combo-banner').classList.remove('selected');
  document.querySelectorAll('#spec-list input[type=checkbox]').forEach(c => c.checked = false);
  document.querySelectorAll('#spec-list .special-row').forEach(r => r.classList.remove('active'));
  state.selectedStd.clear();
  state.selectedSpec.clear();
  state.comboMode = false;
  state.qty = 1;
  $('qty-val').value = '1';
  const pr = $('pack-remarks'); if (pr) pr.value = '';   // 備註已移除 → 容錯
  updatePackPrice();
}

// ============= 渲染購物車 =============
function cartItemHtml(p, idx, removable) {
  const prefsTxt = p.preferences.join('、');
  const specTxt = p.special_items.length
    ? p.special_items.map(s => `${s.item_name}（+NT$${s.price}）`).join('、')
    : '';
  const remarksTxt = (p.pack_remarks && p.pack_remarks !== '無') ? p.pack_remarks : '';
  return `
    ${removable ? `<button class="cart-remove" data-action="remove" data-idx="${idx}" aria-label="移除這包" title="移除">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M18 6L6 18"/><path d="M6 6l12 12"/>
      </svg>
    </button>` : ''}
    <div class="top">
      <span class="num">${String(idx+1).padStart(2,'0')}</span>
      <span class="kind">組合包</span>
      <span class="qty">× ${p.quantity}</span>
      <span class="price"><span class="ccy">NT$</span>${p.line_total}</span>
    </div>
    <div class="body">
      <div><span class="lbl">偏好</span>${esc(prefsTxt)}</div>
      ${specTxt ? `<div><span class="lbl">特殊</span>${esc(specTxt)}</div>` : ''}
      ${remarksTxt ? `<div><span class="lbl">備註</span>${esc(remarksTxt)}</div>` : ''}
    </div>
  `;
}

function renderCart() {
  // 每次 cart 變動都同步 localStorage（讓 LINE redirect 後不會清空）
  saveCart();

  const list = $('cart-list');
  if (state.cart.length === 0) {
    list.innerHTML = '<div class="cart-empty">尚未加入任何組合包<br>關掉這裡，從畫面組裝你的第一包</div>';
    $('cart-count').textContent = '尚未加入任何一包';
    updateTotal();
    return;
  }

  list.innerHTML = '';
  state.cart.forEach((p, idx) => {
    const item = document.createElement('div');
    item.className = 'cart-item';
    if (idx === state.cart.length - 1) item.classList.add('highlight');
    item.innerHTML = cartItemHtml(p, idx, true);
    list.appendChild(item);
  });

  list.querySelectorAll('button[data-action="remove"]').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      const i = parseInt(b.dataset.idx, 10);
      state.cart.splice(i, 1);
      renderCart();
      toast('已移除一筆', '');
    });
  });

  const totalPacks = state.cart.reduce((s, p) => s + p.quantity, 0);
  $('cart-count').textContent = `共 ${state.cart.length} 種組合，${totalPacks} 包`;
  updateTotal();
}

function updateTotal() {
  const subtotal = state.cart.reduce((s, p) => s + p.line_total, 0);
  const qty = state.cart.reduce((s, p) => s + p.quantity, 0);
  const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  setTxt('total-qty', `${qty} 包`);
  setTxt('grand-total', subtotal);
  setTxt('grand-total-2', subtotal);
  setTxt('checkout-amount', subtotal);
  setTxt('checkout-amount-2', subtotal);

  const empty = subtotal === 0;
  const toCk = $('to-checkout-btn'); if (toCk) toCk.disabled = empty;
  // 取貨地點未選 → 不能送單（與後端 400 一致，先在前端擋下）
  const ck = $('checkout-btn'); if (ck) ck.disabled = empty || !selectedPickup();

  updateFab();
}

// ============= 懸浮列上的購物車按鈕（數量左邊）=============
function updateFab() {
  const packs = state.cart.reduce((s, p) => s + p.quantity, 0);
  // 舊的右下角/右上角入口都已移除 → 殘留元素做 null 防護
  const fc = $('fab-count'); if (fc) fc.textContent = `${packs} 包`;
  const ft = $('fab-total'); if (ft) ft.textContent = state.cart.reduce((s, p) => s + p.line_total, 0);
  const dot = $('fab-dot'); if (dot) dot.textContent = packs;
  const fab = $('cart-fab'); if (fab) fab.classList.toggle('empty', state.cart.length === 0);

  // 懸浮列購物車按鈕的紅圈數字徽章（0 時隱藏）
  const cartBadge = $('pab-cart-badge');
  if (cartBadge) {
    cartBadge.textContent = packs > 99 ? '99+' : String(packs);
    cartBadge.classList.toggle('zero', packs === 0);
  }
}
function bumpFab() {
  // 加入購物車時讓懸浮列的購物車按鈕跳一下，提供回饋
  const btn = $('pab-cart-btn');
  if (!btn) return;
  btn.classList.remove('bump');
  void btn.offsetWidth;  // reflow 重新觸發動畫
  btn.classList.add('bump');
}

// ============= 抽屜（購物車兩步驟 + 我的訂單）=============
function openCartDrawer() {
  goStep('cart');
  $('cart-drawer').classList.add('open');
  document.body.classList.add('drawer-open');
}
async function openMyOrdersDrawer() {
  await refreshServerOrders();
  // 預設開到「準備中」分頁
  renderMyOrdersDrawer('preparing');
  $('orders-drawer').classList.add('open');
  document.body.classList.add('drawer-open');
}
function closeDrawers() {
  $('cart-drawer').classList.remove('open');
  const od = $('orders-drawer');
  if (od) od.classList.remove('open');
  document.body.classList.remove('drawer-open');
}
function goStep(which) {
  const track = $('drawer-track');
  track.classList.toggle('to-checkout', which === 'checkout');
}

// 結帳前的訂單預覽
function renderRecap() {
  const recap = $('checkout-recap');
  if (!recap) return;
  if (state.cart.length === 0) { recap.innerHTML = ''; return; }
  recap.innerHTML = `<div class="recap-title">訂單內容</div>`;
  state.cart.forEach((p, idx) => {
    const item = document.createElement('div');
    item.className = 'cart-item recap-item';
    item.innerHTML = cartItemHtml(p, idx, false);
    recap.appendChild(item);
  });
}

// ============= 購物車 localStorage 持久化（避免 LINE redirect 後 cart 被清空）=============
function saveCart() {
  try { localStorage.setItem(CART_KEY, JSON.stringify(state.cart)); }
  catch (e) { /* 容量/隱私模式 */ }
}
function loadCart() {
  try {
    const arr = JSON.parse(localStorage.getItem(CART_KEY));
    if (Array.isArray(arr)) state.cart = arr;
  } catch (e) { /* 略 */ }
}
function clearCartStorage() {
  try { localStorage.removeItem(CART_KEY); } catch (e) { /* 略 */ }
}

// ============= 訂單顯示（純 server — 現金模式強制 LINE 登入，訂單一律在 server）=============
/** 從 server 拉用戶訂單更新 state.serverOrders */
async function refreshServerOrders() {
  if (!state.line) {
    state.serverOrders = [];
    return;
  }
  try {
    const r = await fetch('/api/orders/mine');
    if (r.ok) {
      const j = await r.json();
      state.serverOrders = Array.isArray(j.orders) ? j.orders : [];
    }
  } catch (e) { /* 靜默 */ }
}

// 準備中：訂單成立、尚未取貨（含備貨中 / 可取貨）
function getPreparingForDisplay() {
  return state.serverOrders
    .filter(o => categorizeOrder(o) === 'preparing')
    .map(serverToCard);
}

// 已完成：已取貨 / 已取消 / 已退貨
function getDoneForDisplay() {
  return state.serverOrders
    .filter(o => categorizeOrder(o) === 'done')
    .map(serverToCard);
}

function serverToCard(o) {
  return {
    order_id: o.order_id || '',
    name: o.customer_name || '',
    amount: Number(o.grand_total) || 0,
    status: o.payment_status === 'pending_check' ? 'reported' : 'unpaid',
    payment_method: o.payment_method,
    payment_status: o.payment_status || 'unpaid',
    is_confirmed: !!o.is_confirmed,
    is_picked_up: !!o.is_picked_up,
    is_notified: !!o.is_notified,
    order_status: o.order_status || 'active',
    ship_date: o.expected_ship_date || '',
    pickup_location: o.pickup_location || '',
    created_at: o.order_time ? Date.parse(o.order_time) : Date.now(),
    source: 'server',
    bucket: categorizeOrder(o),
  };
}

/** 統一 UI 同步：拉 server → 更新徽章 + 抽屜（若開著）re-render */
async function refreshMyOrdersUI() {
  await refreshServerOrders();
  updateMyOrdersBadge();
  if ($('orders-drawer') && $('orders-drawer').classList.contains('open')) {
    const activeTab = document.querySelector('.orders-tab.active')?.dataset.tab || 'preparing';
    renderMyOrdersDrawer(activeTab);
  }
}

/** 紅色徽章 = 準備中訂單數；同時決定「我的訂單」按鈕是否顯示 */
function updateMyOrdersBadge() {
  const badge = $('my-orders-badge');
  const btn = $('my-orders-btn');
  if (!badge) return;

  const preparingCount = state.line
    ? state.serverOrders.filter(o => categorizeOrder(o) === 'preparing').length
    : 0;
  const hasAny = state.line && state.serverOrders.length > 0;

  badge.textContent = preparingCount > 99 ? '99+' : String(preparingCount);
  badge.hidden = preparingCount === 0;

  // 按鈕顯示條件：登入即顯示（讓用戶能查歷史）
  if (btn) btn.hidden = !(state.line || hasAny);
}

/**
 * 渲染「我的訂單」抽屜（兩個 tab：準備中 / 已完成）
 * @param {string} activeTab — 'preparing' / 'done'
 */
async function renderMyOrdersDrawer(activeTab) {
  await refreshServerOrders();
  const preparing = getPreparingForDisplay();
  const done = getDoneForDisplay();

  $('tab-count-preparing').textContent = preparing.length;
  $('tab-count-done').textContent = done.length;

  document.querySelectorAll('.orders-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === activeTab);
  });
  document.querySelectorAll('.orders-pane').forEach(p => {
    const isActive = p.id === 'orders-pane-' + activeTab;
    p.hidden = !isActive;
    p.classList.toggle('active', isActive);
  });

  renderOrdersList($('preparing-list'), preparing, 'preparing');
  renderOrdersList($('done-list'), done, 'done');

  updateMyOrdersBadge();
}

function renderOrdersList(container, items, bucket) {
  if (!container) return;
  if (items.length === 0) {
    const emptyMsg = {
      preparing: '目前沒有準備中的訂單',
      done: '最近 60 天沒有已完成的訂單',
    }[bucket] || '沒有訂單';
    container.innerHTML = `<div class="pending-empty">${emptyMsg}</div>`;
    return;
  }
  container.innerHTML = '';
  items.forEach(o => {
    const d = new Date(o.created_at);
    const timeTxt = `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
    let statusText, statusClass;
    if (bucket === 'preparing') {
      if (o.is_notified) {
        statusText = '✅ 可取貨，請帶現金來取';
        statusClass = 'shipping';
      } else {
        statusText = '訂單成立 · 備貨中';
        statusClass = 'unpaid';
      }
    } else {
      // done bucket：已取消 / 已退貨 / 已完成取貨
      if (o.order_status === 'cancelled') {
        statusText = '✗ 已取消';
        statusClass = 'cancelled';
      } else if (o.order_status === 'refunded') {
        statusText = '↺ 已退貨';
        statusClass = 'refunded';
      } else {
        statusText = '✓ 已完成取貨';
        statusClass = 'done';
      }
    }

    // 準備中 + 還沒被店家「確認訂單」→ 可自己取消
    let actionsHtml = '';
    const canCancel = (bucket === 'preparing' && !o.is_confirmed);
    if (canCancel) {
      actionsHtml = `
      <div class="pi-actions">
        <button class="btn btn-danger btn-sm" data-act="cancel" type="button">取消訂單</button>
      </div>`;
    }

    const card = document.createElement('div');
    card.className = 'pending-item ' + statusClass;
    card.innerHTML = `
      <div class="pi-top">
        <span class="pi-status">${statusText}</span>
        <span class="pi-time">${esc(timeTxt)}</span>
      </div>
      <div class="pi-mid">
        <div class="pi-meta">
          <div class="pi-oid">#<span class="pi-oid-val"></span></div>
          <div class="pi-name"></div>
          <div class="pi-pickup"></div>
        </div>
        <div class="pi-amount"><span class="ccy">NT$</span>${Number(o.amount) || 0}</div>
      </div>
      ${actionsHtml}
    `;
    card.querySelector('.pi-oid-val').textContent = o.order_id;
    card.querySelector('.pi-name').textContent = o.name;
    const pickupNode = card.querySelector('.pi-pickup');
    pickupNode.textContent = PICKUP_LABELS[o.pickup_location] || '';
    pickupNode.hidden = !PICKUP_LABELS[o.pickup_location];

    const cancelBtn = card.querySelector('[data-act="cancel"]');
    if (cancelBtn) cancelBtn.addEventListener('click', () => customerCancelOrder(o.order_id));

    container.appendChild(card);
  });
}

// 用戶取消訂單（店家確認訂單前、is_confirmed=false 時可取消）
async function customerCancelOrder(orderId) {
  if (!confirm(`確定取消訂單 #${orderId}？\n取消後無法復原。`)) return;
  try {
    const r = await fetch(`/api/orders/${encodeURIComponent(orderId)}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const j = await r.json();
    if (j.ok) {
      toast('訂單已取消', 'success');
      await refreshMyOrdersUI();
      renderMyOrdersDrawer('preparing');
    } else {
      toast(j.error || '取消失敗（可能店家已確認訂單，請聯繫客服）', 'error');
    }
  } catch (e) {
    toast('網路錯誤，請重試', 'error');
  }
}

// ============= 結帳 =============
async function checkout() {
  // 會員制（B）：必須已 LINE 登入、且已完成會員登記才能下單
  if (!state.line) {
    toast('請先用 LINE 登入才能下單', 'error');
    return;
  }
  if (!state.line.bound_phone) {
    toast('您尚未完成會員登記：請到我們的 LINE 輸入「登記」綁定後再回來下單', 'error');
    return;
  }
  // 一律以「會員登記的姓名 / 電話」下單（與後端一致，避免冒用他人身分）
  const name = state.line.bound_name || '';
  const phone = state.line.bound_phone || '';
  const remarks = $('order-remarks').value.trim();
  const pickup = selectedPickup();
  if (!pickup) {
    toast('請選擇取貨地點', 'error');
    return;
  }

  if (state.cart.length === 0) {
    toast('購物車為空', 'error');
    return;
  }

  const btn = $('checkout-btn');
  btn.disabled = true;
  btn.querySelector('.label').textContent = '處理中…';

  try {
    const r = await fetch('/api/orders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        customer_name: name,
        customer_phone: phone,
        order_remarks: remarks,
        pickup_location: pickup,
        packs: state.cart,
      })
    });
    const j = await r.json();
    if (j.ok) {
      const orderId = j.order_id || '';
      // 若已登入 LINE → 更新本機 state 的綁定資訊（下次自動帶入）
      if (state.line) {
        state.line.bound_name = name;
        state.line.bound_phone = phone;
      }
      // 清空購物車並關閉抽屜（localStorage 也清掉，下次進來不會殘留）
      state.cart = [];
      clearCartStorage();
      $('cust-name').value = '';
      $('cust-phone').value = '';
      $('order-remarks').value = '';
      clearPickupChoice();
      renderCart();
      resetBuilder();
      closeDrawers();
      // 同步「我的訂單」徽章（新增一筆準備中）
      refreshMyOrdersUI().catch(() => {});
      // 開啟訂單成立確認 modal
      fillPayModal({ order_id: orderId, name: name, amount: j.grand_total, pickup: pickup });
    } else {
      toast(j.error || '送出失敗', 'error');
    }
  } catch (e) {
    toast('網路錯誤，請重試', 'error');
  } finally {
    btn.querySelector('.label').textContent = '送出訂單';
    btn.disabled = state.cart.length === 0;
  }
}

// ============= LINE LIFF =============
/**
 * 初始化 LIFF：
 * - 若 LIFF_ID 為空 → 整段降級，連 sdk 都不會載入
 * - 若 isLoggedIn → 拿 idToken 給後端驗 → 更新 state.line 並渲染 UI
 * - 否則 → 顯示「用 LINE 登入」按鈕等使用者主動點
 */
async function initLiff() {
  console.log('[LIFF] LIFF_ID =', LIFF_ID || '(empty)');
  if (!LIFF_ID) {
    console.warn('[LIFF] 跳過 — LIFF_ID 環境變數沒設');
    return;
  }
  if (typeof liff === 'undefined') {
    console.error('[LIFF] SDK 沒載入（檢查 CSP / Network tab）');
    toast('LIFF SDK 載入失敗（可能 CSP 或網路擋）', 'error');
    return;
  }
  try {
    await liff.init({ liffId: LIFF_ID });
    console.log('[LIFF] init OK, isLoggedIn=', liff.isLoggedIn());
  } catch (e) {
    console.error('[LIFF] init 失敗', e);
    toast('LIFF 初始化失敗：' + (e && e.message ? e.message : e), 'error');
    return;
  }

  if (!liff.isLoggedIn()) {
    console.log('[LIFF] 未登入，顯示登入按鈕');
    renderLineAccount(null);
    return;
  }

  const idToken = liff.getIDToken();
  console.log('[LIFF] getIDToken returned:', idToken ? '(JWT, len=' + idToken.length + ')' : 'NULL');
  if (!idToken) {
    console.error('[LIFF] getIDToken() 回 null — 多半是 LIFF Scope 沒勾 openid。請去 LINE Developer Console 把 openid 勾起來。');
    toast('LIFF 拿不到 ID Token — 請去 Console 把 Scope 的 openid 勾起來', 'error');
    renderLineAccount(null);
    return;
  }

  let r;
  try {
    r = await fetch('/api/line/verify', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': CSRF_TOKEN,
      },
      body: JSON.stringify({ idToken }),
    });
  } catch (e) {
    console.error('[LIFF] fetch /api/line/verify 網路失敗', e);
    toast('連線到伺服器失敗：' + e.message, 'error');
    renderLineAccount(null);
    return;
  }

  console.log('[LIFF] /api/line/verify status =', r.status);
  if (!r.ok) {
    let body = '';
    try { body = JSON.stringify(await r.json()); } catch { /* 略 */ }
    console.error('[LIFF] /api/line/verify 失敗', r.status, body);
    if (r.status === 503) {
      toast('伺服器端 LINE_LOGIN_CHANNEL_ID 環境變數沒設', 'error');
    } else if (r.status === 401) {
      toast('ID Token 驗證失敗 — 多半 Channel ID 跟 LIFF Channel 不符', 'error');
    } else {
      toast(`登入驗證失敗（${r.status}）：${body}`, 'error');
    }
    renderLineAccount(null);
    return;
  }

  const profile = await r.json();
  // 不在 console 印完整 profile（含電話 PII），只印一個確認字
  console.log('[LIFF] verify OK, line_id=' + String(profile.line_id || '').slice(0, 8) + '… name=' + (profile.bound_name || profile.display_name || '—'));
  state.line = profile;
  renderLineAccount(profile);
  prefillCheckoutFromLine();
  // 登入前若已加過商品 → 登入完成自動把購物車 UI 打開（接續結帳流程）
  maybeReopenCartAfterLogin();
  // 登入後同步「我的訂單」資料 + 紅色徽章
  refreshMyOrdersUI().catch(() => {});
}

/** 登入導回後：若登入前有設旗標且購物車仍有東西 → 自動開啟購物車抽屜 */
function maybeReopenCartAfterLogin() {
  let flag = null;
  try { flag = localStorage.getItem('veg_reopen_cart'); } catch (e) { /* 略 */ }
  if (flag === '1') {
    try { localStorage.removeItem('veg_reopen_cart'); } catch (e) { /* 略 */ }
    if (state.cart.length > 0) {
      openCartDrawer();
    }
  }
}

/**
 * 渲染右上角 LINE 區。
 * - profile null → 顯示「用 LINE 登入」按鈕
 * - profile 有值 → 顯示頭像 + 姓名 + 下拉
 */
function renderLineAccount(profile) {
  const loginBtn = $('line-login-btn');
  const acctBtn = $('line-account-btn');
  const myOrdersBtn = $('my-orders-btn');
  const ddNameEl = $('line-dd-name');
  const ddPhoneEl = $('line-dd-phone');
  if (!loginBtn || !acctBtn) return; // LIFF_ID 未設則這些元素根本沒 render

  if (!profile) {
    // 未登入：登入按鈕現、頭像收、我的訂單預設收（如果有本機待付款再由 updateMyOrdersBadge 開回來）
    loginBtn.hidden = false;
    acctBtn.hidden = true;
    if (myOrdersBtn) myOrdersBtn.hidden = true;
    $('line-dropdown').hidden = true;
    updateMyOrdersBadge();      // 匿名但有本機待付款 → 會把按鈕再打開
    return;
  }
  // 已登入：頭像 + 我的訂單 都直接顯示（不依賴非同步流程）
  loginBtn.hidden = true;
  acctBtn.hidden = false;
  if (myOrdersBtn) myOrdersBtn.hidden = false;

  const displayName = profile.display_name || profile.bound_name || 'LINE 用戶';
  const avatar = $('line-avatar');
  if (profile.picture_url) {
    avatar.src = profile.picture_url;
    avatar.alt = displayName;
  }
  // 下拉內容（點頭像才會看到）
  ddNameEl.textContent = profile.bound_name || displayName;
  ddPhoneEl.textContent = profile.bound_phone || '尚未綁定電話';
}

function prefillCheckoutFromLine() {
  if (!state.line) return;
  const nameInput = $('cust-name');
  const phoneInput = $('cust-phone');
  // 只在欄位是空的時候才填，避免覆蓋使用者已輸入的值
  if (nameInput && !nameInput.value.trim()) {
    nameInput.value = state.line.bound_name || state.line.display_name || '';
  }
  if (phoneInput && !phoneInput.value.trim() && state.line.bound_phone) {
    phoneInput.value = state.line.bound_phone;
  }
}

function toggleLineDropdown(force) {
  const acctBtn = $('line-account-btn');
  const dd = $('line-dropdown');
  if (!acctBtn || !dd) return;
  const open = force !== undefined ? force : dd.hidden;
  dd.hidden = !open;
  acctBtn.setAttribute('aria-expanded', String(open));
}

async function lineLogin() {
  if (!LIFF_ID || typeof liff === 'undefined') return;
  // 若購物車已有東西，記一個旗標：登入導回後自動把購物車打開（跨頁面 reload 用 localStorage 保存）
  try {
    if (state.cart.length > 0) localStorage.setItem('veg_reopen_cart', '1');
  } catch (e) { /* 隱私模式略過 */ }
  // ⚠ 不要傳 redirectUri — 讓 LIFF SDK 用它自己的 canonical URL
  //   (https://liff.line.me/<LIFF_ID>)。這條 URL 由 LINE 自動授權，
  //   完全不依賴 Channel 的 Callback URL list。
  //   登入完成後，LIFF 會自動把使用者導向 LIFF 設定裡的 Endpoint URL。
  liff.login();
}

async function lineLogout() {
  try {
    await fetch('/api/line/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': CSRF_TOKEN },
    });
  } catch (e) { /* 靜默 */ }
  try {
    if (typeof liff !== 'undefined' && liff.isLoggedIn()) liff.logout();
  } catch (e) { /* 靜默 */ }
  // 登出後直接重整網頁 — 一個乾淨的 fresh state，避免半登入狀態殘留
  location.reload();
}

// ============= 訂單成立確認 modal（取貨付現）=============
// 取貨地點（值與 app.py 的 PICKUP_LOCATIONS 一致）
const PICKUP_LABELS = { tamsui: '淡水廠　2 樓大廳', qiyan: '奇岩廠　1 樓大廳' };

function selectedPickup() {
  const el = document.querySelector('input[name="pickup-location"]:checked');
  return el ? el.value : '';
}

function clearPickupChoice() {
  document.querySelectorAll('input[name="pickup-location"]').forEach(r => { r.checked = false; });
}

function fillPayModal({ order_id, name, amount, pickup }) {
  $('pay-order-id').textContent = order_id || '—';
  $('pay-name').textContent = name || '—';
  const pickupEl = $('pay-pickup');
  if (pickupEl) pickupEl.textContent = PICKUP_LABELS[pickup] || '—';
  const amt = Number(amount) || 0;
  $('pay-amount').textContent = amt;
  const amt2 = $('pay-amount-2');
  if (amt2) amt2.textContent = amt;
  closeDrawers();
  $('success-modal').classList.add('open');
}

function closePayModal() {
  $('success-modal').classList.remove('open');
}

function bindPayModal() {
  // 成立確認 modal：「我知道了」關閉 + 點背景關閉
  const okBtn = $('order-ok-btn');
  if (okBtn) okBtn.addEventListener('click', closePayModal);
  const modal = $('success-modal');
  if (modal) modal.addEventListener('click', (e) => {
    if (e.target === modal) closePayModal();
  });
}

// ============= 初始化 =============
document.addEventListener('DOMContentLoaded', () => {
  bindPayModal();
  setTodayLabel();
  loadVeg();            // 從 CMS 載入商品並渲染（含供應狀態）
  bindStepper();
  // 從 localStorage 還原購物車（讓 LINE redirect / 重整後購物車仍在）
  loadCart();
  renderCart();

  $('add-cart-btn').addEventListener('click', addPackToCart);

  // 購物車入口：懸浮列上的購物車按鈕（在數量左邊）
  const pabCartBtn = $('pab-cart-btn');
  if (pabCartBtn) pabCartBtn.addEventListener('click', openCartDrawer);

  // LINE 登入 / 登出 / 下拉
  const loginBtn = $('line-login-btn');
  if (loginBtn) loginBtn.addEventListener('click', lineLogin);
  const acctBtn = $('line-account-btn');
  if (acctBtn) {
    acctBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleLineDropdown();
    });
  }
  const logoutBtn = $('line-logout-btn');
  if (logoutBtn) logoutBtn.addEventListener('click', lineLogout);

  // 我的訂單按鈕 → 開抽屜
  const myOrdersBtn = $('my-orders-btn');
  if (myOrdersBtn) myOrdersBtn.addEventListener('click', openMyOrdersDrawer);

  // 我的訂單抽屜的 tab 切換
  document.querySelectorAll('.orders-tab').forEach(t => {
    t.addEventListener('click', () => {
      renderMyOrdersDrawer(t.dataset.tab);
    });
  });
  // 我的訂單抽屜：點背景關閉
  const ordersBackdrop = $('orders-drawer');
  if (ordersBackdrop) {
    ordersBackdrop.addEventListener('click', (e) => {
      if (e.target === ordersBackdrop) closeDrawers();
    });
  }
  // 點 dropdown 外面收起
  document.addEventListener('click', (e) => {
    const dd = $('line-dropdown');
    if (!dd || dd.hidden) return;
    const ab = $('line-account-btn');
    if (e.target === ab || (ab && ab.contains(e.target))) return;
    if (e.target === dd || dd.contains(e.target)) return;
    toggleLineDropdown(false);
  });

  // 前往結帳（第一層 → 第二層）
  $('to-checkout-btn').addEventListener('click', () => {
    if (state.cart.length === 0) { toast('購物車為空', 'error'); return; }

    // ⚠ 強制 LINE 登入才能下單（只在 LIFF 啟用時生效；沒設 LIFF_ID 就降級允許匿名）
    if (LIFF_ID && !state.line) {
      closeDrawers();
      $('login-required-modal').classList.add('open');
      return;
    }

    // 結帳前若已登入 LINE 但欄位尚未填，再次嘗試自動帶入
    prefillCheckoutFromLine();
    renderRecap();
    goStep('checkout');
    setTimeout(() => $('cust-name').focus(), 320);
  });

  // 「需登入」modal 動作
  const lrModal = $('login-required-modal');
  if (lrModal) {
    $('lr-cancel-btn').addEventListener('click', () => lrModal.classList.remove('open'));
    $('lr-login-btn').addEventListener('click', () => {
      // 觸發 LIFF OAuth；reload 後 cart 會從 localStorage 還原
      lineLogin();
    });
    // 點背景關閉
    lrModal.addEventListener('click', (e) => {
      if (e.target === lrModal) lrModal.classList.remove('open');
    });
  }
  // 返回購物車（第二層 → 第一層）
  $('back-to-cart').addEventListener('click', () => goStep('cart'));
  // 送出訂單
  $('checkout-btn').addEventListener('click', checkout);
  document.querySelectorAll('input[name="pickup-location"]').forEach(r => {
    r.addEventListener('change', updateTotal);
  });

  // 關閉抽屜（× 按鈕 + 點背景）
  document.querySelectorAll('[data-close-drawer]').forEach(b => {
    b.addEventListener('click', closeDrawers);
  });
  const cartBackdrop = $('cart-drawer');
  if (cartBackdrop) {
    cartBackdrop.addEventListener('click', (e) => {
      if (e.target === cartBackdrop) closeDrawers();
    });
  }

  // ESC 關閉抽屜（兩個都會收）
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const cartOpen = $('cart-drawer').classList.contains('open');
    const ordersOpen = $('orders-drawer') && $('orders-drawer').classList.contains('open');
    if (cartOpen || ordersOpen) closeDrawers();
  });

  updatePackPrice();

  // 進站時先評估「我的訂單」按鈕該不該顯示（為了匿名用戶有 localStorage 待付款的情況）
  updateMyOrdersBadge();

  // 回到頂部 FAB（桌機監聽 .pos-main 內部捲動；手機監聽 document 捲動）
  setupBackToTop();

  // 啟動 LIFF 流程（會非同步驗證 ID Token 並更新右上角 UI）
  initLiff().catch(err => console.warn('[LIFF] startup error', err));
});

function setupBackToTop() {
  const fab = $('back-to-top');
  if (!fab) return;
  const posMain = document.querySelector('.pos-main');
  const SCROLL_THRESHOLD = 320;
  const getScrollTop = () => Math.max(
    window.scrollY || document.documentElement.scrollTop || 0,
    posMain ? posMain.scrollTop : 0
  );
  const onScroll = () => {
    fab.classList.toggle('visible', getScrollTop() > SCROLL_THRESHOLD);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  if (posMain) posMain.addEventListener('scroll', onScroll, { passive: true });
  fab.addEventListener('click', () => {
    // 兩個 scroll container 都拉回頂（其中一個本來就是 0 不會有副作用）
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (posMain) posMain.scrollTo({ top: 0, behavior: 'smooth' });
  });
  onScroll();
}
