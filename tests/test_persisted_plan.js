const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync(new URL("../web/app.js", `file://${__filename}`), "utf8");
const start = source.indexOf("function normalizePlan(");
const end = source.indexOf("\nfunction bindBusy", start);
assert(start >= 0 && end > start, "normalizePlan must be present before busy handlers");
const normalizePlan = new Function(`${source.slice(start, end)}; return normalizePlan;`)();

const persisted = {
  plan_id: "plan-reopened",
  current_version: 7,
  target_id: "DOC-001",
  fingerprint: "persisted-fingerprint",
  payload: "Saved plan content",
};
const reopened = normalizePlan(persisted);
assert.equal(reopened.version, 7, "the backend current_version must become the UI version");
assert.equal(reopened.current_version, 7, "normalization must preserve the backend field");
assert.match(source, /currentPlan=normalizePlan\(appState\.plans\[0\]\)/, "refresh must normalize a reloaded plan");

const approvals = [{plan_id: "plan-reopened", version: 7, fingerprint: "persisted-fingerprint", approval_id: "approval-7"}];
const approval = approvals.find(a => a.plan_id === reopened.plan_id && a.version === reopened.version && a.fingerprint === reopened.fingerprint);
assert.equal(approval.approval_id, "approval-7", "a persisted approval must match the normalized plan version");
assert.match(source, /a\.version===currentPlan\.version && a\.fingerprint===currentPlan\.fingerprint/, "reload must match approvals against normalized version and fingerprint");

const revisionRequest = {plan_id: reopened.plan_id, version: reopened.version, target_id: reopened.target_id, payload: `${reopened.payload} revised`};
assert.equal(revisionRequest.version, 7, "revising after reload must send the persisted version");
assert.match(source, /version:currentPlan\.version,target_id:targetId,payload/, "revise must send the normalized currentPlan.version");
process.stdout.write("persisted plan reload preserves approval and revise version\n");
