const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync(new URL("../web/app.js", `file://${__filename}`), "utf8");
const start = source.indexOf("function bindBusy(");
const end = source.indexOf("\n}\n\nfunction renderPlan", start) + 2;
assert(start >= 0 && end > start, "bindBusy function must be present");

const attributes = new Map();
const button = {
  disabled: false,
  innerHTML: `Approve version <span id="approveVersion">—</span>`,
  addEventListener(type, listener) { assert.equal(type, "click"); this.listener = listener; },
  getAttribute(name) { return attributes.has(name) ? attributes.get(name) : null; },
  setAttribute(name, value) { attributes.set(name, value); },
  removeAttribute(name) { attributes.delete(name); },
};
const get = (id) => { assert.equal(id, "approve"); return button; };
const bindBusy = new Function("$", "renderPlan", `${source.slice(start, end)}; return bindBusy;`)(get, () => {
  assert.match(button.innerHTML, /id="approveVersion"/, "renderPlan must retain the nested version span");
});
bindBusy("approve", "Approving", async () => {
  assert.equal(button.disabled, true);
  assert.equal(button.getAttribute("aria-busy"), "true");
  assert.match(button.innerHTML, /id="approveVersion"/, "busy state must preserve child controls");
});

button.listener().then(() => {
  assert.equal(button.disabled, false);
  assert.equal(button.getAttribute("aria-busy"), null);
  assert.equal(button.getAttribute("aria-label"), null);
  assert.match(button.innerHTML, /id="approveVersion"/);
  process.stdout.write("bindBusy preserves nested controls and restores state\n");
}).catch((error) => { console.error(error); process.exitCode = 1; });
