// A textarea that grows with its text, up to a cap. The note box (#doc-note)
// was one fixed line, so anything longer than ~40 characters was hidden.

// Pure rule: the content height clamped to [minPx, maxPx]; beyond the cap the
// box scrolls rather than pushing the toolbar around.
export function fitHeight(contentPx, maxPx, minPx) {
  const height = Math.max(minPx, Math.min(contentPx, maxPx));
  return { height, scroll: contentPx > maxPx };
}

// Binds the rule to a textarea and returns `refit`, which the caller must
// invoke after setting `.value` from code (no input event fires then) and
// when the box becomes visible (a hidden box measures 0).
export function autosize(el, { maxPx = 160, minPx = 28 } = {}) {
  const refit = () => {
    el.style.height = 'auto';             // shrink first, or scrollHeight never falls
    // scrollHeight leaves out the border; a border-box height must include it
    // or the last line is clipped and a scrollbar appears for two pixels.
    const border = el.offsetHeight - el.clientHeight;
    const { height, scroll } = fitHeight(el.scrollHeight + border, maxPx, minPx);
    el.style.height = `${height}px`;
    el.style.overflowY = scroll ? 'auto' : 'hidden';
  };
  el.addEventListener('input', refit);
  return refit;
}
