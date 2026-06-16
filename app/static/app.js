let sessionId = null;
let rows = [];

const $ = (s) => document.querySelector(s);

$("#file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("#status").textContent = "Extracting…";
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await res.json();
  sessionId = data.session_id;
  rows = data.rows;
  renderImage(data.image_url);
  renderGrid();
  $("#exportBtn").disabled = false;
  $("#status").textContent = `${rows.length} balloons`;
});

function renderImage(url) {
  const left = $("#left");
  let img = left.querySelector("img");
  if (!img) { img = document.createElement("img"); left.prepend(img); }
  img.onload = () => placeMarkers(img);
  img.src = url + "?t=" + Date.now();
}

function placeMarkers(img) {
  const overlay = $("#overlay");
  overlay.innerHTML = "";
  const sx = img.clientWidth / img.naturalWidth;
  const sy = img.clientHeight / img.naturalHeight;
  rows.forEach((r) => {
    if (!r.balloon_xy) return;
    const m = document.createElement("div");
    m.className = "marker";
    m.style.left = r.balloon_xy[0] * sx + "px";
    m.style.top = r.balloon_xy[1] * sy + "px";
    m.title = "Pos " + r.pos;
    overlay.appendChild(m);
  });
}

function renderGrid() {
  const tb = $("#grid tbody");
  tb.innerHTML = "";
  rows.sort((a, b) => a.pos - b.pos).forEach((r, i) => {
    const tr = document.createElement("tr");
    if ((r.confidence ?? 0) < 0.6) tr.className = "low";
    tr.innerHTML =
      `<td>${r.pos}</td>` +
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

$("#exportBtn").addEventListener("click", async () => {
  const res = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, rows }),
  });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "inspection.xlsx";
  a.click();
});
