// Global keyboard shortcuts that aren't already handled in viewer.js.

import { undo, redo } from './state.js';
import { acceptRows } from './table.js';
import { openHelp } from './ui.js';

export function initShortcuts() {
  window.addEventListener('keydown', (e) => {
    if (isEditing(e.target)) return;

    // Undo / Redo
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
      e.preventDefault();
      redo();
      return;
    }

    // Focus search
    if (e.key === '/') {
      e.preventDefault();
      const s = document.getElementById('search-input');
      if (s) s.focus();
      return;
    }

    // Help
    if (e.key === '?') {
      e.preventDefault();
      openHelp();
      return;
    }

    // Filter pills
    if (e.key === '1') clickFilter('all');
    if (e.key === '2') clickFilter('review');
    if (e.key === '3') clickFilter('ok');
    if (e.key === '4') clickFilter('suggested');

    // Accept visible -- the same rule as the Accept button: suggestions are
    // confirmed only inside the Low-confidence filter (key 4).
    if (e.key.toLowerCase() === 'y' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      acceptRows([]);
    }
  });
}

function clickFilter(name) {
  const btn = document.querySelector(`#filter-pills button[data-filter="${name}"]`);
  if (btn) btn.click();
}

function isEditing(el) {
  if (!el) return false;
  if (el.isContentEditable) return true;
  const t = el.tagName;
  return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT';
}
