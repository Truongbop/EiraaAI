// ===== EIRAA – web báo cáo công việc (dùng chung, không đăng nhập) =====
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ls = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
const S = { info: null, users: [], view: null };
const STATUSES = ["Chưa làm", "Đang làm", "Hoàn thành", "Tạm dừng"];
const VIEWS = {
  report: ["Nộp báo cáo", loadReport], chat: ["Tin nhắn", loadChat], overview: ["Tổng quan", loadOverview], tasks: ["Công việc", loadTasks],
  history: ["Lịch sử báo cáo", loadHistory], users: ["Nhân viên", loadUsers], audit: ["Nhật ký", loadAudit],
};

// ---------- tiện ích ----------
async function api(path, opt = {}) {
  const isForm = opt.body instanceof FormData;
  const res = await fetch(path, {
    method: opt.method || (opt.body ? "POST" : "GET"),
    headers: isForm || !opt.body ? {} : { "Content-Type": "application/json" },
    body: opt.body ? (isForm ? opt.body : JSON.stringify(opt.body)) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data?.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || res.statusText);
  }
  return data;
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3200); }
async function busy(btn, fn) {
  const h = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Đang xử lý';
  try { return await fn(); } catch (e) { toast("❌ " + e.message); } finally { btn.disabled = false; btn.innerHTML = h; }
}
const fmtDate = (s) => s ? s.slice(8, 10) + "/" + s.slice(5, 7) + "/" + s.slice(0, 4) : "–";
const fmtDT = (s) => s ? new Date(s).toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }) : "";
const statusBadge = (s) => `<span class="badge ${{ "Hoàn thành": "b-ok", "Đang làm": "b-warn", "Tạm dừng": "b-bad" }[s] || "b-info"}">${esc(s)}</span>`;
const levelBadge = (s) => s ? `<span class="badge ${{ Cao: "b-bad", "Trung bình": "b-warn" }[s] || "b-info"}">${esc(s)}</span>` : "";
const bar = (p) => `<div class="progress" title="${p}%"><i style="width:${p}%"></i></div>`;
const today = () => S.info?.today || new Date().toISOString().slice(0, 10);
const isOverdue = (t) => t.status !== "Hoàn thành" && t.deadline && t.deadline < today();
const sizeFmt = (b) => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.ceil(b / 1024) + " KB";
const userOptions = (sel, first = "— Chọn nhân viên —") => `<option value="">${first}</option>` +
  S.users.map((u) => `<option value="${u.id}" ${String(u.id) === String(sel) ? "selected" : ""}>${esc(u.full_name)}${u.department ? " – " + esc(u.department) : ""}</option>`).join("");

function dialog(title, html) {
  return new Promise((resolve) => {
    $("#dlgTitle").textContent = title; $("#dlgBody").innerHTML = html;
    const d = $("#dlg");
    d.onclose = () => resolve(d.returnValue === "ok" ? Object.fromEntries(new FormData($("#dlgForm"))) : null);
    d.returnValue = ""; d.showModal();
  });
}

// ---------- điều hướng ----------
function go(v) {
  if (!VIEWS[v]) v = "report";
  S.view = v;
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.v === v));
  $$(".view").forEach((s) => s.classList.toggle("hidden", s.id !== "v-" + v));
  $("#viewTitle").textContent = VIEWS[v][0];
  $("#sidebar").classList.remove("open");
  history.replaceState(null, "", "#" + v);
  VIEWS[v][1]().catch((e) => toast("❌ " + e.message));
}
$$("#nav a").forEach((a) => (a.onclick = () => go(a.dataset.v)));
$("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
$("#themeBtn").onclick = () => {
  const r = document.documentElement, dark = r.dataset.theme === "dark" || (!r.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  r.dataset.theme = dark ? "light" : "dark"; ls.set("theme", r.dataset.theme);
};
if (ls.get("theme")) document.documentElement.dataset.theme = ls.get("theme");
async function refreshUsers() {
  S.users = await api("/api/users");
  $("#reporter").innerHTML = userOptions(ls.get("reporter"), "— Chọn tên của bạn —");
  $("#chatAs").innerHTML = userOptions(ls.get("reporter"), "— Gửi với tên —");
  $$(".userSelect").forEach((s) => (s.innerHTML = userOptions(s.value)));
  for (const id of ["#taskPerson", "#histPerson"]) { const v = $(id).value; $(id).innerHTML = userOptions(v, "Tất cả nhân viên"); }
}

// ================= NỘP BÁO CÁO (file Excel / Word) =================
const OK_EXT = [".xlsx", ".xls", ".docx", ".doc"];
let picked = [];
const extOf = (n) => n.slice(n.lastIndexOf(".")).toLowerCase();
const fileIcon = (n) => ([".xlsx", ".xls"].includes(extOf(n)) ? "📗" : "📘");
function addFiles(list) {
  for (const f of list) {
    if (!OK_EXT.includes(extOf(f.name))) { toast("❌ " + f.name + ": chỉ nhận file Excel hoặc Word"); continue; }
    if (f.size > 20 * 1048576) { toast("❌ " + f.name + ": quá 20MB"); continue; }
    if (!picked.some((p) => p.name === f.name && p.size === f.size)) picked.push(f);
  }
  if (!$("#subject").value && picked.length) $("#subject").value = picked[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ");
  renderPicked();
}
function renderPicked() {
  $("#pickedList").innerHTML = picked.map((f, i) => `<div class="file">${fileIcon(f.name)} <b>${esc(f.name)}</b>
    <span class="muted">${sizeFmt(f.size)}</span><span class="grow"></span><button type="button" data-unpick="${i}" title="Bỏ">✕</button></div>`).join("");
  $$("[data-unpick]").forEach((b) => (b.onclick = () => { picked.splice(Number(b.dataset.unpick), 1); renderPicked(); }));
}
const drop = $("#drop");
$("#fileInput").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));
$("#reporter").onchange = () => ls.set("reporter", $("#reporter").value);
$("#subDays").onchange = loadReport;

$("#submitForm").onsubmit = (e) => {
  e.preventDefault();
  if (!picked.length) return toast("Hãy chọn file Excel hoặc Word");
  busy($("#submitBtn"), async () => {
    const fd = new FormData();
    fd.append("user_id", $("#reporter").value);
    fd.append("subject", $("#subject").value.trim());
    picked.forEach((f) => fd.append("files", f));
    await api("/api/submit", { body: fd });
    toast(`✅ Đã nộp ${picked.length} file: ${$("#subject").value}`);
    picked = []; renderPicked(); $("#subject").value = "";
    loadReport();
  });
};

async function loadReport() {
  const list = await api("/api/submissions?days=" + $("#subDays").value);
  $("#subList").innerHTML = list.length ? list.map((a) => `
    <div class="sub">
      <div class="sub-icon">${fileIcon(a.filename)}</div>
      <div class="grow">
        <div class="row between"><b class="sub-title">${esc(a.subject || a.filename)}</b><span class="muted">${fmtDT(a.created_at)}</span></div>
        <div class="muted">👤 ${esc(a.full_name)}${a.department ? " · " + esc(a.department) : ""} · <a href="/api/attachments/${a.id}" target="_blank" rel="noopener">${esc(a.filename)}</a> · ${sizeFmt(a.size)}</div>
        ${a.preview ? `<details><summary class="muted">Xem nội dung</summary><pre class="preview">${esc(a.preview)}${a.preview.length >= 400 ? "\n…" : ""}</pre></details>` : ""}
      </div>
      <button class="btn sm danger" data-delsub="${a.id}" title="Xóa file">✕</button>
    </div>`).join("") : `<p class="empty">Chưa có báo cáo nào được nộp.</p>`;
  $$("[data-delsub]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Xóa file báo cáo này?")) return;
    await api("/api/attachments/" + b.dataset.delsub, { method: "DELETE" }); toast("Đã xóa"); loadReport();
  }));
}

// ================= TIN NHẮN (đồng nghiệp + khách hàng) =================
const CH = { id: Number(ls.get("chatId")) || -1, threads: [], timer: null };
const hm = (iso) => iso ? `${iso.slice(11, 16)} ${iso.slice(8, 10)}/${iso.slice(5, 7)}` : "";
async function loadThreads() {
  CH.threads = await api("/api/chat/threads");
  const waiting = CH.threads.filter((t) => t.type === "web_customer" && t.waiting).length;
  $("#chatBadge").textContent = waiting; $("#chatBadge").classList.toggle("hidden", !waiting);
  const item = (t) => `<button class="thread ${t.chat_id === CH.id ? "active" : ""}" data-tid="${t.chat_id}">
      <span class="t-ico">${t.type === "web_internal" ? "👥" : "🧑‍💼"}</span>
      <span class="grow"><b>${esc(t.title)}</b><small class="muted">${esc((t.last_text || "Chưa có tin nhắn").slice(0, 60))}</small></span>
      <span class="t-meta"><small class="muted">${hm(t.last_at)}</small>${t.type === "web_customer" && t.waiting ? '<span class="badge b-warn">⏳ chờ</span>' : ""}</span></button>`;
  const internal = CH.threads.filter((t) => t.type === "web_internal");
  const cus = CH.threads.filter((t) => t.type === "web_customer");
  $("#threadList").innerHTML = `<div class="t-group">Đồng nghiệp</div>${internal.map(item).join("")}
    <div class="t-group">Khách hàng (${cus.length})</div>${cus.map(item).join("") || '<p class="muted small">Chưa có khách hàng nhắn tin.</p>'}`;
  $$("[data-tid]").forEach((b) => (b.onclick = () => openThread(Number(b.dataset.tid))));
}
async function openThread(id, show = true) {
  CH.id = id; ls.set("chatId", id);
  if (show) $(".chat-wrap").classList.add("open");
  $$("[data-tid]").forEach((b) => b.classList.toggle("active", Number(b.dataset.tid) === id));
  const t = CH.threads.find((x) => x.chat_id === id);
  $("#convTitle").textContent = t ? t.title : "";
  $("#convSub").textContent = t?.type === "web_customer" ? "Khách hàng · nhân viên trả lời bên dưới" : "Chat nội bộ giữa đồng nghiệp";
  const msgs = await api("/api/chat/messages?chat_id=" + id);
  const box = $("#msgList");
  let lastDay = "";
  box.innerHTML = msgs.length ? msgs.map((m) => {
    const day = m.sent_at.slice(0, 10), mine = m.user_id !== null && t?.type === "web_customer";
    const sep = day !== lastDay ? `<div class="day-sep">${fmtDate(day)}</div>` : ""; lastDay = day;
    const cls = t?.type === "web_customer" ? (m.user_id === null ? "them" : "me") : (String(m.user_id) === $("#chatAs").value ? "me" : "them");
    return `${sep}<div class="bubble-row ${cls}"><div class="bubble">
      <div class="b-name">${esc(m.user_name)}${m.user_id === null ? ' <span class="badge b-info">Khách</span>' : ""}</div>
      <div class="b-text">${esc(m.text)}</div><div class="b-time">${m.sent_at.slice(11, 16)}</div></div></div>`;
  }).join("") : `<p class="empty">Chưa có tin nhắn. Hãy gửi tin đầu tiên.</p>`;
  box.scrollTop = box.scrollHeight;
}
async function loadChat() {
  await loadThreads();
  if (!CH.threads.some((t) => t.chat_id === CH.id)) CH.id = CH.threads[0]?.chat_id ?? -1;
  $(".chat-wrap").classList.remove("open");
  await openThread(CH.id, false);
  clearInterval(CH.timer);
  CH.timer = setInterval(async () => {
    if (S.view !== "chat") return clearInterval(CH.timer);
    const atBottom = $("#msgList").scrollHeight - $("#msgList").scrollTop - $("#msgList").clientHeight < 40;
    await loadThreads(); if (atBottom) await openThread(CH.id, false);
  }, 8000);
}
$("#chatAs").onchange = () => { ls.set("reporter", $("#chatAs").value); openThread(CH.id); };
$("#convBack").onclick = () => $(".chat-wrap").classList.remove("open");
$("#chatText").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chatForm").requestSubmit(); } });
$("#chatForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("#chatText").value.trim();
  if (!$("#chatAs").value) { $("#chatAs").focus(); $("#chatAs").classList.add("need"); return toast("👈 Chọn tên của bạn ở ô “Gửi với tên” trước khi gửi"); }
  $("#chatAs").classList.remove("need");
  if (!text) return;
  try {
    await api("/api/chat/send", { body: { chat_id: CH.id, user_id: Number($("#chatAs").value), text } });
    $("#chatText").value = ""; await loadThreads(); await openThread(CH.id);
  } catch (err) { toast("❌ " + err.message); }
};
$("#newCustomerBtn").onclick = async () => {
  const f = await dialog("Khách hàng mới", `<p class="muted">Ghi lại tin nhắn khách gửi (qua điện thoại, Zalo, email…). Khách cũng có thể tự nhắn ở trang /lienhe.html</p>
    <label><span>Tên khách *</span><input name="name" required minlength="2" placeholder="VD: Anh Minh"></label>
    <div class="grid-2"><label><span>Công ty</span><input name="company"></label><label><span>Số điện thoại</span><input name="phone"></label></div>
    <label><span>Khách nhắn *</span><textarea name="text" rows="3" required></textarea></label>`);
  if (!f) return;
  try {
    const r = await api("/api/chat/customer", { body: { ...f, user_id: Number($("#chatAs").value) || null } });
    await loadThreads(); openThread(r.chat_id); toast("✅ Đã tạo cuộc trò chuyện với " + f.name);
  } catch (err) { toast("❌ " + err.message); }
};

// ================= TỔNG QUAN =================
$("#ovDate").onchange = loadOverview;
const reportBlock = (r, showName = true) => `<div class="rep">
  <div class="rep-h"><b>${showName ? `${esc(r.full_name)} <span class="muted">· ${esc(r.department || "")} · ${fmtDate(r.report_date)}</span>` : fmtDate(r.report_date)}</b>
    <span class="row gap8">${levelBadge(r.issue_level)}${bar(r.progress)}<b>${r.progress}%</b></span></div>
  <p><span class="lbl">Nội dung:</span> ${esc(r.done)}</p>
  ${r.plan ? `<p><span class="lbl">Kế hoạch:</span> ${esc(r.plan)}</p>` : ""}
  ${r.issues ? `<p><span class="lbl">Vấn đề:</span> ${esc(r.issues)}</p>` : ""}
  ${r.proposals ? `<p><span class="lbl">Đề xuất:</span> ${esc(r.proposals)}</p>` : ""}
  ${(r.attachments || []).map((a) => `<div class="file">${fileIcon(a.filename)} <b>${esc(a.subject || "")}</b> <a href="/api/attachments/${a.id}" target="_blank" rel="noopener">${esc(a.filename)}</a></div>`).join("")}
</div>`;
async function loadOverview() {
  if (!$("#ovDate").value) $("#ovDate").value = today();
  const d = await api("/api/overview?d=" + $("#ovDate").value);
  const s = d.stats;
  $("#kpis").innerHTML = [["Đã nộp", `${s.submitted}/${s.staff}`, s.submitted >= s.staff ? "ok" : ""], ["Chưa nộp", s.staff - s.submitted, s.staff - s.submitted ? "bad" : "ok"],
    ["Việc đang mở", s.tasks_open], ["Quá hạn", s.tasks_overdue, s.tasks_overdue ? "bad" : ""], ["Đã hoàn thành", s.tasks_done, "ok"], ["Vấn đề mức cao", s.issues_high, s.issues_high ? "bad" : ""]]
    .map(([k, v, c]) => `<div class="kpi ${c || ""}"><span>${k}</span><b>${v}</b></div>`).join("");
  $("#missingList").innerHTML = d.missing.length ? d.missing.map((m) => `<div class="file">👤 ${esc(m.full_name)} <span class="muted">${esc(m.department || "")}</span></div>`).join("")
    : `<p class="muted">✅ Tất cả đã nộp.</p>`;
  const iss = d.reports.filter((r) => r.issues);
  $("#issueList").innerHTML = iss.length ? iss.map((r) => `<div class="file">${levelBadge(r.issue_level)} <span><b>${esc(r.full_name)}:</b> ${esc(r.issues)}</span></div>`).join("")
    : `<p class="muted">Không có vấn đề.</p>`;
  $("#ovReports").innerHTML = d.reports.length ? d.reports.map((r) => reportBlock(r)).join("") : `<p class="empty">Chưa có báo cáo trong ngày.</p>`;
}

// ================= CÔNG VIỆC =================
$("#taskFilter").innerHTML += STATUSES.map((s) => `<option>${s}</option>`).join("");
$("#taskFilter").onchange = $("#overdueOnly").onchange = $("#taskPerson").onchange = loadTasks;
async function loadTasks() {
  let ts = await api("/api/tasks" + ($("#taskPerson").value ? "?user_id=" + $("#taskPerson").value : ""));
  if ($("#taskFilter").value) ts = ts.filter((t) => t.status === $("#taskFilter").value);
  if ($("#overdueOnly").checked) ts = ts.filter(isOverdue);
  $("#taskTable").innerHTML = `<tr><th>Công việc</th><th>Phụ trách</th><th>Hạn</th><th>Trạng thái</th><th>Tiến độ</th><th></th></tr>` +
    (ts.map((t) => `<tr><td><b>${esc(t.title)}</b>${t.description ? `<div class="muted">${esc(t.description)}</div>` : ""}</td>
      <td>${esc(t.assignee || "–")}<div class="muted">${esc(t.department || "")}</div></td>
      <td style="color:${isOverdue(t) ? "var(--bad)" : "inherit"}">${fmtDate(t.deadline)}${isOverdue(t) ? "<div class='muted' style='color:var(--bad)'>quá hạn</div>" : ""}</td>
      <td><select data-st="${t.id}">${STATUSES.map((s) => `<option ${s === t.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
      <td><div class="row gap8">${bar(t.progress)}<input type="number" min="0" max="100" value="${t.progress}" data-pr="${t.id}" style="width:70px" title="Tiến độ %"></div></td>
      <td><div class="row gap8"><button class="btn sm" data-edit="${t.id}">Sửa</button><button class="btn sm danger" data-rm="${t.id}">Xóa</button></div></td></tr>`).join("")
      || `<tr><td colspan="6" class="empty">Không có công việc.</td></tr>`);
  const patch = async (id, body) => { try { await api("/api/tasks/" + id, { method: "PATCH", body }); toast("✅ Đã cập nhật"); loadTasks(); } catch (e) { toast("❌ " + e.message); } };
  $$("[data-st]").forEach((s) => (s.onchange = () => patch(s.dataset.st, { status: s.value })));
  $$("[data-pr]").forEach((i) => (i.onchange = () => patch(i.dataset.pr, { progress: Math.max(0, Math.min(100, Number(i.value))) })));
  $$("[data-rm]").forEach((b) => (b.onclick = async () => { if (confirm("Xóa công việc này?")) { await api("/api/tasks/" + b.dataset.rm, { method: "DELETE" }); loadTasks(); } }));
  $$("[data-edit]").forEach((b) => (b.onclick = async () => {
    const t = ts.find((x) => x.id == b.dataset.edit);
    const f = await dialog("Sửa công việc", `<label><span>Tên</span><input name="title" value="${esc(t.title)}" required></label>
      <label><span>Mô tả</span><textarea name="description" rows="3">${esc(t.description)}</textarea></label>
      <label><span>Phụ trách</span><select name="assignee_id">${userOptions(t.assignee_id)}</select></label>
      <label><span>Hạn</span><input type="date" name="deadline" value="${esc(t.deadline)}"></label>
      <label><span>Ghi chú tiến độ</span><input name="note" placeholder="Không bắt buộc"></label>`);
    if (!f) return;
    f.assignee_id = f.assignee_id ? Number(f.assignee_id) : null;
    patch(t.id, f);
  }));
}
$("#taskForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target)); f.assignee_id = Number(f.assignee_id);
  try { await api("/api/tasks", { body: f }); e.target.reset(); toast("✅ Đã giao việc"); loadTasks(); } catch (err) { toast("❌ " + err.message); }
};

// ================= LỊCH SỬ =================
$("#histPerson").onchange = $("#histDays").onchange = loadHistory;
async function loadHistory() {
  const uid = $("#histPerson").value;
  const rs = await api(`/api/reports?days=${$("#histDays").value}${uid ? "&user_id=" + uid : ""}`);
  $("#historyList").innerHTML = rs.length ? rs.map((r) => reportBlock(r)).join("") : `<p class="empty">Chưa có báo cáo nào.</p>`;
}

// ================= NHÂN VIÊN =================
async function loadUsers() {
  const all = await api("/api/users?all=true");
  $("#deptList").innerHTML = [...new Set(all.map((u) => u.department).filter(Boolean))].map((d) => `<option value="${esc(d)}">`).join("");
  $("#userTable").innerHTML = `<tr><th>Họ tên</th><th>Phòng ban</th><th>Chức vụ</th><th>Trạng thái</th><th></th></tr>` +
    (all.map((u) => `<tr style="opacity:${u.active ? 1 : .5}"><td><b>${esc(u.full_name)}</b></td><td>${esc(u.department)}</td><td>${esc(u.position)}</td>
      <td>${u.active ? '<span class="badge b-ok">Đang làm việc</span>' : '<span class="badge b-bad">Đã nghỉ</span>'}</td>
      <td><button class="btn sm" data-uedit="${u.id}">Sửa</button></td></tr>`).join("") || `<tr><td colspan="5" class="empty">Chưa có nhân viên. Thêm ở ô phía trên.</td></tr>`);
  $$("[data-uedit]").forEach((b) => (b.onclick = async () => {
    const u = all.find((x) => x.id == b.dataset.uedit);
    const f = await dialog("Sửa nhân viên", `<label><span>Họ tên</span><input name="full_name" value="${esc(u.full_name)}" required></label>
      <label><span>Phòng ban</span><input name="department" value="${esc(u.department)}" list="deptList"></label>
      <label><span>Chức vụ</span><input name="position" value="${esc(u.position)}"></label>
      <label><span>Trạng thái</span><select name="active"><option value="1">Đang làm việc</option><option value="0" ${u.active ? "" : "selected"}>Đã nghỉ (ẩn khỏi danh sách)</option></select></label>`);
    if (!f) return;
    f.active = Number(f.active);
    try { await api("/api/users/" + u.id, { method: "PATCH", body: f }); toast("✅ Đã lưu"); await refreshUsers(); loadUsers(); } catch (e) { toast("❌ " + e.message); }
  }));
}
$("#userForm").onsubmit = async (e) => {
  e.preventDefault();
  try { await api("/api/users", { body: Object.fromEntries(new FormData(e.target)) }); e.target.reset(); toast("✅ Đã thêm nhân viên"); await refreshUsers(); loadUsers(); }
  catch (err) { toast("❌ " + err.message); }
};

// ================= NHẬT KÝ =================
async function loadAudit() {
  const a = await api("/api/audit");
  $("#auditTable").innerHTML = `<tr><th>Thời gian</th><th>Người</th><th>Hành động</th><th>Chi tiết</th></tr>` +
    a.map((x) => `<tr><td>${fmtDT(x.created_at)}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td>${esc(x.detail)}</td></tr>`).join("");
}

// ================= KHỞI ĐỘNG =================
(async () => {
  try {
    S.info = await api("/api/info");
    $("#companyName").textContent = S.info.company;
    $("#botStatus").innerHTML = S.info.bot ? `<span class="dot ok"></span>Bot Telegram @${esc(S.info.bot)} đang chạy` : `<span class="dot"></span>Bot Telegram chưa chạy`;
    await refreshUsers();
    go(location.hash.slice(1));
  } catch (e) { toast("❌ Không kết nối được máy chủ: " + e.message); }
})();
window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (v && v !== S.view) go(v); });
