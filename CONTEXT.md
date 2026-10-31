# Domain and invariants

## Plan version
A plan version records one exact target, expected destination revision, current content, proposed content, fingerprint, and creation time. Revisions are immutable. A changed payload or target creates a new version.

## Approval receipt
An approval receipt binds an operator decision to one plan ID, version, and fingerprint. It has a fixed expiry. The engine rejects it when the plan version changes, its content no longer matches, or its expiry passes.

## Fake destination
The local adapter stores two fabricated documents. It applies an update only when both the target revision and prior content match the approved plan. It does not accept network destinations or arbitrary queries.

## Action receipt
An action receipt records a rejection, an adapter success without readback, or a verified result. A later readback appends a new receipt linked to the earlier one. Approval and action receipts cannot be changed or deleted through the database schema.

## Local policy
Prepare, approve, execute, and verify permissions are checked inside the engine. The settings screen changes the local demo policy; it does not authenticate users or establish identity.
