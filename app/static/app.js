let sessionId = null;
let rows = [];
let imgEl = null;
let addMode = false;

const $ = (s) => document.querySelector(s);
const BAND_TOL = 60;

$("#file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("#status").textContent = "Extracting…";
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "upload failed" }));
    $("#status").textContent = err.detail;
    return;
  }
  const data = await res.json();
  sessionId = data.session_id;
  rows = data.rows;
  renderImage(data.image_url);
  renderGrid();
  $("#exportBtn").disabled = false;
  $("#exportPdfBtn").disabled = false;
  $("#status").textContent = `${rows.length} characteristics`;
});

function renderImage(url) {
  const left = $("#left");
  let img = left.querySelector("img");
  if (!img) { img = document.createElement("img"); left.prepend(img); }
  imgEl = img;
  img.onload = () => placeMarkers();
  img.src = url + "?t=" + Date.now();
}

function scales() {
  return { sx: imgEl.clientWidth / imgEl.naturalWidth,
           sy: imgEl.clientHeight / imgEl.naturalHeight };
}

function renumber() {
  // reading order: banded rows top-to-bottom, left-to-right within a band
  const c = (r) => r.target_region
    ? [(r.target_region[1] + r.target_region[3]) / 2,
       (r.target_region[0] + r.target_region[2]) / 2]
    : [r.balloon_xy[1], r.balloon_xy[0]];
  rows.sort((a, b) => {
    const [ay, ax] = c(a), [by, bx] = c(b);
    const band = Math.round(ay / BAND_TOL) - Math.round(by / BAND_TOL);
    return band !== 0 ? band : ax - bx;
  });
  rows.forEach((r, i) => (r.pos = i + 1));
}

function placeMarkers() {
  const overlay = $("#overlay");
  overlay.innerHTML = "";
  const { sx, sy } = scales();
  rows.forEach((r) => {
    if (!r.balloon_xy) return;
    const m = document.createElement("div");
    m.className = "marker";
    m.style.left = r.balloon_xy[0] * sx + "px";
    m.style.top = r.balloon_xy[1] * sy + "px";
    m.textContent = r.pos;
    m.title = "Pos " + r.pos;
    const del = document.createElement("div");
    del.className = "del";
    del.textContent = "×";
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteRow(r.id); });
    m.appendChild(del);
    makeDraggable(m, r);
    overlay.appendChild(m);
  });
}

function makeDraggable(m, r) {
  let dragging = false;
  m.addEventListener("mousedown", (e) => {
    if (e.target.classList.contains("del")) return;
    dragging = true; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = imgEl.getBoundingClientRect();
    const { sx, sy } = scales();
    const x = (e.clientX - rect.left), y = (e.clientY - rect.top);
    m.style.left = x + "px"; m.style.top = y + "px";
    r.balloon_xy = [x / sx, y / sy];
  });
  window.addEventListener("mouseup", () => { dragging = false; });
}

function deleteRow(id) {
  rows = rows.filter((r) => r.id !== id);
  renumber(); placeMarkers(); renderGrid();
}

$("#addBtn").addEventListener("click", () => {
  addMode = !addMode;
  $("#addBtn").classList.toggle("active", addMode);
  $("#left").classList.toggle("adding", addMode);
  $("#status").textContent = addMode
    ? "Add mode: drag a box around the missed callout"
    : `${rows.length} characteristics`;
});

// drag a box on the image (in add mode) -> /api/read_region
(function enableBoxDraw() {
  const left = $("#left");
  let start = null, rubber = null;
  left.addEventListener("mousedown", (e) => {
    if (!addMode || !imgEl) return;
    const rect = imgEl.getBoundingClientRect();
    start = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    rubber = document.createElement("div");
    rubber.style.cssText =
      "position:absolute;border:1px dashed #dc2626;background:rgba(220,38,38,.1);";
    $("#overlay").appendChild(rubber);
  });
  left.addEventListener("mousemove", (e) => {
    if (!start) return;
    const rect = imgEl.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    rubber.style.left = Math.min(start.x, x) + "px";
    rubber.style.top = Math.min(start.y, y) + "px";
    rubber.style.width = Math.abs(x - start.x) + "px";
    rubber.style.height = Math.abs(y - start.y) + "px";
  });
  left.addEventListener("mouseup", async (e) => {
    if (!start) return;
    const rect = imgEl.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const { sx, sy } = scales();
    const box = [Math.min(start.x, x) / sx, Math.min(start.y, y) / sy,
                 Math.max(start.x, x) / sx, Math.max(start.y, y) / sy];
    rubber.remove(); start = null; rubber = null;
    if (box[2] - box[0] < 4 || box[3] - box[1] < 4) return;   // ignore stray clicks
    const res = await fetch("/api/read_region", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, box }),
    });
    if (!res.ok) return;
    rows.push(await res.json());
    renumber(); placeMarkers(); renderGrid();
  });
})();

function renderGrid() {
  const tb = $("#grid tbody");
  tb.innerHTML = "";
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    if (r.needs_review) {
      tr.className = "low";
      tr.title = (r.review_reasons || []).join(", ");
    }
    const posCell = `${r.needs_review ? "⚠ " : ""}${r.pos}`;
    tr.innerHTML =
      `<td>${posCell}</td>` +
      ["char_type", "nominal", "upper_tol", "lower_tol"]
        .map((k) => `<td contenteditable data-i="${i}" data-k="${k}">${r[k] ?? ""}</td>`)
        .join("");
    tb.appendChild(tr);
  });
  tb.querySelectorAll("td[contenteditable]").forEach((td) => {
    td.addEventListener("input", () => {
      rows[+td.dataset.i][td.dataset.k] = td.textContent;
    });
  });
}

async function download(endpoint, filename) {
  const res = await fetch(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, rows }),
  });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

$("#exportBtn").addEventListener("click", () => download("/api/export", "inspection.xlsx"));
$("#exportPdfBtn").addEventListener("click", () => download("/api/export/pdf", "ballooned.pdf"));
