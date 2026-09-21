const api = (path, opts) => fetch(path, opts).then(async (r) => {
  const body = await r.json().catch(() => null);
  if (!r.ok) throw { status: r.status, body };
  return body;
});

let berths = [];
let vessels = [];
let pendingForceBooking = null;

async function loadReference() {
  [berths, vessels] = await Promise.all([api("/berths"), api("/vessels")]);
  document.getElementById("berth_id").innerHTML =
    berths.map(b => `<option value="${b.id}">${b.code} (${b.length_ft}ft, ${b.location})</option>`).join("");
  document.getElementById("vessel_id").innerHTML =
    vessels.map(v => `<option value="${v.id}">${v.name} (LOA ${v.loa_ft != null ? v.loa_ft + "ft" : "unknown"})</option>`).join("");
}

document.getElementById("kind").addEventListener("change", (e) => {
  const isVessel = e.target.value === "vessel";
  document.getElementById("vessel-field").style.display = isVessel ? "" : "none";
  document.getElementById("event-field").style.display = isVessel ? "none" : "";
  document.getElementById("suggest-btn").style.display = isVessel ? "" : "none";
});

function renderIssues(issues) {
  return issues.map(i => `<div class="issue ${i.severity}">${i.severity.toUpperCase()}: ${i.message}</div>`).join("");
}

function prefillBooking({ berthId, date }) {
  document.getElementById("booking-form-card").scrollIntoView({ behavior: "smooth", block: "center" });
  if (berthId) document.getElementById("berth_id").value = berthId;
  if (date) {
    document.getElementById("start_date").value = date;
    document.getElementById("end_date").value = date;
  }
  document.getElementById("form-status").innerHTML = "";
  document.getElementById("force-btn").style.display = "none";
}

async function submitBooking(force) {
  const statusEl = document.getElementById("form-status");
  const forceBtn = document.getElementById("force-btn");
  const kind = document.getElementById("kind").value;
  const payload = {
    kind,
    berth_id: Number(document.getElementById("berth_id").value),
    start_date: document.getElementById("start_date").value,
    end_date: document.getElementById("end_date").value,
    force: !!force,
  };
  if (kind === "vessel") {
    payload.vessel_id = Number(document.getElementById("vessel_id").value);
  } else {
    payload.event_name = document.getElementById("event_name").value;
  }
  if (force) payload.override_reason = "Accepted via UI override";

  try {
    await api("/bookings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    statusEl.innerHTML = `<div class="issue ok">Booked successfully.</div>`;
    forceBtn.style.display = "none";
    pendingForceBooking = null;
    loadCalendar();
  } catch (err) {
    const issues = err.body?.detail?.issues ?? [];
    statusEl.innerHTML = renderIssues(issues) || `<div class="issue error">${JSON.stringify(err.body)}</div>`;
    const onlyWarnings = issues.length > 0 && issues.every(i => i.severity === "warning");
    forceBtn.style.display = onlyWarnings ? "" : "none";
    pendingForceBooking = onlyWarnings;
  }
}

document.getElementById("booking-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitBooking(false);
});
document.getElementById("force-btn").addEventListener("click", () => {
  if (pendingForceBooking) submitBooking(true);
});

document.getElementById("suggest-btn").addEventListener("click", async () => {
  const el = document.getElementById("suggest-inline-results");
  const vesselId = document.getElementById("vessel_id").value;
  const start = document.getElementById("start_date").value;
  const end = document.getElementById("end_date").value;
  if (!vesselId || !start || !end) {
    el.innerHTML = `<div class="issue warning">Pick a vessel and both dates first.</div>`;
    return;
  }
  try {
    const results = await api(`/bookings/suggest?vessel_id=${vesselId}&start_date=${start}&end_date=${end}`);
    if (results.length === 0) {
      el.innerHTML = `<div class="issue error">No berth fits this vessel for those dates.</div>`;
      return;
    }
    el.innerHTML = results.map(r => `
      <div class="issue ${r.warnings.length ? 'warning' : 'ok'}" data-pick="${r.berth.id}" style="cursor:pointer">
        <strong>${r.berth.code}</strong> — ${r.berth.length_ft}ft, ${r.berth.location}
        ${r.warnings.length ? "<br>" + r.warnings.map(w => w.message).join("<br>") : " — good fit"}
        <br><em>click to select</em>
      </div>`).join("");
    el.querySelectorAll("[data-pick]").forEach(node => {
      node.addEventListener("click", () => {
        document.getElementById("berth_id").value = node.dataset.pick;
        el.innerHTML = "";
      });
    });
  } catch (err) {
    el.innerHTML = `<div class="issue error">${err.body?.detail ?? "Could not fetch suggestions."}</div>`;
  }
});

async function showCellDetail(occupants, berthCode, day, dateStr) {
  const el = document.getElementById("cell-detail");
  const rows = await Promise.all(occupants.map(async (o) => `
    <div class="issue ${occupants.length > 1 ? 'error' : 'ok'}">
      <strong>${o.name}</strong> (${o.kind}) on ${berthCode}, ${dateStr}
      <button class="secondary" data-del="${o.booking_id}" style="margin-left:10px">Delete</button>
    </div>`));
  el.innerHTML = `<div class="card" style="margin-top:12px; padding:14px;">${rows.join("")}</div>`;
  el.querySelectorAll("[data-del]").forEach(btn => {
    btn.addEventListener("click", async () => {
      await api(`/bookings/${btn.dataset.del}`, { method: "DELETE" });
      el.innerHTML = "";
      loadCalendar();
    });
  });
}

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

function initCalendarControls() {
  const monthSel = document.getElementById("cal-month");
  monthSel.innerHTML = MONTH_NAMES.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("");
  monthSel.value = 8; // matches the sample data (Aug 1997)
  document.getElementById("cal-year").addEventListener("change", loadCalendar);
  monthSel.addEventListener("change", loadCalendar);
}

async function loadCalendar() {
  const year = document.getElementById("cal-year").value;
  const month = document.getElementById("cal-month").value;
  const el = document.getElementById("calendar-grid");
  if (!year || !month) return;

  let data;
  try {
    data = await api(`/calendar?year=${year}&month=${month}`);
  } catch (err) {
    el.innerHTML = `<div class="issue error">Could not load calendar: ${JSON.stringify(err.body ?? err)}</div>`;
    return;
  }

  if (data.rows.length === 0) {
    el.innerHTML = `<div class="issue warning">No berths in the system yet.</div>`;
    return;
  }

  const dayHeaders = Array.from({ length: data.days_in_month }, (_, i) => i + 1)
    .map(d => `<th>${d}</th>`).join("");

  const pad = (n) => String(n).padStart(2, "0");

  const bodyRows = data.rows.map(row => {
    const cells = row.days.map(dayInfo => {
      const dateStr = `${year}-${pad(month)}-${pad(dayInfo.day)}`;
      if (dayInfo.occupants.length === 0) {
        return `<td class="cal-cell" data-berth="${row.berth_id}" data-date="${dateStr}"></td>`;
      }
      const cls = dayInfo.conflict ? "conflict" : (dayInfo.occupants[0].kind === "event" ? "occupied-event" : "occupied-vessel");
      const title = dayInfo.occupants.map(o => o.name).join(" / ");
      const label = dayInfo.occupants.length > 1 ? `${dayInfo.occupants.length}⚠` : (dayInfo.occupants[0].name || "").slice(0, 3);
      return `<td class="cal-cell ${cls}" title="${title.replace(/"/g, "'")}"
                  data-berth="${row.berth_id}" data-date="${dateStr}" data-occupants='${JSON.stringify(dayInfo.occupants)}'>${label}</td>`;
    }).join("");
    return `<tr><td class="berth-label">${row.berth_code} (${row.berth_length_ft}ft)</td>${cells}</tr>`;
  }).join("");

  el.innerHTML = `
    <table class="cal-table">
      <thead><tr><th class="berth-label">Berth</th>${dayHeaders}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
    <div class="cal-legend">
      <span><span class="swatch" style="background:#dbe9ff"></span>vessel</span>
      <span><span class="swatch" style="background:#ffe6c7"></span>event</span>
      <span><span class="swatch" style="background:#f8b4ac"></span>double-booking</span>
    </div>`;

  document.getElementById("cell-detail").innerHTML = "";

  el.querySelectorAll(".cal-cell").forEach(cell => {
    cell.addEventListener("click", () => {
      const occupants = cell.dataset.occupants ? JSON.parse(cell.dataset.occupants) : [];
      const berthId = cell.dataset.berth;
      const dateStr = cell.dataset.date;
      if (occupants.length === 0) {
        prefillBooking({ berthId, date: dateStr });
      } else {
        const berthCode = data.rows.find(r => String(r.berth_id) === berthId)?.berth_code ?? berthId;
        showCellDetail(occupants, berthCode, dateStr, dateStr);
      }
    });
  });
}

document.getElementById("run-audit").addEventListener("click", async () => {
  const result = await api("/audit");
  document.getElementById("audit-summary").innerHTML = `
    <span>Total bookings: <strong>${result.summary.total_bookings}</strong></span>
    <span>Double-booking pairs: <strong>${result.summary.double_booking_pairs}</strong></span>
    <span>Fit errors: <strong>${result.summary.fit_errors}</strong></span>
    <span>Fit warnings: <strong>${result.summary.fit_warnings}</strong></span>
  `;
  const berthById = Object.fromEntries(berths.map(b => [b.id, b]));
  const dbHtml = result.double_bookings.map(d => `
    <div class="issue error">Berth ${berthById[d.berth_id]?.code ?? d.berth_id}: booking #${d.booking_a} (${d.dates_a.join(" to ")})
      overlaps booking #${d.booking_b} (${d.dates_b.join(" to ")})
      ${d.suggested_alternative_for_b ? `<br><strong>Suggested fix:</strong> move booking #${d.booking_b} to berth ${d.suggested_alternative_for_b} instead.` : ""}
      </div>`).join("");
  const fitHtml = result.fit_violations.map(f => `
    <div class="issue ${f.severity}">Booking #${f.booking_id} — ${f.vessel_name} @ ${f.berth_code}: ${f.message}</div>`).join("");
  document.getElementById("audit-results").innerHTML = dbHtml + fitHtml || `<div class="issue ok">No issues found.</div>`;
});

// ---------- Dashboard: utilization + conflict-frequency charts ----------

function renderBarChart(el, rows, { critical = false, unit = "" } = {}) {
  if (rows.length === 0) {
    el.innerHTML = `<div class="viz-empty">No data yet.</div>`;
    return;
  }
  const max = Math.max(...rows.map(r => r.value), 1);
  const trackCls = critical ? "viz-bar-track track-critical" : "viz-bar-track";
  const fillCls = critical ? "viz-bar-fill fill-critical" : "viz-bar-fill";
  el.innerHTML = `<div class="viz-root">` + rows.map(r => `
    <div class="viz-bar-row" title="${r.label}: ${r.value}${unit}">
      <div class="viz-bar-label">${r.label}</div>
      <div class="${trackCls}"><div class="${fillCls}" style="width:${Math.max(2, 100 * r.value / max)}%"></div></div>
      <div class="viz-bar-value">${r.value}${unit}</div>
    </div>`).join("") + `</div>`;
}

async function loadDashboard() {
  const data = await api("/analytics");
  const windowEl = document.getElementById("dashboard-window");
  windowEl.textContent = data.window
    ? `Covering ${data.window.start} to ${data.window.end} (${data.window.total_days} days) · ${data.summary.total_bookings} bookings · busiest berth: ${data.summary.busiest_berth} (${data.summary.busiest_berth_pct}% occupied)`
    : "No bookings yet.";

  renderBarChart(
    document.getElementById("chart-utilization"),
    data.utilization.map(u => ({ label: u.berth_code, value: u.utilization_pct })),
    { unit: "%" }
  );

  renderBarChart(
    document.getElementById("chart-conflicts"),
    data.conflicts_by_month.map(c => ({ label: c.month, value: c.conflict_days })),
    { critical: true, unit: " day" + (data.conflicts_by_month.length === 1 ? "" : "s") }
  );

  const vesselRows = data.top_vessels.map(v => `
    <tr><td>${v.name}</td><td class="num">${v.booking_count}</td><td class="num">${v.total_days}</td></tr>`).join("");
  document.getElementById("top-vessels-table").innerHTML = data.top_vessels.length === 0
    ? `<div class="viz-empty">No vessel bookings yet.</div>`
    : `<table class="simple-table">
        <thead><tr><th>Vessel</th><th class="num">Bookings</th><th class="num">Total days berthed</th></tr></thead>
        <tbody>${vesselRows}</tbody>
      </table>`;
}

// ---------- Batch conflict resolution ----------

let currentPlan = null;

document.getElementById("compute-plan").addEventListener("click", async () => {
  const el = document.getElementById("resolve-results");
  el.innerHTML = `<div class="viz-empty">Computing...</div>`;
  currentPlan = await api("/audit/resolve");

  const parts = [];
  parts.push(`<div class="issue ok">Kept in place: ${currentPlan.kept_count}. Reassigned: ${currentPlan.moves.length}. Unresolved: ${currentPlan.unresolved.length}.</div>`);
  for (const m of currentPlan.moves) {
    parts.push(`<div class="plan-move ${m.needs_review ? 'needs-review' : ''}">
      Move booking #${m.booking_id} (<strong>${m.occupant}</strong>, ${m.dates.join(" to ")}) from ${m.from_berth} &rarr; ${m.to_berth}
      ${m.needs_review ? "<br><em>Vessel dimensions unknown -- verify fit manually before confirming.</em>" : ""}
    </div>`);
  }
  for (const u of currentPlan.unresolved) {
    parts.push(`<div class="issue error">Booking #${u.booking_id} (${u.occupant} @ ${u.berth}, ${u.dates.join(" to ")}): ${u.reason}</div>`);
  }
  el.innerHTML = parts.join("");
  document.getElementById("apply-plan").style.display = currentPlan.moves.length > 0 ? "" : "none";
});

document.getElementById("apply-plan").addEventListener("click", async () => {
  if (!currentPlan || currentPlan.moves.length === 0) return;
  const result = await api("/audit/resolve/apply", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(currentPlan.moves),
  });
  document.getElementById("resolve-results").innerHTML += `<div class="issue ok">Applied ${result.applied} reassignment(s).</div>`;
  document.getElementById("apply-plan").style.display = "none";
  currentPlan = null;
  loadCalendar();
  loadDashboard();
});

initCalendarControls();
loadReference().then(loadCalendar).then(loadDashboard);
