// One state machine for both banners B3 adds (batch design §3, §5): the
// losing side of a two-tab takeover (`stale` -- unrecoverable, reload is the
// only way out) and a journal that keeps failing to flush (`lagging` --
// recoverable, shown with a retry countdown). Stale wins when both are true:
// once another tab holds the writer, no retry of this tab's flush can ever
// succeed, so there is nothing a countdown would be counting down to.
export function bannerFor({ stale, lagging, unsavedCount = 0, retryAt = 0, now = Date.now() }) {
  if (stale) return { kind: 'stale', unsavedCount };
  if (lagging) return { kind: 'lagging', secondsLeft: Math.max(0, Math.ceil((retryAt - now) / 1000)) };
  return null;
}
