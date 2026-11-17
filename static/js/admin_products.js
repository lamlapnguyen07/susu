// ===================== 商品管理 CMS =====================
'use strict';

const $ = (id) => document.getElementById(id);

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---- CSRF / fetch ----
const CSRF = window.__CSRF__ || '';
function authFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const method = (opts.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = CSRF;
  return fetch(url, Object.assign({}, opts, { headers, credentials: 'same-origin' }));
}

// ---- Toast ----
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
  }, 2600);
}

// ---- Confirm modal ----
function confirmAction(title, body, onOk) {
  $('confirm-title').textContent = title;
  $('confirm-body').textContent = body;
  $('confirm-modal').classList.add('open');
  const ok = $('confirm-ok'), cancel = $('confirm-cancel');
  const close = () => $('confirm-modal').classList.remove('open');
  const handler = () => { close(); ok.removeEventListener('click', handler); onOk(); };
  ok.addEventListener('click', handler);
  cancel.onclick = close;
}

// ---- Idle timeout / logout ----
const IDLE_MIN = window.__IDLE_MIN__ || 15;
let lastActivity = Date.now();
['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(ev =>
  document.addEventListener(ev, () => { lastActivity = Date.now(); }, { passive: true }));
function updateIdleTimer() {
  const remain = Math.max(0, IDLE_MIN * 60 - (Date.now() - lastActivity) / 1000);
  const mm = Math.floor(remain / 60), ss = Math.floor(remain % 60);
  const el = $('idle-timer');
  el.textContent = `自動登出 ${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
  el.classList.toggle('warn', remain < 120);
  if (remain <= 0) authFetch('/api/logout', { method: 'POST' }).finally(() => location.href = '/login');
}
setInterval(updateIdleTimer, 1000);
setInterval(() => authFetch('/api/heartbeat', { method: 'POST' }).catch(() => {}), 120000);
$('logout-btn').onclick = () =>
  authFetch('/api/logout', { method: 'POST' }).finally(() => location.href = '/login');

// ===================== 狀態 =====================
const state = { products: [], byId: {}, farms: [], farmById: {}, editingId: null, uploadSlot: null };
const STOCK_LABEL = { ok: '這週有貨', low: '剩少量', next: '下週採收' };

async function loadFarms() {
  try {
    const r = await authFetch('/api/admin/farms');
    const data = r.ok ? await r.json() : [];
    state.farms = Array.isArray(data) ? data : [];
  } catch (e) { state.farms = []; }
  state.farmById = {};
  state.farms.forEach(f => { state.farmById[f.id] = f; });
  $('f-farm').innerHTML = '<option value="">— 請選擇農場 —</option>' + state.farms.map(f =>
    `<option value="${f.id}">${esc(f.name)}${f.is_active ? '' : '（下架中）'}</option>`).join('');
}

// ===================== 載入 + 渲染 =====================
async function loadProducts() {
  try {
    const r = await fetch('/api/products', { credentials: 'same-origin' });
    if (!r.ok) throw new Error('http ' + r.status);
    const data = await r.json();
    state.products = Array.isArray(data) ? data : [];
    state.byId = {};
    state.products.forEach(p => { state.byId[p.id] = p; });
    renderGrid();
  } catch (e) {
    $('prod-grid').innerHTML = '<div class="prod-empty">載入失敗，請重新整理頁面。</div>';
  }
}

function kindBadge(p) {
  if (p.kind === 'special') {
    return `<span class="prod-badge special">特殊 · NT$${Number(p.price) || 0} / ${esc(p.unit || '份')}</span>`;
  }
  if (p.kind === 'farm') {
    const f = state.farmById[p.farm_id];
    return `<span class="prod-badge farm">${esc(f ? f.name : '合作農場')} · NT$${Number(p.price) || 0} / ${esc(p.unit || '件')}</span>`;
  }
  return `<span class="prod-badge standard">一般萵苣</span>`;
}

function renderGrid() {
  const grid = $('prod-grid');
  if (!state.products.length) {
    grid.innerHTML = '<div class="prod-empty">還沒有任何商品，點右上角「＋ 新增商品」開始。</div>';
    return;
  }
  grid.innerHTML = state.products.map(p => {
    const photo = (p.photos && p.photos[0]) || '';
    const thumb = photo
      ? `<img src="${esc(photo)}" alt="${esc(p.name)}" loading="lazy">`
      : `<span class="prod-thumb-empty">無照片</span>`;
    const off = !p.is_available;
    return `
      <article class="prod-card${off ? ' is-off' : ''}" data-id="${p.id}">
        <div class="prod-thumb">${thumb}
          ${off ? '<span class="prod-off-tag">暫無供應</span>' : ''}
          <span class="prod-photo-count">${(p.photos || []).length}/2 張</span>
        </div>
        <div class="prod-body">
          <div class="prod-name-row">
            <h3 class="prod-name">${esc(p.name)}</h3>
            ${kindBadge(p)}
          </div>
          <p class="prod-en">${esc(p.name_en || '')}</p>
          <p class="prod-cat">${esc(p.category || '—')} · ${esc(STOCK_LABEL[p.stock_status] || '這週有貨')}</p>
          <p class="prod-desc-line">${esc(p.description || '')}</p>
        </div>
        <div class="prod-actions">
          <label class="avail-toggle" title="切換供應狀態">
            <input type="checkbox" class="avail-input" data-id="${p.id}" ${p.is_available ? 'checked' : ''}>
            <span class="avail-track"><span class="avail-thumb"></span></span>
            <span class="avail-text">${p.is_available ? '供應中' : '暫停'}</span>
          </label>
          <span class="prod-act-spacer"></span>
          <button class="btn btn-ghost btn-sm prod-edit" data-id="${p.id}" type="button">編輯</button>
          <button class="btn btn-ghost btn-sm prod-del" data-id="${p.id}" type="button">刪除</button>
        </div>
      </article>`;
  }).join('');

  grid.querySelectorAll('.prod-edit').forEach(b =>
    b.onclick = () => openModal(state.byId[b.dataset.id]));
  grid.querySelectorAll('.prod-del').forEach(b =>
    b.onclick = () => askDelete(state.byId[b.dataset.id]));
  grid.querySelectorAll('.avail-input').forEach(c =>
    c.onchange = () => quickToggle(state.byId[c.dataset.id], c));
}

// ===================== 供應狀態快速切換 =====================
async function quickToggle(p, checkbox) {
  if (!p) return;
  const want = checkbox.checked;
  const body = productPayload(p, { is_available: want });
  checkbox.disabled = true;
  try {
    const r = await authFetch(`/api/admin/products/${p.id}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'fail');
    p.is_available = want;
    toast(want ? `「${p.name}」已恢復供應` : `「${p.name}」已標為暫無供應`, 'success');
    renderGrid();
  } catch (e) {
    checkbox.checked = !want;
    toast('更新失敗：' + (e.message || ''), 'error');
  } finally {
    checkbox.disabled = false;
  }
}

// 把商品物件轉成 API 需要的 payload；overrides 可覆寫個別欄位
function productPayload(p, overrides = {}) {
  return Object.assign({
    name: p.name, name_en: p.name_en || '', category: p.category || '',
    kind: p.kind || 'standard', price: Number(p.price) || 0,
    unit: p.unit || '', farm_id: p.farm_id || null, stock_status: p.stock_status || 'ok',
    description: p.description || '', features: p.features || '',
    nutrition: p.nutrition || '',
    eating: p.eating || '', pairing: p.pairing || '',
    is_available: !!p.is_available, sort_order: Number(p.sort_order) || 0,
  }, overrides);
}

// ===================== 刪除 =====================
function askDelete(p) {
  if (!p) return;
  confirmAction('刪除商品？', `將永久刪除「${p.name}」，首頁與點餐頁會立即移除此商品。此操作無法復原。`, async () => {
    try {
      const r = await authFetch(`/api/admin/products/${p.id}/delete`, { method: 'POST' });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'fail');
      toast(`已刪除「${p.name}」`, 'success');
      loadProducts();
    } catch (e) {
      toast('刪除失敗：' + (e.message || ''), 'error');
    }
  });
}

// ===================== 編輯 / 新增 Modal =====================
function syncKindUI() {
  const kind = $('f-kind').value;
  const unitPriced = kind === 'special' || kind === 'farm';
  $('f-price-block').hidden = !unitPriced;
  $('f-unit-block').hidden = !unitPriced;
  $('f-farm-block').hidden = kind !== 'farm';
  $('f-price-label').textContent = kind === 'farm' ? '售價（每單位 NT$）' : '售價（特殊品項，每份 NT$）';
}

function openModal(p) {
  const isNew = !p;
  state.editingId = isNew ? null : p.id;
  $('prod-modal-title').textContent = isNew ? '新增商品' : '編輯商品';
  $('prod-modal-eyebrow').textContent = isNew ? 'New Product' : 'Edit Product';

  $('f-id').value = isNew ? '' : p.id;
  $('f-name').value = isNew ? '' : (p.name || '');
  $('f-name-en').value = isNew ? '' : (p.name_en || '');
  $('f-category').value = isNew ? '' : (p.category || '');
  $('f-kind').value = isNew ? 'standard' : (p.kind || 'standard');
  $('f-price').value = isNew ? '' : (p.price || '');
  $('f-unit').value = isNew ? '' : (p.unit && p.unit !== '包' ? p.unit : (p.kind === 'standard' ? '' : (p.unit || '')));
  $('f-farm').value = isNew ? '' : (p.farm_id || '');
  $('f-stock').value = isNew ? 'ok' : (p.stock_status || 'ok');
  $('f-sort').value = isNew ? '' : (p.sort_order || 0);
  $('f-desc').value = isNew ? '' : (p.description || '');
  $('f-features').value = isNew ? '' : (p.features || '');
  $('f-nutrition').value = isNew ? '' : (p.nutrition || '');
  $('f-eating').value = isNew ? '' : (p.eating || '');
  $('f-pairing').value = isNew ? '' : (p.pairing || '');
  $('f-available').checked = isNew ? true : !!p.is_available;
  syncKindUI();

  // 圖片：新商品先存再傳
  const photos = isNew ? [] : (p.photos || []);
  setSlotImg(1, photos[0] || '');
  setSlotImg(2, photos[1] || '');
  const lockImg = isNew;
  $('f-imgslot-1').disabled = lockImg;
  $('f-imgslot-2').disabled = lockImg;
  $('f-imgnote').style.display = lockImg ? 'block' : 'none';

  $('prod-modal').classList.add('open');
}

function setSlotImg(slot, url) {
  const img = $('f-img-' + slot), ph = $('f-imgph-' + slot);
  if (url) { img.src = url; img.style.display = 'block'; ph.style.display = 'none'; }
  else { img.removeAttribute('src'); img.style.display = 'none'; ph.style.display = 'flex'; }
}

function closeModal() { $('prod-modal').classList.remove('open'); }

$('f-kind').onchange = syncKindUI;
$('prod-add-btn').onclick = () => openModal(null);
$('prod-cancel').onclick = closeModal;
$('prod-modal').addEventListener('click', (e) => { if (e.target === $('prod-modal')) closeModal(); });

// ---- 儲存（建立 / 更新）----
$('prod-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('f-name').value.trim();
  if (!name) { toast('請填寫商品名稱', 'error'); $('f-name').focus(); return; }
  const kind = ['standard', 'special', 'farm'].includes($('f-kind').value) ? $('f-kind').value : 'standard';
  const unitPriced = kind !== 'standard';
  if (kind === 'farm' && !$('f-farm').value) { toast('請選擇這件商品屬於哪個合作農場', 'error'); $('f-farm').focus(); return; }
  if (unitPriced && !(parseInt($('f-price').value, 10) > 0)) { toast('請填售價', 'error'); $('f-price').focus(); return; }
  const body = {
    name, name_en: $('f-name-en').value.trim(), category: $('f-category').value.trim(),
    kind, price: unitPriced ? (parseInt($('f-price').value, 10) || 0) : 0,
    unit: unitPriced ? ($('f-unit').value.trim() || '件') : '包',
    farm_id: kind === 'farm' ? parseInt($('f-farm').value, 10) : null,
    stock_status: $('f-stock').value,
    description: $('f-desc').value.trim(), features: $('f-features').value.trim(),
    nutrition: $('f-nutrition').value.trim(),
    eating: $('f-eating').value.trim(), pairing: $('f-pairing').value.trim(),
    is_available: $('f-available').checked, sort_order: parseInt($('f-sort').value, 10) || 0,
  };
  const id = $('f-id').value;
  const url = id ? `/api/admin/products/${id}` : '/api/admin/products';
  const saveBtn = $('prod-save');
  saveBtn.disabled = true; saveBtn.textContent = '儲存中…';
  try {
    const r = await authFetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'fail');
    toast(id ? '已更新商品' : '已建立商品，現在可以上傳照片了', 'success');
    if (!id && d.id) {
      // 新建立 → 重新載入並重新打開該商品，讓使用者接著上傳圖
      await loadProducts();
      const created = state.byId[d.id];
      if (created) openModal(created);
    } else {
      closeModal();
      loadProducts();
    }
  } catch (e2) {
    toast('儲存失敗：' + (e2.message || ''), 'error');
  } finally {
    saveBtn.disabled = false; saveBtn.textContent = '儲存';
  }
});

// ---- 圖片上傳 ----
$('f-imgslot-1').onclick = () => triggerUpload(1);
$('f-imgslot-2').onclick = () => triggerUpload(2);
function triggerUpload(slot) {
  if (!$('f-id').value) { toast('請先按「儲存」建立商品，再上傳照片', 'error'); return; }
  state.uploadSlot = slot;
  const inp = $('f-img-input');
  inp.value = '';
  inp.click();
}
$('f-img-input').onchange = async function () {
  const file = this.files && this.files[0];
  const slot = state.uploadSlot;
  if (!file || !slot) return;
  if (file.size > 3 * 1024 * 1024) { toast('圖片過大（上限 3MB）', 'error'); return; }
  const id = $('f-id').value;
  const fd = new FormData();
  fd.append('slot', String(slot));
  fd.append('image', file);
  toast('上傳中…');
  try {
    // FormData：不要手動設 Content-Type，讓瀏覽器帶 boundary；但仍要帶 CSRF header
    const r = await fetch(`/api/admin/products/${id}/image`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRF-Token': CSRF }, body: fd,
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'fail');
    setSlotImg(slot, d.url + '?t=' + Date.now());
    toast('照片已更新', 'success');
    // 同步 state，避免關閉 modal 後卡片仍是舊圖
    const p = state.byId[id];
    if (p) { p.photos = p.photos || []; p.photos[slot - 1] = d.url; }
    renderGrid();
  } catch (e) {
    toast('上傳失敗：' + (e.message || ''), 'error');
  }
};

// ===================== 啟動 =====================
loadFarms().then(loadProducts);
// 從「合作農場」頁的「＋ 新增這家的商品」過來：?new=farm&farm=<id>
(function () {
  const q = new URLSearchParams(location.search);
  if (q.get('new') !== 'farm') return;
  loadFarms().then(() => {
    openModal(null);
    $('f-kind').value = 'farm';
    $('f-farm').value = q.get('farm') || '';
    syncKindUI();
  });
})();
