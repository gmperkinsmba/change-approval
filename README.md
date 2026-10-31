# Change Approval

**First principle:** A person must review and approve each change before it applies.

This app prepares a versioned plan for a made-up local document. Review the target and its current and new text. Check the expected revision, fingerprint, and approval expiry. The engine checks the plan and approval again before it acts. A local SQLite adapter applies the change. It reads the document back before it records a verified result.

This build shows how to approve local document updates. It sends no email, calls no remote service, and does not connect to an agent framework. The market need is still a hypothesis.

**Install as a Mac app**

Build the native app on macOS with Python 3.12. The pinned build packages are listed in `requirements-desktop.lock.txt`.

```sh
python3.12 -m venv .desktop-venv
.desktop-venv/bin/python -m pip install -r requirements-desktop.lock.txt
.desktop-venv/bin/python build_desktop.py
.desktop-venv/bin/python install_desktop.py
```

The install step puts `Change Approval.app` in `~/Applications`. Open it in Finder. Close its window or choose **Quit** to stop the local server. The app bundle includes Python. Runtime data stays in `~/.local/share/review-before-action`; set `RBA_RUNTIME` before launching from a shell to use another folder. Moving the app to Trash leaves runtime data in place.

The bundle uses ad-hoc signing. It has no Developer ID signature and is not notarized. macOS may ask you to approve opening it. The native app builds on macOS only.

To work on the native shell, activate `.desktop-venv` and run `.desktop-venv/bin/python desktop.py`.

## Run in a browser

Use Python 3.11 or newer. The app uses only the Python standard library.

```sh
python3 run.py
```

Open <http://127.0.0.1:8335>. Runtime data goes in `~/.local/share/review-before-action`, outside the source folder. Use `--runtime` or `RBA_RUNTIME` to choose another private folder. Use a new, empty folder for a fresh set of made-up documents.

## Use

1. Choose one of the two made-up documents and prepare the text.
2. Review the target and its current and new text. Check the revision and full plan fingerprint.
3. Check the review box and approve that version. The approval expires at the time shown.
4. Apply the approved plan. The adapter updates the local document, then reads it back.

To see how stale approvals are rejected, approve a plan. Select **Change payload after approval**, then **Try stale approval**. The engine records why it rejected the plan: its fingerprint and version changed. Review and approve the new version before you apply it.

Select **Skip readback** to see a tool result without a destination check. Without a readback, the receipt stays unverified. Choose **Verify destination**. The adapter checks the text and revision.

Settings sets approval time and four permissions. Those permissions are prepare, approve, execute, and verify. The engine checks each one before it acts. A verified action needs execute and verify permission. One process lock orders plan changes, approvals, and actions. This demo has one local operator. Settings do not identify users or manage accounts.

## Engine API

`rba.ReviewEngine(root, clock=time.time)` creates the plan store and fake destination outside the source folder. `prepare(target_id, payload)` creates version 1. `revise(plan_id, expected_version, target_id, payload)` adds a new immutable version. `approve(plan_id, version, fingerprint)` creates an immutable approval receipt with an expiry. `execute(approval_id, readback=True)` checks permission, version, fingerprint, expiry, and destination revision before applying. `verify(receipt_id)` reads the destination again and appends a separate receipt. `update_settings(values)` changes the local policy flags and approval lifetime.

The only destination IDs are `DOC-001` and `DOC-002`. Text is capped at 64 KB. Approval lifetime is limited to 30–3,600 seconds. Plans, approvals, and action receipts use string IDs and fingerprints. SQLite rejects updates and deletes to plan versions and receipts.

## Test

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

Tests check exact plan approval and changes to text or target. They check destination changes both before and after approval. They check expired approvals, denied actions, and revocation races. Receipt tests block updates and deletes. Readback tests check for mismatches. All test documents are made up.

## Limits and safety

The adapter changes only the local fake document database. It rejects URLs and SQL. It also rejects credentials and files sent as attachments. It rejects email recipients and outside destinations. The server listens only on loopback. It checks Host and Origin. It needs a per-process request token for changes and caps JSON requests at 100 KB. The fake adapter lives in a private runtime folder. Approval records live there too.

## License

Original code is available under the MIT License. There are no third-party runtime dependencies.
