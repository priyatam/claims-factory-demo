
const MAX = 3 * 1024 * 1024;
const ERROR = __ERROR__;
const out = document.getElementById("out");
const go = document.getElementById("go");

function show(claim) {
  const lines = ["Status: " + claim.status];
  const damage = claim.damage;
  if (damage && damage.summary) lines.push("Damage: " + damage.summary + (damage.severity ? " (" + damage.severity + ")" : ""));
  const estimate = claim.estimate;
  if (estimate && estimate.low != null) {
    lines.push("Estimate: " + estimate.low + " to " + estimate.high + " " + (estimate.currency || "") + ", confidence " + estimate.confidence);
  } else {
    lines.push("No estimate: this photo could not be priced.");
  }
  return lines.join("\n");
}

document.getElementById("claim").onsubmit = async (event) => {
  event.preventDefault();
  const file = document.getElementById("file").files[0];
  if (!file || file.size > MAX) { out.textContent = ERROR; return; }
  go.disabled = true;
  out.textContent = "Assessing...";
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const response = await fetch("/", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({policy: document.getElementById("policy").value, image_b64: btoa(binary)}),
    });
    if (!response.ok) throw new Error("rejected");
    out.textContent = show(await response.json());
  } catch (_) {
    out.textContent = ERROR;
  }
  go.disabled = false;
};
