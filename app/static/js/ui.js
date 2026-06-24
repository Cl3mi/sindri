// Toasts, modal, splitter, theme/density, drag-drop overlay.

const STORAGE = {
  theme:    'sindri.theme',
  density:  'sindri.density',
  split:    'sindri.split',
};

// ===== Toasts =========================================================
let toastsEl;
const TOAST_TIMEOUT = 4200;

export function toast({ kind = 'info', title = '', msg = '', timeout = TOAST_TIMEOUT } = {}) {
  if (!toastsEl) toastsEl = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  const iconId = kind === 'ok' ? 'i-check' :
                 kind === 'warn' ? 'i-warn' :
                 kind === 'error' ? 'i-warn' : 'i-info';
  t.innerHTML = `
    <svg class="icon"><use href="#${iconId}"/></svg>
    <div class="body">
      <div class="title"></div>
      <div class="msg"></div>
    </div>
    <button class="close" aria-label="Dismiss"><svg width="11" height="11"><use href="#i-x"/></svg></button>
  `;
  t.querySelector('.title').textContent = title;
  const m = t.querySelector('.msg');
  if (msg) m.textContent = msg; else m.remove();
  toastsEl.appendChild(t);

  const close = () => {
    if (t.classList.contains('exiting')) return;
    t.classList.add('exiting');
    setTimeout(() => t.remove(), 220);
  };
  t.querySelector('.close').addEventListener('click', close);
  if (timeout) setTimeout(close, timeout);
  return close;
}

// ===== Modal ==========================================================
export function initModal() {
  const backdrop = document.getElementById('modal-backdrop');
  const close = () => { backdrop.dataset.open = 'false'; };
  document.getElementById('modal-close').addEventListener('click', close);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
}
export function openHelp()  { document.getElementById('modal-backdrop').dataset.open = 'true'; }
export function isHelpOpen() { return document.getElementById('modal-backdrop').dataset.open === 'true'; }

// ===== Splitter =======================================================
export function initSplitter() {
  const handle = document.getElementById('split-handle');
  const body   = document.getElementById('shell-body');

  const saved = parseFloat(localStorage.getItem(STORAGE.split) || '');
  if (Number.isFinite(saved) && saved >= 20 && saved <= 80) {
    body.style.setProperty('--split-l', saved + '%');
  } else {
    body.style.setProperty('--split-l', '58%');
  }

  let dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true;
    handle.classList.add('active');
    document.body.classList.add('col-resizing');
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const rect = body.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    const min = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pane-min')) / rect.width) * 100;
    const clamped = Math.max(min, Math.min(100 - min, pct));
    body.style.setProperty('--split-l', clamped + '%');
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('active');
    document.body.classList.remove('col-resizing');
    const val = body.style.getPropertyValue('--split-l').replace('%','').trim();
    if (val) localStorage.setItem(STORAGE.split, val);
  });
}

// ===== Theme + Density ===============================================
export function initThemeAndDensity() {
  const html = document.documentElement;

  const theme = localStorage.getItem(STORAGE.theme) || 'dark';
  html.dataset.theme = theme;
  updateThemeIcon(theme);

  const dens = localStorage.getItem(STORAGE.density) || 'comfortable';
  html.dataset.density = dens;

  document.getElementById('theme-toggle').addEventListener('click', () => {
    const next = html.dataset.theme === 'dark' ? 'light' : 'dark';
    html.dataset.theme = next;
    localStorage.setItem(STORAGE.theme, next);
    updateThemeIcon(next);
  });
  document.getElementById('density-toggle').addEventListener('click', () => {
    const next = html.dataset.density === 'compact' ? 'comfortable' : 'compact';
    html.dataset.density = next;
    localStorage.setItem(STORAGE.density, next);
  });
}
function updateThemeIcon(theme) {
  const use = document.querySelector('#theme-toggle svg use');
  if (use) use.setAttribute('href', theme === 'dark' ? '#i-moon' : '#i-sun');
}

// ===== Drag-drop overlay =============================================
export function initDragDrop(onFile) {
  let depth = 0;
  window.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    depth++;
    document.body.classList.add('drag-over');
  });
  window.addEventListener('dragover', (e) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) document.body.classList.remove('drag-over');
  });
  window.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    document.body.classList.remove('drag-over');
    const file = [...(e.dataTransfer?.files || [])].find((f) => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'));
    if (file) onFile(file);
    else toast({ kind: 'warn', title: 'Drop a PDF file', msg: 'Only .pdf files are supported.' });
  });
}
