// ============= 食在好蔬 landing page =============

// ---- 商品資料：從後台 CMS API 載入（後台改了這裡就同步）----
let PRODUCTS = [];
let PROD_FILTER = 'all';

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function normalizeProduct(r) {
  return {
    name: r.name || '',
    en: r.name_en || '',
    cat: r.category || '',
    kind: r.kind || 'standard',
    price: Number(r.price) || 0,
    photos: Array.isArray(r.photos) ? r.photos.filter(Boolean) : [],
    desc: r.description || '',
    features: r.features || '',
    nutrition: r.nutrition || '',
    eating: r.eating || '',
    pairing: r.pairing || '',
    available: r.is_available !== false,
  };
}

async function loadProducts() {
  try {
    const res = await fetch('/api/products');
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    PRODUCTS = (Array.isArray(data) ? data : []).map(normalizeProduct);
  } catch (e) {
    PRODUCTS = [];
  }
  renderProducts();
}

// ---- 渲染商品卡 ----
function renderProducts() {
  const grid = document.getElementById('products-grid');
  if (!grid) return;
  grid.innerHTML = '';
  if (!PRODUCTS.length) {
    grid.innerHTML = '<p class="products-empty">商品載入中，請稍候…</p>';
    return;
  }
  const list = PRODUCTS.filter(p => PROD_FILTER === 'all' || p.kind === PROD_FILTER);
  if (!list.length) {
    grid.innerHTML = '<p class="products-empty">此分類暫無商品</p>';
    return;
  }
  list.forEach((p) => {
    const card = document.createElement('div');
    card.className = 'product-card fade-up' + (p.available ? '' : ' is-unavailable');
    const firstPhoto = p.photos[0] || '';
    const imgHtml = firstPhoto
      ? `<img src="${esc(firstPhoto)}" alt="${esc(p.name)}" loading="lazy"
             onerror="this.parentElement.classList.add('no-img'); this.remove();">`
      : '';
    card.innerHTML = `
      <div class="product-img">${imgHtml}
        ${p.available ? '' : '<span class="product-unavail-tag">暫無供應</span>'}
      </div>
      <div class="product-body">
        <div class="product-name">${esc(p.name)}</div>
        <span class="product-name-en">${esc(p.en)}</span>
        <p class="product-desc">${esc(p.desc)}</p>
        <div class="product-more">查看詳細 →</div>
      </div>
    `;
    card.addEventListener('click', () => openProductModal(p));
    grid.appendChild(card);
  });

  // Re-observe for scroll fade-in
  document.querySelectorAll('#products-grid .fade-up').forEach(el => fadeObserver.observe(el));
}

// ---- 商品分類切換 ----
function setupProdFilter() {
  const bar = document.getElementById('prod-filter');
  if (!bar) return;
  bar.querySelectorAll('.prod-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      PROD_FILTER = btn.dataset.kind;
      bar.querySelectorAll('.prod-filter-btn').forEach(b => b.classList.toggle('is-active', b === btn));
      renderProducts();
    });
  });
}

// ---- 商品 Modal ----
const modalState = { product: null, idx: 0 };

function setModalPhoto(idx) {
  if (!modalState.product) return;
  const p = modalState.product;
  const n = p.photos.length;
  if (!n) return;
  modalState.idx = (idx + n) % n;
  document.getElementById('pm-main-img').src = p.photos[modalState.idx];
  document.getElementById('pm-counter').textContent = `${modalState.idx + 1} / ${n}`;
  document.querySelectorAll('#pm-thumbs .pm-thumb').forEach((t, i) => {
    t.classList.toggle('active', i === modalState.idx);
  });
}

function openProductModal(p) {
  const modal = document.getElementById('product-modal');
  modalState.product = p;
  document.getElementById('pm-category').textContent = p.cat + (p.available ? '' : ' · 暫無供應');
  document.getElementById('pm-name').textContent = p.name;
  document.getElementById('pm-en').textContent = p.en;
  document.getElementById('pm-desc').textContent = p.desc;
  document.getElementById('pm-features').textContent = p.features;
  document.getElementById('pm-nutrition').textContent = p.nutrition;
  document.getElementById('pm-nutrition-row').style.display = p.nutrition ? '' : 'none';
  document.getElementById('pm-eating').textContent = p.eating;
  document.getElementById('pm-pairing').textContent = p.pairing;

  document.getElementById('pm-main-img').alt = p.name;

  const thumbsBox = document.getElementById('pm-thumbs');
  thumbsBox.innerHTML = '';
  const n = p.photos.length;
  p.photos.forEach((url, i) => {
    const t = document.createElement('button');
    t.className = 'pm-thumb';
    t.type = 'button';
    t.innerHTML = `<img src="${esc(url)}" alt="" loading="lazy">`;
    t.addEventListener('click', () => setModalPhoto(i));
    thumbsBox.appendChild(t);
  });

  const mainImg = document.getElementById('pm-main-img');
  if (n) { setModalPhoto(0); mainImg.style.display = ''; }
  else { mainImg.removeAttribute('src'); mainImg.style.display = 'none'; document.getElementById('pm-counter').textContent = ''; }
  // 只有一張就隱藏左右箭頭與計數
  document.querySelectorAll('.pm-nav').forEach(b => b.style.display = n > 1 ? '' : 'none');
  document.getElementById('pm-counter').style.display = n > 1 ? '' : 'none';

  modal.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeProductModal() {
  document.getElementById('product-modal').classList.remove('open');
  document.body.style.overflow = '';
  modalState.product = null;
}

// 左右滑動切換主圖
function setupModalSwipe() {
  const main = document.getElementById('pm-main');
  if (!main) return;
  let startX = 0, startY = 0, isTouching = false;

  main.addEventListener('touchstart', (e) => {
    isTouching = true;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }, { passive: true });

  main.addEventListener('touchend', (e) => {
    if (!isTouching) return;
    isTouching = false;
    const dx = e.changedTouches[0].clientX - startX;
    const dy = e.changedTouches[0].clientY - startY;
    // 必須水平位移為主 + 距離超過 40px
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      if (dx < 0) setModalPhoto(modalState.idx + 1); // 左滑 → 下一張
      else setModalPhoto(modalState.idx - 1);
    }
  }, { passive: true });

  document.getElementById('pm-prev').addEventListener('click', () => setModalPhoto(modalState.idx - 1));
  document.getElementById('pm-next').addEventListener('click', () => setModalPhoto(modalState.idx + 1));

  // 鍵盤左右切換
  document.addEventListener('keydown', (e) => {
    if (!document.getElementById('product-modal').classList.contains('open')) return;
    if (e.key === 'ArrowLeft')  setModalPhoto(modalState.idx - 1);
    if (e.key === 'ArrowRight') setModalPhoto(modalState.idx + 1);
  });
}

// ---- Hero slideshow ----
function setupHeroSlider() {
  const slider = document.getElementById('hero-slider');
  if (!slider) return;
  const slides = slider.querySelectorAll('.slide');
  if (slides.length === 0) return;
  const dotsContainer = document.getElementById('hero-dots');
  let idx = 0;
  let timer;

  slides.forEach((_, i) => {
    const dot = document.createElement('button');
    dot.setAttribute('aria-label', `第 ${i+1} 張`);
    if (i === 0) dot.classList.add('active');
    dot.addEventListener('click', () => { goTo(i); reset(); });
    dotsContainer.appendChild(dot);
  });

  function goTo(i) {
    slides[idx].classList.remove('active');
    dotsContainer.children[idx].classList.remove('active');
    idx = (i + slides.length) % slides.length;
    slides[idx].classList.add('active');
    dotsContainer.children[idx].classList.add('active');
  }
  function next() { goTo(idx + 1); }
  function reset() { clearInterval(timer); timer = setInterval(next, 5000); }
  reset();

  // 行動裝置左右滑動切換
  let startX = 0, startY = 0, touching = false;
  slider.addEventListener('touchstart', (e) => {
    touching = true;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }, { passive: true });
  slider.addEventListener('touchend', (e) => {
    if (!touching) return;
    touching = false;
    const dx = e.changedTouches[0].clientX - startX;
    const dy = e.changedTouches[0].clientY - startY;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      goTo(idx + (dx < 0 ? 1 : -1));
      reset();
    }
  }, { passive: true });
}

// ---- 訪問人數 ----
function setupVisitorCount() {
  const el = document.getElementById('visitor-count');
  if (!el) return;
  fetch('/api/visit', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d && typeof d.count === 'number') {
        // 千分位顯示
        el.textContent = d.count.toLocaleString('zh-Hant');
      }
    })
    .catch(() => { /* 靜默失敗 */ });
}

// ---- 回到頂部 FAB ----
function setupBackToTop() {
  const fab = document.getElementById('back-to-top');
  if (!fab) return;
  const onScroll = () => {
    fab.classList.toggle('visible', window.scrollY > 320);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  fab.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  onScroll();
}

// ---- Sticky nav shadow + 漢堡選單 ----
function setupNav() {
  const nav = document.getElementById('main-nav');
  if (!nav) return;
  window.addEventListener('scroll', () => {
    nav.classList.toggle('scrolled', window.scrollY > 20);
  });

  // 漢堡選單
  const burger = document.getElementById('nav-burger');
  const links = document.getElementById('nav-links');
  if (burger && links) {
    const close = () => {
      links.classList.remove('open');
      burger.classList.remove('active');
      burger.setAttribute('aria-expanded', 'false');
    };
    burger.addEventListener('click', () => {
      const willOpen = !links.classList.contains('open');
      links.classList.toggle('open', willOpen);
      burger.classList.toggle('active', willOpen);
      burger.setAttribute('aria-expanded', String(willOpen));
    });
    // 點選任一連結就收起
    links.querySelectorAll('a').forEach(a => a.addEventListener('click', close));
    // 滾動視窗也收起
    window.addEventListener('scroll', () => {
      if (links.classList.contains('open')) close();
    }, { passive: true });
  }
}

// ---- 多視圖 hash 路由 ----
const VIEWS = ['home', 'aquaponics', 'about', 'contact'];
// 特殊 hash：顯示某視圖後再捲到區塊（當季商品已併回首頁）
const SCROLL_HASH = { products: { view: 'home', sel: '#products' } };

function applyHash() {
  const navKey = (location.hash || '').replace('#', '') || 'home';
  const special = SCROLL_HASH[navKey];
  let view = special ? special.view : navKey;
  if (!VIEWS.includes(view)) view = 'home';
  document.querySelectorAll('.view').forEach(v => {
    v.classList.toggle('is-active', v.dataset.view === view);
  });
  document.querySelectorAll('#nav-links a[data-nav]').forEach(a => {
    a.classList.toggle('is-current', a.dataset.nav === navKey);
  });
  document.querySelectorAll('.view.is-active .fade-up').forEach(el => fadeObserver.observe(el));
  if (special) {
    const el = document.querySelector(special.sel);
    if (el) { el.scrollIntoView({ behavior: 'smooth' }); return; }
  }
  window.scrollTo({ top: 0 });
}

function setupViews() {
  if (!document.querySelector('.view')) return;
  applyHash();
  window.addEventListener('hashchange', applyHash);
}

// ---- QR 點擊全螢幕放大 + 直接前往 LINE ----
function setupQR() {
  const modal = document.getElementById('qr-modal');
  const img = document.getElementById('qr-modal-img');
  const label = document.getElementById('qr-modal-label');
  const linkBtn = document.getElementById('qr-modal-link');
  if (!modal) return;

  function open(src, name, url) {
    img.src = src;
    label.textContent = name;
    if (url) {
      linkBtn.href = url;
      linkBtn.style.display = '';
    } else {
      linkBtn.style.display = 'none';
    }
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
  }
  function close() {
    modal.classList.remove('open');
    document.body.style.overflow = '';
  }
  document.querySelectorAll('.qr-card').forEach(card => {
    card.addEventListener('click', () => {
      open(card.dataset.qr, card.dataset.qrLabel || '', card.dataset.qrUrl || '');
    });
  });
  document.getElementById('qr-modal-close').addEventListener('click', close);
  document.getElementById('qr-modal-close-btn').addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('open')) close();
  });
}

// ---- 通用分頁：tabSel 切換 panelSel 的顯示（用 data 屬性對應）----
function setupTabs(tabSel, panelSel, key) {
  const tabs = document.querySelectorAll(tabSel);
  const panels = document.querySelectorAll(panelSel);
  if (!tabs.length || !panels.length) return;
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const v = tab.dataset[key];
      tabs.forEach(t => t.classList.toggle('is-active', t === tab));
      panels.forEach(p => {
        p.hidden = (p.dataset[key] !== v);
        if (!p.hidden) p.querySelectorAll('.fade-up').forEach(el => el.classList.add('visible'));
      });
    });
  });
}

// ---- Fade-up on scroll ----
const fadeObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('visible');
      fadeObserver.unobserve(e.target);
    }
  });
}, { threshold: 0.12 });

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  setupNav();
  setupViews();
  setupProdFilter();
  setupTabs('#about-tabs .about-tab', '#view-about .about-panel', 'panel');
  setupTabs('.esg-subtabs .esg-subtab', '#view-about .esg-pane', 'esg');
  setupTabs('#policy-tabs .policy-tab', '#view-contact .policy-panel', 'policy');
  setupHeroSlider();
  setupQR();
  loadProducts();
  setupModalSwipe();
  setupBackToTop();
  setupVisitorCount();
  document.querySelectorAll('.fade-up').forEach(el => fadeObserver.observe(el));

  // Product modal close handlers
  document.getElementById('pm-close').addEventListener('click', closeProductModal);
  document.querySelector('#product-modal .pm-backdrop').addEventListener('click', closeProductModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeProductModal();
  });
});
