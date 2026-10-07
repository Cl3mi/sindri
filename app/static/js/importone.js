// One drawing imported as a draft batch of one (decision 17), through the
// same chunked, resumable protocol a folder or a ZIP uses (batch design §2).
// The interim screen's import; B3's staging view replaces it.
//
// What this decides is when a draft is KEPT for resuming and when it is
// thrown away, so it is kept free of the DOM and every server call is
// injected (tests/js/importone.test.mjs):
//  * The draft is remembered for this file BEFORE the first byte, in
//    localStorage (a closed tab is one of the cases decision 21 names), so
//    selecting the same file again -- after a failure, a reload or a closed
//    tab -- resumes into the same draft at the last chunk. It stays
//    remembered after the file arrives, until the caller starts or cancels
//    it: a tab closed on the confirm screen then finds the staged drawing
//    again instead of importing it a second time.
//  * The server resumes only UNFINISHED uploads; one already complete would
//    be uploaded again as a new upload and stage the same drawing twice in
//    one draft. So a remembered draft is asked first (its staging view) and
//    a completed upload of this file is answered from there -- the stored
//    result a retried /complete would return, without needing its id.
//  * A remembered draft that is gone (discarded, swept after a week) or
//    already started falls back to a fresh draft, ONCE.
//  * Only a FATAL failure (refused, disk full, a fault before any upload)
//    discards the draft. A non-fatal one -- the server or network away for
//    now -- keeps the draft and what arrived of the file.
//
// Never logs: anything worth logging here would carry a client file name.

import { UploadError, batchNameFor, recallDraft, rememberDraft, forgetDraft,
         fingerprint } from './upload.js';

const gone = (err) => err?.status === 404;

// The same name the server stores (Uploads.create: NFC, whitespace stripped).
const serverName = (name) => String(name || '').normalize('NFC').trim();

// What a remembered draft already holds of this file: { stale } when it can
// no longer take it, { done } when the file already arrived, {} when the
// upload should resume (or start) in it.
async function inspectDraft(batchId, file, getBatch, fingerprintOf) {
  let view;
  try {
    view = await getBatch(batchId);
  } catch (err) {
    if (gone(err)) return { stale: true };
    // Not known to be gone: keep the draft and say "not now", so selecting
    // the file again tries the same draft rather than orphaning it.
    throw new UploadError('the server could not be reached', { status: err?.status || 0 });
  }
  if (!view?.batch?.draft) return { stale: true };
  const name = serverName(file.name);
  let ups = (view.uploads || []).filter((u) => u.state === 'complete' && u.name === name
    && u.size === file.size && (u.rel_path || '') === '');
  // Name, size and time survive `cp -p` onto other content: the fingerprint
  // (a hash of the file's ends, as the upload was keyed) must agree too.
  // Read only when there is a candidate -- it reads 2 MiB of the file.
  if (ups.length) {
    const key = await fingerprintOf(file);
    ups = ups.filter((u) => u.fingerprint === key);
  }
  // EVERY matching upload, not the first: one whose drawing has since been
  // removed (Stop deletes it) may stand before the one that staged it.
  const ids = new Set(ups.map((u) => u.id));
  const rows = (view.documents || []).filter((r) => ids.has(r.upload_id));
  // No staged row left at all: upload the file again into the same draft
  // rather than report an empty completion as unreadable.
  if (!rows.length) return {};
  // Several copies can only come from a race or an older client; the newest
  // is shown, and Start refuses until the draft holds just one (stagesOnly).
  return { done: { state: 'complete', documents: [rows[rows.length - 1]] } };
}

// Whether a draft holds exactly the drawing on screen. Start queues every
// included drawing of the draft, and the interim screen shows one: a second
// copy (an earlier attempt, a second tab) would be extracted unseen.
export function stagesOnly(view, docId) {
  const docs = view?.documents;
  return Boolean(view?.batch?.draft) && Array.isArray(docs) && docs.length === 1 && docs[0]?.id === docId;
}

// Resolves { batchId, row } with the staged row, or { batchId: null,
// unreadable: true, row } after discarding an unreadable file's draft.
// Rejects with the failure; an UploadError that is not fatal means the
// draft was kept for resuming.
export async function importSingle({
  file, storage, createBatch, getBatch, uploadFile, discardBatch, onProgress = () => {},
  fingerprint: fingerprintOf = fingerprint,
}) {
  const remembered = recallDraft(storage, file);
  let batchId = null;
  let done = null;
  try {
    if (remembered) {
      // Set before the draft is inspected: a file that cannot be read
      // while inspecting it must still find the draft kept (below).
      batchId = remembered;
      const found = await inspectDraft(remembered, file, getBatch, fingerprintOf);
      if (found.stale) {
        batchId = null;
      } else {
        done = found.done || null;
        if (!done) {
          try {
            done = await uploadFile({ file, batchId, onProgress });
          } catch (err) {
            // Refused as gone or already started since the view was read.
            const stale = err instanceof UploadError && err.fatal && (err.status === 404 || err.status === 409);
            if (!stale) throw err;
            batchId = null;
          }
        }
      }
      if (!batchId) forgetDraft(storage, file);
    }
    if (!done) {
      batchId = (await createBatch(batchNameFor([file]))).id;
      rememberDraft(storage, file, batchId);
      done = await uploadFile({ file, batchId, onProgress });
    }
  } catch (err) {
    // A file that cannot be read (moved, changed, a permission) is no fault
    // of a REMEMBERED draft: what already arrived of it stays for the next
    // selection. A fresh draft holds nothing of it yet and may go.
    const keepRemembered = err?.reason === 'file_unreadable' && batchId && batchId === remembered;
    if (!(err instanceof UploadError && !err.fatal) && !keepRemembered) {
      // Nothing worth resuming. Never a started import: batchId is only
      // ever a draft this call created or found still a draft, and the
      // server refuses to discard a started one anyway (409).
      forgetDraft(storage, file);
      if (batchId) await discardBatch(batchId);
    }
    throw err;
  }
  const row = done.documents?.[0] || null;
  if (!row || row.unreadable) {
    // Never includable, and the interim screen has nothing else to show.
    forgetDraft(storage, file);
    await discardBatch(batchId);
    return { batchId: null, unreadable: true, row };
  }
  return { batchId, row };
}
