// ===================== 合作農場管理 =====================
'use strict';

const $ = (id) => document.getElementById(id);
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
const CSRF = window.__CSRF__ || '';
function authFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const method = (opts.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = CSRF;
  return fetch(url, Object.assign({}, opts, { headers, credentials: 'same-origin' }));
}
function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  $('toast-stack').appendChild(el);
  setTimeout(() => { el.style.transition = 'opacity 280ms'; el.style.opacity = '0'; setTimeout(() => el.remove(), 320); }, 3200);
}
function confirmAction(title, body, onOk) {
  $('confirm-title').textContent = title;
  $('confirm-body').textContent = body;
  $('confirm-modal').classList.add('open');
  const ok = $('confirm-ok');
  const close = () => $('confirm-modal').classList.remove('open');
  const handler = () => { close(); ok.removeEventListener('click', handler); onOk(); };
  ok.addEventListener('click', handler);
  $('confirm-cancel').onclick = () => { close(); ok.removeEventListener('click', handler); };
}

// ---- Idle timeout / logout（與其他後台頁一致）----
const IDLE_MIN = window.__IDLE_MIN__ || 15;
let lastActivity = Date.now();
['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(ev =>
  document.addEventListener(ev, () => { lastActivity = Date.now(); }, { passive: true }));
setInterval(() => {
  const remain = Math.max(0, IDLE_MIN * 60 - (Date.now() - lastActivity) / 1000);
  const el = $('idle-timer');
  el.textContent = `自動登出 ${String(Math.floor(remain / 60)).padStart(2, '0')}:${String(Math.floor(remain % 60)).padStart(2, '0')}`;
  el.classList.toggle('warn', remain < 120);
  if (remain <= 0) authFetch('/api/logout', { method: 'POST' }).finally(() => location.href = '/login');
}, 1000);
setInterval(() => authFetch('/api/heartbeat', { method: 'POST' }).catch(() => {}), 120000);
$('logout-btn').onclick = () => authFetch('/api/logout', { method: 'POST' }).finally(() => location.href = '/login');

// ---- 資料 ----
const state = { farms: [], byId: {}, counts: {}, uploadSlot: null };

async function load() {
  try {
    const [rf, rp] = await Promise.all([authFetch('/api/admin/farms'), fetch('/api/products')]);
    if (!rf.ok) throw new Error('http ' + rf.status);
    state.farms = await rf.json();
    const products = rp.ok ? await rp.json() : [];
    state.counts = {};
    (Array.isArray(products) ? products : []).forEach(p => {
      if (p.kind === 'farm' && p.farm_id) state.counts[p.farm_id] = (state.counts[p.farm_id] || 0) + 1;
    });
    state.byId = {};
    state.farms.forEach(f => { state.byId[f.id] = f; });
    render();
  } catch (e) {
    $('farm-grid').innerHTML = '<div class="prod-empty">載入失敗，請重新整理頁面。</div>';
  }
}

function render() {
  const grid = $('farm-grid');
  if (!state.farms.length) {
    grid.innerHTML = '<div class="prod-empty">還沒有合作農場，點右上角「＋ 新增農場」開始。</div>';
    return;
  }
  grid.innerHTML = state.farms.map(f => {
    const photo = (f.photos && f.photos[0]) || '';
    const n = state.counts[f.id] || 0;
    return `
      <article class="prod-card${f.is_active ? '' : ' is-off'}">
        <div class="prod-thumb">${photo ? `<img src="${esc(photo)}" alt="${esc(f.name)}" loading="lazy">` : '<span class="prod-thumb-empty">還沒有封面照</span>'}
          ${f.is_active ? '' : '<span class="prod-off-tag">下架中</span>'}
        </div>
        <div class="prod-body">
          <div class="prod-name-row"><h3 class="prod-name">${esc(f.name)}</h3><span class="prod-badge farm">${n} 件商品</span></div>
          <p class="prod-en">${esc(f.region || '')}</p>
          <p class="prod-desc-line">${esc(f.summary || '')}</p>
        </div>
        <div class="prod-actions">
          <a class="btn btn-ghost btn-sm" href="/admin-products-redirect" data-new-product="${f.id}">＋ 這家的商品</a>
          <a class="btn btn-ghost btn-sm" href="/farms/${f.id}" target="_blank" rel="noopener"${f.is_active ? '' : ' hidden'}>看前台</a>
          <span class="prod-act-spacer"></span>
          <button class="btn btn-ghost btn-sm" data-edit="${f.id}" type="button">編輯</button>
          <button class="btn btn-ghost btn-sm" data-del="${f.id}" type="button">刪除</button>
        </div>
      </article>`;
  }).join('');
  grid.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => openModal(state.byId[b.dataset.edit]));
  grid.querySelectorAll('[data-del]').forEach(b => b.onclick = () => askDelete(state.byId[b.dataset.del]));
  grid.querySelectorAll('[data-new-product]').forEach(a => {
    // 商品管理頁的路徑跟後台路徑走（ADMIN_PATH 可能自訂），沿用 header 上的連結
    const base = document.querySelector('a.redeem-link[href$="/products"]');
    a.href = (base ? base.getAttribute('href') : '/admin/products') + '?new=farm&farm=' + a.dataset.newProduct;
  });
}

function askDelete(f) {
  if (!f) return;
  confirmAction('刪除農場？', `將永久刪除「${f.name}」。如果只是暫時不賣，建議改成「下架」。`, async () => {
    try {
      const r = await authFetch(`/api/admin/farms/${f.id}/delete`, { method: 'POST' });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'fail');
      toast(`已刪除「${f.name}」`, 'success');
      load();
    } catch (e) { toast(e.message || '刪除失敗', 'error'); }
  });
}

// ---- Modal ----
function setSlotImg(slot, url) {
  const img = $('g-img-' + slot), ph = $('g-imgph-' + slot);
  if (url) { img.src = url; img.style.display = 'block'; ph.style.display = 'none'; }
  else { img.removeAttribute('src'); img.style.display = 'none'; ph.style.display = 'flex'; }
}
function openModal(f) {
  const isNew = !f;
  $('farm-modal-title').textContent = isNew ? '新增農場' : '編輯農場';
  $('farm-modal-eyebrow').textContent = isNew ? 'New Farm' : 'Edit Farm';
  $('g-id').value = isNew ? '' : f.id;
  $('g-name').value = isNew ? '' : f.name;
  $('g-region').value = isNew ? '' : (f.region || '');
  $('g-summary').value = isNew ? '' : (f.summary || '');
  $('g-story').value = isNew ? '' : (f.story || '');
  $('g-crops').value = isNew ? '' : (f.crops || '');
  $('g-cert').value = isNew ? '' : (f.certification || '');
  $('g-website').value = isNew ? '' : (f.website || '');
  $('g-sort').value = isNew ? '' : (f.sort_order || 0);
  $('g-active').checked = isNew ? true : !!f.is_active;
  const photos = isNew ? [] : (f.photos || []);
  setSlotImg(1, photos[0] || ''); setSlotImg(2, photos[1] || '');
  $('g-imgslot-1').disabled = isNew; $('g-imgslot-2').disabled = isNew;
  $('g-imgnote').style.display = isNew ? 'block' : 'none';
  $('farm-modal').classList.add('open');
  setTimeout(() => $('g-name').focus(), 50);
}
const closeModal = () => $('farm-modal').classList.remove('open');
$('farm-add-btn').onclick = () => openModal(null);
$('farm-cancel').onclick = closeModal;
$('farm-modal').addEventListener('click', (e) => { if (e.target === $('farm-modal')) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

$('farm-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('g-name').value.trim();
  if (!name) { toast('請填農場名稱', 'error'); $('g-name').focus(); return; }
  const website = $('g-website').value.trim();
  if (website && !/^https?:\/\//i.test(website)) { toast('網站請以 https:// 開頭', 'error'); $('g-website').focus(); return; }
  const body = {
    name, region: $('g-region').value.trim(), summary: $('g-summary').value.trim(),
    story: $('g-story').value.trim(), crops: $('g-crops').value.trim(),
    certification: $('g-cert').value.trim(), website,
    is_active: $('g-active').checked, sort_order: parseInt($('g-sort').value, 10) || 0,
  };
  const id = $('g-id').value;
  const btn = $('farm-save');
  btn.disabled = true; btn.textContent = '儲存中…';
  try {
    const r = await authFetch(id ? `/api/admin/farms/${id}` : '/api/admin/farms', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'fail');
    toast(id ? '已更新農場' : '已建立農場，現在可以上傳照片了', 'success');
    await load();
    if (!id && d.id && state.byId[d.id]) openModal(state.byId[d.id]);
    else closeModal();
  } catch (e2) {
    toast('儲存失敗：' + (e2.message || ''), 'error');
  } finally {
    btn.disabled = false; btn.textContent = '儲存';
  }
});

// ---- 照片上傳 ----
$('g-imgslot-1').onclick = () => pick(1);
$('g-imgslot-2').onclick = () => pick(2);
function pick(slot) {
  if (!$('g-id').value) { toast('請先按「儲存」建立農場，再上傳照片', 'error'); return; }
  state.uploadSlot = slot;
  const inp = $('g-img-input'); inp.value = ''; inp.click();
}
$('g-img-input').onchange = async function () {
  const file = this.files && this.files[0];
  const slot = state.uploadSlot, id = $('g-id').value;
  if (!file || !slot || !id) return;
  if (file.size > 3 * 1024 * 1024) { toast('圖片過大（上限 3MB）', 'error'); return; }
  const fd = new FormData();
  fd.append('slot', String(slot)); fd.append('image', file);
  toast('上傳中…');
  try {
    const r = await fetch(`/api/admin/farms/${id}/image`, {
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': CSRF }, body: fd,
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'fail');
    setSlotImg(slot, d.url + '?t=' + Date.now());
    toast('照片已更新', 'success');
    const f = state.byId[id];
    if (f) { f.photos = f.photos || []; f.photos[slot - 1] = d.url; render(); }
  } catch (e) { toast('上傳失敗：' + (e.message || ''), 'error'); }
};

load();
