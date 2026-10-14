import { initFastPasskey } from "/api/v1/auth/assets/fastpasskey.js";
const enrollment = document.querySelector("[data-passkey-add-link]");
if (enrollment) {
  enrollment.dataset.passkeyAddToken = location.hash.slice(1);
  history.replaceState(null, "", location.pathname);
}
initFastPasskey();
const $ = (selector) => document.querySelector(selector);
const csrf = $('meta[name="csrf-token"]').content;
let state = {
  instances: [],
  vehicles: [],
  chargers: [],
  sessions: [],
  reports: [],
};
const escape = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (v) =>
  Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 3 });
const date = (v) => (v ? new Date(v).toLocaleString() : "Charging now");
const notice = (text, error = false) => {
  if ($("#notice")) {
    $("#notice").textContent = text;
    $("#notice").className = error ? "error" : "success";
  }
};
async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
function options(items, selected = "", empty = null) {
  return (
    (empty !== null ? `<option value="">${escape(empty)}</option>` : "") +
    items
      .map(
        (i) =>
          `<option value="${escape(i.id)}" ${i.id === selected ? "selected" : ""}>${escape(i.name)}</option>`,
      )
      .join("")
  );
}
function formData(form) {
  return Object.fromEntries(new FormData(form));
}
function bindForm(id, handler) {
  $(id)?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector(
      'button[type="submit"],button:not([type])',
    );
    if (button) button.disabled = true;
    try {
      await handler(formData(event.target), event.target);
      await load();
    } catch (error) {
      notice(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  });
}
function render() {
  const total = state.sessions.reduce(
    (n, s) => n + Number(s.payload.energy_kwh),
    0,
  );
  const attention = state.sessions.filter(
    (s) =>
      !["confirmed", "default"].includes(s.assignment) ||
      s.payload.flags.length,
  );
  $("#stats").innerHTML = [
    ["Retained charging energy", number(total) + " kWh"],
    ["Charging cycles", state.sessions.length],
    ["Need review", attention.length],
  ]
    .map(
      ([label, value]) =>
        `<div class="stat"><span>${escape(label)}</span><strong>${escape(value)}</strong></div>`,
    )
    .join("");
  $("#homes-summary").innerHTML =
    state.instances
      .map(
        (i) =>
          `<div class="item"><strong>${escape(i.name)}</strong><span class="muted">${escape(i.url)} · ${i.last_sync ? "Last read " + escape(date(i.last_sync)) : "Awaiting charger mapping"}</span>${i.error ? `<p class="error">${escape(i.error)}</p>` : ""}</div>`,
      )
      .join("") ||
    '<p class="empty">Connect your first Home Assistant in Connections & cars.</p>';
  $("#attention").innerHTML =
    attention
      .slice(0, 5)
      .map(
        (s) =>
          `<div class="item"><strong>${escape(date(s.start))}</strong><span class="muted">${escape(s.assignment)}${s.payload.flags.length ? " · " + escape(s.payload.flags.join(", ")) : ""}</span></div>`,
      )
      .join("") || '<p class="empty">No charging sessions need review.</p>';
  $("#cars-summary").innerHTML =
    state.vehicles
      .map(
        (v) =>
          `<div><span class="badge">${escape(v.provider)}</span><h3>${escape(v.name)}</h3><p>${escape(v.plate || "Add license plate")} · ${v.policy === "employer" ? "Company reimbursement" : "Actual energy costs"}</p></div>`,
      )
      .join("") ||
    '<p class="empty">Cars appear after discovery. Each VIN stays a separate vehicle.</p>';
  document.querySelectorAll(".vehicle-options").forEach((el) => {
    const v = el.value;
    el.innerHTML = options(state.vehicles, v, el.dataset.empty ?? null);
  });
  document.querySelectorAll(".instance-options").forEach((el) => {
    const v = el.value;
    el.innerHTML = options(state.instances, v);
  });
  const selected = $("#session-filter").value;
  $("#session-filter").innerHTML = options(
    state.vehicles,
    selected,
    "All cars",
  );
  $("#session-rows").innerHTML =
    state.sessions
      .filter((s) => !selected || s.vehicle_id === selected)
      .map((s) => {
        const car = state.vehicles.find((v) => v.id === s.vehicle_id),
          charger = state.chargers.find((c) => c.id === s.charger_id),
          p = s.payload;
        const issue =
          s.assignment === "unassigned"
            ? "Car mapping required"
            : s.assignment === "suggested"
              ? "Confirm car match"
              : p.flags.length
                ? p.flags.join(", ")
                : s.end
                  ? "Complete"
                  : "In progress";
        return `<tr><td>${escape(date(s.start))}<small>${escape(date(s.end))}</small></td><td>${escape(charger?.name)}</td><td>${escape(car?.name || "Unassigned")}<small>${escape(s.assignment)}</small></td><td>${number(p.energy_kwh)} kWh</td><td>${number(p.grid_kwh)} / ${number(p.solar_kwh)}<small>${Number(p.unknown_kwh) > 0 ? number(p.unknown_kwh) + " kWh unknown" : ""}</small></td><td>${escape(issue)}</td><td><form data-assignment="${escape(s.id)}"><select name="vehicle_id" required>${options(state.vehicles, s.vehicle_id, "Choose car")}</select><button>Confirm</button></form></td></tr>`;
      })
      .join("") ||
    '<tr><td colspan="7">No charging sessions yet. Map a charger, then sync its Home Assistant.</td></tr>';
  $("#connections").innerHTML =
    state.instances
      .map(
        (i) =>
          `<div class="item"><strong>${escape(i.name)}</strong><small>${escape(i.url)}</small><div><button data-discover="${escape(i.id)}">Discover & map</button><button data-sync="${escape(i.id)}" class="secondary">Sync history</button><button data-toggle="${escape(i.id)}" class="secondary">${i.enabled ? "Pause" : "Resume"}</button></div>${i.error ? `<p class="error">${escape(i.error)}</p>` : ""}<details><summary>Replace access token</summary><form data-credentials="${escape(i.id)}"><label>New token<input name="token" type="password" autocomplete="off" required></label><button>Verify & save token</button></form></details></div>`,
      )
      .join("") || '<p class="empty">No connections yet.</p>';
  $("#vehicle-editors").innerHTML =
    state.vehicles
      .map(
        (v) =>
          `<form class="vehicle-form" data-vehicle="${escape(v.id)}"><label>Name<input name="name" value="${escape(v.name)}" required><small>VIN …${escape(v.vin.slice(-6))}</small></label><label>License plate<input name="plate" value="${escape(v.plate)}" required></label><label>Report policy<select name="policy"><option value="actual" ${v.policy === "actual" ? "selected" : ""}>Actual costs + solar</option><option value="employer" ${v.policy === "employer" ? "selected" : ""}>Company reimbursement</option></select></label><button>Save car</button></form>`,
      )
      .join("") ||
    '<p class="empty">Connect Home Assistant to discover Leapmotor vehicles.</p>';
  $("#charger-list").innerHTML =
    state.chargers
      .map(
        (c) =>
          `<div class="item"><strong>${escape(c.name)}</strong><span class="muted">${escape(c.energy_entity)} · ${escape(c.power_entity)}</span><small>Charging state: ${escape(c.state_entity)} · active: ${escape(c.active_values.join(", "))}</small><small>Session counter: ${escape(c.session_energy_entity || "Not mapped")}</small><button data-edit-charger="${escape(c.id)}">Edit mapping</button></div>`,
      )
      .join("") ||
    '<p class="empty">Use Discover & map to select a charger.</p>';
  $("#report-list").innerHTML =
    state.reports
      .slice()
      .reverse()
      .map(
        (r) =>
          `<div class="item"><strong>${escape(r.snapshot.vehicle)} · ${escape(r.month)}</strong><span>${escape(r.snapshot.energy_kwh)} kWh · €${escape(r.snapshot.amount_eur)}</span><div>${["csv", "xlsx", "pdf"].map((f) => `<a class="download" href="/api/reports/${encodeURIComponent(r.id)}/${f}">${f.toUpperCase()}</a>`).join("")}</div></div>`,
      )
      .join("") ||
    '<p class="empty">Create a report to download all three formats from one fixed snapshot.</p>';
}
async function load() {
  state = await api("/dashboard");
  render();
}
async function discovery(id, charger = null) {
  notice("Reading Home Assistant entities…");
  const result = await api(`/instances/${id}/discover`, "POST", {});
  await load();
  $("#discovery-panel").hidden = false;
  $("#discovery-title").textContent =
    "Map " + state.instances.find((i) => i.id === id).name;
  $("#grid-form [name=instance_id]").value = id;
  const chargerForm = $("#charger-form");
  chargerForm.reset();
  chargerForm.dataset.chargerId = charger?.id || "";
  chargerForm.querySelector("h3").textContent = charger
    ? "Edit charger"
    : "Add charger";
  chargerForm.querySelector("button").textContent = charger
    ? "Save mapping & sync"
    : "Add charger";
  $("#charger-form [name=instance_id]").value = id;
  $("#charger-form [name=name]").value = charger?.name || "";
  $("#charger-form [name=active_values]").value =
    charger?.active_values.join(", ") || "charger_charging";
  $("#charger-form [name=default_vehicle_id]").value =
    charger?.default_vehicle_id || "";
  const powers = result.entities.filter((e) => ["W", "kW"].includes(e.unit));
  const energies = result.entities.filter(
    (e) =>
      ["Wh", "kWh"].includes(e.unit) && e.state_class === "total_increasing",
  );
  const entities = result.entities.map((e) => ({
    ...e,
    name: e.name + " · " + e.id,
  }));
  const labelled = (items) =>
    items.map((e) => ({ ...e, name: e.name + " · " + e.id }));
  const site = state.instances.find((i) => i.id === id);
  $("#grid-options").innerHTML = options(
    labelled(powers),
    site.grid_entity,
    "Not mapped — solar allocation unknown",
  );
  $("#grid-form [name=grid_inverted]").checked = site.grid_inverted;
  $("#energy-options").innerHTML = options(
    labelled(energies),
    charger?.energy_entity,
    "Choose energy meter",
  );
  $("#session-energy-options").innerHTML = options(
    labelled(result.entities.filter((e) => ["Wh", "kWh"].includes(e.unit))),
    charger?.session_energy_entity || "",
    "Not available",
  );
  $("#power-options").innerHTML = options(
    labelled(powers),
    charger?.power_entity,
    "Choose charging power",
  );
  $("#state-options").innerHTML = options(
    entities,
    charger?.state_entity,
    "Choose charging state",
  );
  notice(
    `Found ${result.vehicles.length} Leapmotor vehicle(s). Select charger and grid sensors below.`,
  );
  $("#discovery-panel").scrollIntoView({ behavior: "smooth" });
}
if ($("#overview")) {
  document.querySelectorAll("[data-tab]").forEach((button) =>
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".tab")
        .forEach(
          (section) => (section.hidden = section.id !== button.dataset.tab),
        );
      document
        .querySelectorAll("[data-tab]")
        .forEach((b) => b.classList.toggle("active", b === button));
    }),
  );
  $("#session-filter").addEventListener("change", render);
  $("#refresh").addEventListener("click", () =>
    load().catch((e) => notice(e.message, true)),
  );
  bindForm("#connection-form", async (data, form) => {
    const site = await api("/instances", "POST", data);
    form.reset();
    await discovery(site.id);
  });
  bindForm("#grid-form", async (data) => {
    await api(`/instances/${data.instance_id}`, "PATCH", {
      grid_entity: data.grid_entity || null,
      grid_inverted: data.grid_inverted === "on",
      enabled: state.instances.find((i) => i.id === data.instance_id).enabled,
    });
    notice("Grid mapping saved.");
  });
  bindForm("#charger-form", async (data, form) => {
    data.active_values = data.active_values
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    data.default_vehicle_id ||= null;
    data.session_energy_entity ||= null;
    const id = form.dataset.chargerId;
    await api(
      id ? `/chargers/${id}` : "/chargers",
      id ? "PATCH" : "POST",
      data,
    );
    notice("Charger mapping saved. Reading history…");
    await api(`/instances/${data.instance_id}/sync`, "POST", {});
    notice("History sync finished.");
  });
  for (const id of ["#tariff-form", "#reimbursement-form"])
    bindForm(id, async (data) => {
      data.effective_from = new Date(data.effective_from).toISOString();
      await api("/tariffs", "POST", data);
      notice("Rate saved. Existing report snapshots stay unchanged.");
    });
  bindForm("#report-form", async (data) => {
    await api("/reports", "POST", data);
    notice("Report ready. Download CSV, Excel or PDF.");
  });
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (
      !form.dataset.vehicle &&
      !form.dataset.assignment &&
      !form.dataset.credentials
    )
      return;
    event.preventDefault();
    try {
      if (form.dataset.credentials) {
        const site = state.instances.find(
          (i) => i.id === form.dataset.credentials,
        );
        await api(`/instances/${site.id}/credentials`, "POST", {
          name: site.name,
          url: site.url,
          token: formData(form).token,
        });
        form.reset();
        await load();
        notice("Token replaced.");
        return;
      }
      await api(
        form.dataset.vehicle
          ? `/vehicles/${form.dataset.vehicle}`
          : `/sessions/${form.dataset.assignment}/assignment`,
        "PATCH",
        formData(form),
      );
      await load();
      notice("Saved.");
    } catch (e) {
      notice(e.message, true);
    }
  });
  document.addEventListener("click", async (event) => {
    const b = event.target.closest("button");
    if (!b) return;
    try {
      if (b.dataset.editCharger) {
        const charger = state.chargers.find(
          (c) => c.id === b.dataset.editCharger,
        );
        await discovery(charger.instance_id, charger);
      }
      if (b.dataset.discover) await discovery(b.dataset.discover);
      if (b.dataset.sync) {
        b.disabled = true;
        notice("Reading retained history…");
        await api(`/instances/${b.dataset.sync}/sync`, "POST", {});
        await load();
        notice("Sync finished.");
      }
      if (b.dataset.toggle) {
        const i = state.instances.find((i) => i.id === b.dataset.toggle);
        await api(`/instances/${i.id}`, "PATCH", {
          grid_entity: i.grid_entity,
          grid_inverted: i.grid_inverted,
          enabled: !i.enabled,
        });
        await load();
      }
    } catch (e) {
      notice(e.message, true);
    } finally {
      b.disabled = false;
    }
  });
  $("#report-form [name=month]").value = new Date().toISOString().slice(0, 7);
  await load().catch((e) => notice(e.message, true));
  setInterval(() => {
    if (
      !document.hidden &&
      !["INPUT", "SELECT"].includes(document.activeElement?.tagName)
    )
      load().catch((e) => notice(e.message, true));
  }, 30000);
}

$("#logout")?.addEventListener("click", async () => {
  await fetch("/api/v1/auth/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  location.reload();
});

async function loadAccounts() {
  if (!$("#account-list")) return;
  const users = await api("/admin/users");
  $("#account-list").innerHTML = users
    .map(
      (u) =>
        `<form class="card" data-account="${escape(u.id)}"><h2>${escape(u.display_name)}</h2><p>${escape(u.email)} · ${u.passkeys} passkeys</p>${["is_approved", "is_active", "is_admin"].map((k) => `<label class="check"><input type="checkbox" name="${k}" ${u[k] ? "checked" : ""}>${{ is_approved: "Household access approved", is_active: "Active", is_admin: "Administrator" }[k]}</label>`).join("")}<button>Save access</button> <button type="button" data-invite="${escape(u.id)}">Generate invite</button> <button type="button" data-revoke="${escape(u.id)}">Revoke invite</button></form>`,
    )
    .join("");
}
function showInvite(link) {
  $("#invite-result").hidden = false;
  $("#invite-url").value = link.url;
}
if ($("#admin")) {
  $("#account-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const data = formData(e.target);
      data.is_admin = data.is_admin === "on";
      showInvite(await api("/admin/users", "POST", data));
      e.target.reset();
      await loadAccounts();
    } catch (e) {
      notice(e.message, true);
    }
  });
  $("#account-list").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const data = Object.fromEntries(
        ["is_approved", "is_active", "is_admin"].map((k) => [
          k,
          e.target.elements[k].checked,
        ]),
      );
      await api(`/admin/users/${e.target.dataset.account}`, "PATCH", data);
      await loadAccounts();
      notice("Account access updated.");
    } catch (e) {
      notice(e.message, true);
    }
  });
  $("#account-list").addEventListener("click", async (e) => {
    const invite = e.target.dataset.invite,
      revoke = e.target.dataset.revoke;
    try {
      if (invite)
        showInvite(await api(`/admin/users/${invite}/invite`, "POST", {}));
      if (revoke) {
        await api(`/admin/users/${revoke}/invite`, "DELETE");
        $("#invite-result").hidden = true;
        notice("Invitation revoked.");
      }
    } catch (e) {
      notice(e.message, true);
    }
  });
  $("#copy-invite").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#invite-url").value);
      notice("Link copied.");
    } catch {
      $("#invite-url").select();
      notice("Select and copy the link.");
    }
  });
  await loadAccounts().catch((e) => notice(e.message, true));
}
