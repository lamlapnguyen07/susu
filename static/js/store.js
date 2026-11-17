// ============================================================
// 食在好蔬 前台商店
// 購物車與 /pos 結帳頁共用 localStorage：
//   veg_cart        → 萵苣包（與 pos.js 相同格式）
//   veg_cart_items  → 單件商品 [{product_id, quantity}]（特殊品項 / 合作農場商品）
// 金額只是顯示用；送出訂單時一律由伺服器重算。
// ============================================================
(() => {
  'use strict';
  const S = window.__STORE__ || {};
  const COMBO = S.comboSentinel || '組合包（農場隨機搭配）';
  const PACK_LIMIT = S.packLimit || 10;
  const ITEM_MAX = S.itemMax || 20;
  const PACKS_KEY = 'veg_cart';
  const ITEMS_KEY = 'veg_cart_items';
  const catalog = new Map((S.catalog || []).map((p) => [p.id, p]));
  const lettuceByName = new Map((S.catalog || []).filter((p) => p.kind === 'standard').map((p) => [p.name, p]));
  const specialByName = new Map((S.catalog || []).filter((p) => p.kind === 'special').map((p) => [p.name, p]));

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (n) => 'NT$' + Number(n || 0).toLocaleString('zh-TW');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- storage ----------
  const read = (k) => { try { const v = JSON.parse(localStorage.getItem(k) || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 隱私模式：購物車只活在這一頁 */ } };
  const cart = { packs: read(PACKS_KEY), items: read(ITEMS_KEY) };
  const save = () => { write(PACKS_KEY, cart.packs); write(ITEMS_KEY, cart.items); };

  // ---------- pack helpers ----------
  const isComboPack = (p) => !p.preferences || !p.preferences.length || (p.preferences.length === 1 && p.preferences[0] === COMBO);
  const isSimple = (p) => !(p.special_items && p.special_items.length) && !(p.pack_remarks || '').trim();
  const packKey = (p) => {
    if (!isSimple(p)) return null;
    if (isComboPack(p)) return 'pack:__combo__';
    if (p.preferences.length === 1) return 'pack:' + p.preferences[0];
    return null;
  };
  const packUnit = (p) => {
    const base = isComboPack(p) ? S.comboPrice : S.pickPrice;
    const sp = (p.special_items || []).reduce((s, x) => {
      const c = specialByName.get(x.item_name);
      return s + (c ? c.price : Number(x.price) || 0);
    }, 0);
    return base + sp;
  };
  const packTotal = () => cart.packs.reduce((s, p) => s + (Number(p.quantity) || 0), 0);
  const findPack = (key) => cart.packs.findIndex((p) => packKey(p) === key);
  const findItem = (id) => cart.items.findIndex((x) => Number(x.product_id) === id);

  function qtyOf(key) {
    if (key.startsWith('pack:')) { const i = findPack(key); return i < 0 ? 0 : cart.packs[i].quantity; }
    const i = findItem(Number(key.slice(5))); return i < 0 ? 0 : cart.items[i].quantity;
  }

  // 設定某個商品的數量；回傳 { ok, qty, msg }
  function setQty(key, n) {
    n = Math.max(0, Math.floor(Number(n) || 0));
    if (key.startsWith('pack:')) {
      const i = findPack(key);
      const cur = i < 0 ? 0 : cart.packs[i].quantity;
      const room = PACK_LIMIT - (packTotal() - cur);
      let msg = '';
      if (n > room) { n = Math.max(0, room); msg = `每個帳號每天最多 ${PACK_LIMIT} 包萵苣`; }
      if (n <= 0) { if (i >= 0) cart.packs.splice(i, 1); }
      else if (i >= 0) { const p = cart.packs[i]; p.quantity = n; p.subtotal = packUnit(p); p.line_total = p.subtotal * n; }
      else {
        const name = key.slice(5);
        const p = { preferences: [name === '__combo__' ? COMBO : name], special_items: [], pack_remarks: '', quantity: n };
        p.subtotal = packUnit(p); p.line_total = p.subtotal * n;
        cart.packs.push(p);
      }
      return { ok: !msg, qty: n, msg };
    }
    const id = Number(key.slice(5));
    const c = catalog.get(id);
    const i = findItem(id);
    let msg = '';
    if (n > ITEM_MAX) { n = ITEM_MAX; msg = `每樣商品一次最多 ${ITEM_MAX} ${c ? c.unit : '件'}`; }
    if (n > 0 && c && !c.available) return { ok: false, qty: 0, msg: `${c.name} 目前暫無供應` };
    if (n <= 0) { if (i >= 0) cart.items.splice(i, 1); }
    else if (i >= 0) cart.items[i].quantity = n;
    else cart.items.push({ product_id: id, quantity: n });
    return { ok: !msg, qty: n, msg };
  }

  function lineCount() { return packTotal() + cart.items.reduce((s, x) => s + (Number(x.quantity) || 0), 0); }
  function grandTotal() {
    let t = cart.packs.reduce((s, p) => s + packUnit(p) * (Number(p.quantity) || 0), 0);
    cart.items.forEach((x) => { const c = catalog.get(Number(x.product_id)); if (c && c.available) t += c.price * x.quantity; });
    return t;
  }
  function problems() {
    const out = [];
    cart.items.forEach((x) => {
      const c = catalog.get(Number(x.product_id));
      if (!c) out.push('購物車裡有已下架的商品，請先移除');
      else if (!c.available) out.push(`${c.name} 目前暫無供應，請先移除`);
    });
    if (packTotal() > PACK_LIMIT) out.push(`萵苣最多 ${PACK_LIMIT} 包，請減少數量`);
    return out;
  }

  // ---------- add controls on cards ----------
  const PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
  function renderControl(el) {
    const key = el.dataset.cartKey;
    const label = el.dataset.label || '';
    const q = qtyOf(key);
    if (el.querySelector('.add-off')) return;
    const had = el.contains(document.activeElement) ? (document.activeElement.dataset.inc != null ? 'inc' : document.activeElement.dataset.dec != null ? 'dec' : 'add') : null;
    if (q > 0) {
      const atMax = key.startsWith('pack:') ? packTotal() >= PACK_LIMIT : q >= ITEM_MAX;
      el.innerHTML = `<div class="stepper" role="group" aria-label="${esc(label)} 數量">
        <button type="button" data-dec aria-label="${esc(label)} 減少一個">−</button>
        <output aria-live="polite">${q}</output>
        <button type="button" data-inc aria-label="${esc(label)} 增加一個"${atMax ? ' disabled' : ''}>＋</button></div>`;
    } else {
      el.innerHTML = `<button type="button" class="add-btn" data-add aria-label="把${esc(label)}加入購物車">${PLUS}<span>加入</span></button>`;
    }
    if (had) {
      const target = q > 0 ? (el.querySelector(had === 'dec' ? '[data-dec]' : '[data-inc]:not([disabled])') || el.querySelector('[data-dec]')) : el.querySelector('[data-add]');
      if (target) target.focus();
    }
  }
  const renderControls = () => $$('[data-cart-key]').forEach(renderControl);

  // ---------- header badge ----------
  function renderBadge(bump) {
    const n = lineCount();
    const c = $('#cart-count');
    if (!c) return;
    c.textContent = n;
    $('#cart-open').classList.toggle('has-items', n > 0);
    $('#cart-open').setAttribute('aria-label', `購物車，${n} 件商品`);
    if (bump && !reduceMotion) { c.classList.remove('bump'); void c.offsetWidth; c.classList.add('bump'); }
  }

  // ---------- drawer ----------
  const drawer = $('#drawer'), scrim = $('#scrim');
  let lastFocus = null;
  function openDrawer() {
    renderDrawer();
    if (toastEl) { toastEl.classList.remove('show'); toastEl.hidden = true; }
    lastFocus = document.activeElement;
    drawer.hidden = false; scrim.hidden = false;
    requestAnimationFrame(() => { drawer.classList.add('open'); scrim.classList.add('show'); });
    document.body.style.overflow = 'hidden';
    setTimeout(() => $('#cart-close').focus(), 60);
  }
  function closeDrawer() {
    drawer.classList.remove('open'); scrim.classList.remove('show');
    document.body.style.overflow = '';
    setTimeout(() => { drawer.hidden = true; scrim.hidden = true; }, reduceMotion ? 0 : 300);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  const thumb = (src, alt) => src ? `<img src="${esc(src)}" alt="" loading="lazy">` : `<span class="ph" aria-hidden="true"><span>${esc((alt || '菜').slice(0, 1))}</span></span>`;
  function packView(p) {
    if (isComboPack(p)) return { name: '農場搭配萵苣包', sub: '品種由農場搭配', photo: '/img/menu/frilly-2.jpg' };
    const first = lettuceByName.get(p.preferences[0]);
    const name = p.preferences.length === 1 ? p.preferences[0] : '自選萵苣包';
    const sub = p.preferences.length === 1 ? '指定品種包' : p.preferences.join('、');
    return { name, sub, photo: first ? first.photo : '' };
  }

  function lineHtml(kind, idx, name, sub, photo, qty, unitPrice, maxed, broken, unit = '包') {
    const priceTxt = unitPrice != null ? `${money(unitPrice)} / ${unit}` : '';
    const subTxt = [sub, priceTxt].filter(Boolean).join(' · ');
    return `<div class="cart-line" data-kind="${kind}" data-idx="${idx}">
      ${thumb(photo, name)}
      <div>
        <div class="cl-name">${esc(name)}</div>
        <div class="cl-sub">${esc(subTxt)}</div>
        <div class="cl-ctrl">
          ${broken ? '' : `<div class="qty" role="group" aria-label="${esc(name)} 數量">
            <button type="button" data-act="dec" aria-label="${esc(name)} 減少一個">−</button>
            <output aria-live="polite">${qty}</output>
            <button type="button" data-act="inc" aria-label="${esc(name)} 增加一個"${maxed ? ' disabled' : ''}>＋</button>
          </div>`}
          <button type="button" class="cl-remove" data-act="del">移除</button>
        </div>
      </div>
      <div class="cl-sum">${broken ? '—' : money(unitPrice * qty)}</div>
    </div>`;
  }

  function renderDrawer() {
    const body = $('#drawer-body'), foot = $('#drawer-foot');
    if (!body) return;
    if (!cart.packs.length && !cart.items.length) {
      body.innerHTML = `<div class="cart-empty">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h2l2.2 10.2a2 2 0 0 0 2 1.6h7.6a2 2 0 0 0 2-1.5L21 8H6.2"/><circle cx="10" cy="20" r="1.4"/><circle cx="17" cy="20" r="1.4"/></svg>
        <p>購物車還是空的。</p><a class="btn" href="/shop">去逛這週的菜</a></div>`;
      foot.hidden = true;
      return;
    }
    foot.hidden = false;
    const groups = new Map(); // source -> [html]
    const push = (src, html) => { if (!groups.has(src)) groups.set(src, []); groups.get(src).push(html); };
    const packsFull = packTotal() >= PACK_LIMIT;
    cart.packs.forEach((p, i) => {
      const v = packView(p);
      const sp = (p.special_items || []).map((x) => x.item_name);
      push('食在好蔬・北投', lineHtml('pack', i, v.name, sp.length ? v.sub + '＋' + sp.join('、') : v.sub, v.photo, p.quantity, packUnit(p), packsFull, false));
    });
    cart.items.forEach((x, i) => {
      const c = catalog.get(Number(x.product_id));
      if (!c) { push('已下架', lineHtml('item', i, '已下架的商品', '請移除', '', x.quantity, null, true, true)); return; }
      const src = c.kind === 'farm' ? c.source : '食在好蔬・北投';
      push(src, lineHtml('item', i, c.name, c.available ? '' : '暫無供應，請移除', c.photo, x.quantity, c.available ? c.price : null, x.quantity >= ITEM_MAX, !c.available, c.unit));
    });
    let html = '';
    groups.forEach((lines, src) => { html += `<section class="cart-group"><h3>${esc(src)}</h3>${lines.join('')}</section>`; });
    problems().forEach((m) => { html += `<p class="cart-warn">${esc(m)}</p>`; });
    if (packsFull && !problems().length) html += `<p class="cart-warn">萵苣已達每日上限 ${PACK_LIMIT} 包。</p>`;
    // 加購建議：買了萵苣但還沒有其他商品 → 推一樣
    const inCart = new Set(cart.items.map((x) => Number(x.product_id)));
    const sug = (S.catalog || []).find((c) => c.kind !== 'standard' && c.available && !inCart.has(c.id));
    if (sug && cart.packs.length && !cart.items.length) {
      html += `<div class="cart-suggest"><p>常一起買</p><div class="suggest-row">
        ${thumb(sug.photo, sug.name)}<div><b>${esc(sug.name)}</b><br><small>${esc(sug.source)} · ${money(sug.price)} / ${esc(sug.unit)}</small></div>
        <div class="add" data-cart-key="item:${sug.id}" data-label="${esc(sug.name)}"></div></div></div>`;
    }
    body.innerHTML = html;
    $$('[data-cart-key]', body).forEach(renderControl);
    $('#cart-total').textContent = money(grandTotal());
    const bad = problems().length > 0;
    $('#checkout-btn').disabled = bad;
  }

  // ---------- toast ----------
  const toastEl = $('#toast');
  let toastTimer = null;
  function toast(msg, opts = {}) {
    if (!toastEl) return;
    toastEl.innerHTML = `<span>${esc(msg)}</span>${opts.view ? '<button type="button" data-view-cart>查看購物車</button>' : ''}`;
    toastEl.classList.toggle('warn', !!opts.warn);
    toastEl.hidden = false;
    requestAnimationFrame(() => toastEl.classList.add('show'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.classList.remove('show'); setTimeout(() => { toastEl.hidden = true; }, 250); }, opts.warn ? 5000 : 3200);
  }

  function refresh(bump) { save(); renderControls(); renderBadge(bump); if (drawer && !drawer.hidden) renderDrawer(); }

  function add(key, label, n = 1) {
    const before = qtyOf(key);
    const r = setQty(key, before + n);
    const added = r.qty - before;
    refresh(added > 0);
    if (added > 0) toast(`已加入 ${label}${added > 1 ? ' ×' + added : ''}`, { view: true });
    if (r.msg) toast(r.msg, { warn: true });
    return added;
  }

  // ---------- events ----------
  document.addEventListener('click', (e) => {
    const t = e.target;
    const ctl = t.closest('[data-cart-key]');
    if (ctl) {
      const key = ctl.dataset.cartKey, label = ctl.dataset.label || '';
      if (t.closest('[data-add]')) { e.preventDefault(); add(key, label, 1); return; }
      if (t.closest('[data-inc]')) { e.preventDefault(); add(key, label, 1); return; }
      if (t.closest('[data-dec]')) { e.preventDefault(); setQty(key, qtyOf(key) - 1); refresh(false); return; }
    }
    if (t.closest('[data-view-cart]')) { toastEl.classList.remove('show'); openDrawer(); return; }

    const buy = t.closest('[data-buy]');
    if (buy) {
      const inp = $('[data-qty-input]');
      const n = Math.max(1, parseInt(inp && inp.value, 10) || 1);
      if (add(buy.dataset.key, buy.dataset.label, n) > 0 && inp) { inp.value = 1; syncBuySum(); }
      return;
    }
    if (t.closest('[data-sticky-buy]')) { const b = $('[data-buy]'); if (b) add(b.dataset.key, b.dataset.label, 1); return; }

    const step = t.closest('[data-qty] [data-step]');
    if (step) {
      const inp = step.parentElement.querySelector('[data-qty-input]');
      inp.value = Math.min(ITEM_MAX, Math.max(1, (parseInt(inp.value, 10) || 1) + Number(step.dataset.step)));
      syncBuySum();
      return;
    }

    const act = t.closest('.cart-line [data-act]');
    if (act) {
      const line = act.closest('.cart-line');
      const list = line.dataset.kind === 'pack' ? cart.packs : cart.items;
      const i = Number(line.dataset.idx);
      const x = list[i];
      if (!x) return;
      const what = act.dataset.act;
      if (what === 'inc') {
        if (line.dataset.kind === 'pack') { if (packTotal() >= PACK_LIMIT) return; x.quantity += 1; x.subtotal = packUnit(x); x.line_total = x.subtotal * x.quantity; }
        else x.quantity = Math.min(ITEM_MAX, x.quantity + 1);
      } else if (what === 'dec') {
        x.quantity -= 1;
        if (line.dataset.kind === 'pack' && x.quantity > 0) { x.subtotal = packUnit(x); x.line_total = x.subtotal * x.quantity; }
      }
      if (what === 'del' || x.quantity <= 0) {
        line.classList.add('leaving');
        setTimeout(() => { list.splice(i, 1); refresh(false); $('#cart-close').focus(); }, reduceMotion ? 0 : 200);
        return;
      }
      refresh(false);
      const again = $(`.cart-line[data-kind="${line.dataset.kind}"][data-idx="${i}"] [data-act="${what}"]`);
      if (again && !again.disabled) again.focus();
    }
  });

  function syncBuySum() {
    const inp = $('[data-qty-input]'), b = $('[data-buy]');
    if (!inp || !b) return;
    const n = Math.min(ITEM_MAX, Math.max(1, parseInt(inp.value, 10) || 1));
    const sum = b.querySelector('.buy-sum');
    if (sum) sum.textContent = money(n * Number(b.dataset.price || 0));
  }
  const qtyInput = $('[data-qty-input]');
  if (qtyInput) qtyInput.addEventListener('input', () => { qtyInput.value = qtyInput.value.replace(/\D/g, '').slice(0, 2); syncBuySum(); });

  if (drawer) {
    $('#cart-open').addEventListener('click', openDrawer);
    $('#cart-close').addEventListener('click', closeDrawer);
    $('#keep-shopping').addEventListener('click', closeDrawer);
    scrim.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (e) => {
      if (drawer.hidden) return;
      if (e.key === 'Escape') { closeDrawer(); return; }
      if (e.key === 'Tab') { // 焦點留在抽屜裡
        const f = $$('button:not([disabled]), a[href], input, [tabindex]:not([tabindex="-1"])', drawer).filter((x) => x.offsetParent !== null);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    });
    $('#checkout-btn').addEventListener('click', () => {
      if (problems().length) { renderDrawer(); return; }
      save();
      try { localStorage.setItem('veg_reopen_cart', '1'); } catch (e) { /* 略 */ }
      $('#checkout-btn').textContent = '前往結帳…';
      location.href = '/pos';
    });
  }

  // 其他分頁（或 /pos）改了購物車 → 同步
  window.addEventListener('storage', (e) => {
    if (e.key !== PACKS_KEY && e.key !== ITEMS_KEY) return;
    cart.packs = read(PACKS_KEY); cart.items = read(ITEMS_KEY);
    refresh(false);
  });

  // ---------- nav ----------
  const burger = $('#burger'), nav = $('#nav');
  if (burger) {
    burger.addEventListener('click', () => {
      const o = nav.classList.toggle('open');
      burger.setAttribute('aria-expanded', o);
      burger.setAttribute('aria-label', o ? '關閉選單' : '開啟選單');
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && nav.classList.contains('open')) { burger.click(); burger.focus(); } });
  }

  // ---------- shop filters ----------
  const grid = $('#grid');
  if (grid && $('#shop')) {
    const cards = $$('.pcard', grid);
    const params = new URLSearchParams(location.search);
    const f = { src: params.get('src') || '', type: params.get('type') || '', q: params.get('q') || '', sort: params.get('sort') || 'rec' };
    const qEl = $('#q'), sortEl = $('#sort');
    qEl.value = f.q; sortEl.value = f.sort;

    const apply = () => {
      const q = f.q.trim().toLowerCase();
      let shown = 0;
      cards.forEach((c) => {
        const srcOk = !f.src || c.dataset.src === f.src || (f.src === 'farm' && c.dataset.src.startsWith('farm-'));
        const typeOk = !f.type || c.dataset.type === f.type;
        const qOk = !q || q.split(/\s+/).every((w) => c.dataset.search.includes(w));
        c.hidden = !(srcOk && typeOk && qOk);
        if (!c.hidden) shown++;
      });
      const sorted = [...cards].sort((a, b) => {
        if (f.sort === 'price-asc') return a.dataset.price - b.dataset.price || a.dataset.order - b.dataset.order;
        if (f.sort === 'price-desc') return b.dataset.price - a.dataset.price || a.dataset.order - b.dataset.order;
        return a.dataset.order - b.dataset.order;
      });
      sorted.forEach((c) => grid.appendChild(c));
      $('#result-count').textContent = shown ? `共 ${shown} 件商品` : '沒有符合的商品';
      $('#no-result').hidden = shown > 0;
      $$('.fchip').forEach((b) => b.setAttribute('aria-pressed', String(f[b.dataset.f] === b.dataset.v)));
      const p = new URLSearchParams();
      if (f.src) p.set('src', f.src); if (f.type) p.set('type', f.type); if (f.q) p.set('q', f.q); if (f.sort !== 'rec') p.set('sort', f.sort);
      history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
    };
    $$('.fchip').forEach((b) => b.addEventListener('click', () => { f[b.dataset.f] = b.dataset.v; apply(); }));
    let deb = null;
    qEl.addEventListener('input', () => { clearTimeout(deb); deb = setTimeout(() => { f.q = qEl.value; apply(); }, 150); });
    sortEl.addEventListener('change', () => { f.sort = sortEl.value; apply(); });
    $('#clear-filters').addEventListener('click', () => { f.src = f.type = f.q = ''; qEl.value = ''; apply(); qEl.focus(); });
    apply();
  }

  // ---------- product gallery ----------
  $$('[data-gallery]').forEach((g) => {
    const main = g.querySelector('.main-img');
    g.querySelectorAll('.thumbs button').forEach((b) => b.addEventListener('click', () => {
      if (main && main.tagName === 'IMG') main.src = b.dataset.img;
      g.querySelectorAll('.thumbs button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    }));
  });

  // ---------- sticky buy bar (手機：購買列捲出畫面才出現) ----------
  const sticky = $('#sticky-buy'), buyRow = $('#buy-row');
  if (sticky && buyRow && 'IntersectionObserver' in window) {
    new IntersectionObserver(([en]) => {
      // 手機：頁面裡的購買列不在畫面上時（還沒捲到、或已捲過）就顯示底部購買列
      const show = !en.isIntersecting && matchMedia('(max-width: 760px)').matches;
      sticky.hidden = !show;
      document.body.classList.toggle('has-sticky', show);
    }).observe(buyRow);
  }

  refresh(false);
})();
