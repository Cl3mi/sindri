// Review-campaign rules for the review screen (campaign design §3). Pure, so
// node can test them without a DOM; main.js does the wiring.

// The server sends `campaign: null` for an ordinary drawing and the campaign's
// name otherwise, so a missing doc (not yet in the work-list cache) reads as
// ordinary -- the safe side, since it only changes button copy.
export const isCampaignDoc = (doc) => !!(doc && doc.campaign);

// "12 / 50 approved": a drawing counts once it has ANY sealed revision --
// re-opening an approved drawing to fix something does not un-approve it.
// Purged tombstones are not drawings the reviewer can still act on, so they
// leave the denominator too.
export function approvedCount(docs, batchId) {
  const inBatch = docs.filter((x) => x.batch_id === batchId && x.state !== 'purged');
  return { approved: inBatch.filter((x) => x.final_revision != null).length,
           total: inBatch.length };
}

// journal.py's NOTE_MAX: the server refuses a longer set_note, and one
// refused event fails its whole flush batch and stalls the journal.
export const NOTE_MAX = 2000;

// The text to journal as set_note, or null for nothing. The note box is one
// textarea shared by every drawing, so the text in it belongs to the drawing
// that was open when it took focus (sidAtFocus) -- after Back/Forward with
// the box still focused, committing it to the NEW session would put A's note
// on B and lose A's. Unchanged text is not re-journaled.
export function noteToCommit(sidAtFocus, sid, text, current) {
  if (!sid || sidAtFocus !== sid) return null;
  const cut = String(text ?? '').slice(0, NOTE_MAX);
  return cut === (current || '') ? null : cut;
}

// Approve is the same seal as Finish; only the promise differs. The campaign
// copy says out loud that untouched rows count as confirmed, because that is
// what the grading (plan C2) will read an untouched row as.
export function finishCopy(campaign) {
  return campaign
    ? { label: 'Approve drawing',
        title: 'Everything you did not change is confirmed as correct. Seals your review — no export.' }
    : { label: 'Finish & Export',
        title: 'Seal the review and export Excel + ballooned PDF' };
}
