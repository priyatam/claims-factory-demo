
const MAX = 3 * 1024 * 1024;
const ERROR = __ERROR__;
const $ = (id) => document.getElementById(id);
const text = (value) => (value === null || value === undefined || value === "" ? "n/a" : String(value));

function add(list, label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  list.append(term, detail);
}

function showClaim(claim) {
  const list = $("record-list");
  const vehicle = claim.vehicle || {};
  const plate = claim.plate || {};
  const damage = claim.damage || {};
  const estimate = claim.estimate || {};
  list.textContent = "";
  add(list, "Claim ID", text(claim.claim_id));
  add(list, "Status", text(claim.status));
  add(list, "Vehicle", [vehicle.colour, vehicle.make, vehicle.model].filter(Boolean).join(" ") || "n/a");
  add(list, "Plate", text(plate.value));
  add(list, "Damage", text(damage.summary));
  add(list, "Parts", (damage.parts || []).join(", ") || "n/a");
  add(list, "Severity", text(damage.severity));
  add(list, "Estimate", estimate.low == null ? "none" : estimate.low + " to " + estimate.high + " " + (estimate.currency || "USD"));
  add(list, "Confidence", text(estimate.confidence));
  add(list, "Assumptions", (estimate.assumptions || []).join("; ") || "n/a");
  for (const [name, fact] of Object.entries(claim.partner_facts || {})) {
    const label = name.replace(/_/g, " ");
    add(list, label.charAt(0).toUpperCase() + label.slice(1), fact === null ? "n/a" : JSON.stringify(fact));
  }
  $("record").hidden = false;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error("rejected: HTTP " + response.status);
  return response.json();
}

$("claim").onsubmit = async (event) => {
  event.preventDefault();
  const file = $("file").files[0];
  const status = $("status");
  $("record").hidden = true;
  if (!file || file.size > MAX) { console.warn("no photo, or over 3 MB"); status.textContent = ERROR; return; }
  $("go").disabled = true;
  status.textContent = "Assessing...";
  const policy = $("policy").value;
  console.info("policy code length:", policy.length);
  if (!policy) console.warn("no policy code found in the textbox");
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const claim = await post("/", {policy, image_b64: btoa(binary)});
    status.textContent = "";
    showClaim(claim);
    $("go").disabled = false;
    return;
  } catch (error) {
    console.error(error);
    status.textContent = ERROR;
  }
  $("go").disabled = false;
};
