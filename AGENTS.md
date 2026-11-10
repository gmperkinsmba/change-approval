# Change Approval

Prepares a versioned plan for a made-up local document; a person reviews and approves each change, and the engine rechecks plan and approval before a local SQLite adapter applies it.

## Run

```sh
python3 run.py
```

The Mac app is built with `build_desktop.py` and installed with `install_desktop.py`; the README gives the exact steps and the pinned requirements file.

## Test

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

Run the tests before committing. There is no separate lint step.

## Layout

- Core code: `rba/`, `run.py`
- Browser UI: `web/`
- Mac app wrapper, build and install: `build_desktop.py`, `desktop-app-owner.txt`, `desktop-assets.txt`, `desktop.py`, `desktop_policy.py`, `install_desktop.py`
- Installer and runtime safety checks: `install_security.py`
- Dependencies: `requirements-desktop.lock.txt`
- Tests: `tests/`
- Docs: `CONTEXT.md`, `LICENSE`, `README.md`, `SECURITY.md`, `third_party_licenses/`

## Conventions

- `README.md` describes behaviour and limits; `CONTEXT.md` holds the domain terms and rules. Keep both in step with code changes.
- Read `SECURITY.md` before changing request handling, file access, or the installer.
- Demo data, fixtures and examples are made up. Keep them that way.
- Never commit secrets, keys, tokens, or runtime data.
- A behaviour change comes with a test.
