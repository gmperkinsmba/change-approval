# Security

Change Approval binds each approval to a plan version and SHA-256 content fingerprint. The local document adapter checks the expected revision and prior text before a change. It then reads the result back. The server checks Host. It checks Origin when present. It needs a token for state-changing requests. It limits request size. Hashes show if reviewed text changed. They do not encrypt text or name the approver. This app uses a local demo adapter. It does not use an external document service.

Local state is plaintext protected by account permissions. Use your Mac’s disk encryption to protect saved files. The native app has an ad-hoc code signature. This lets the installer check whether its files changed. It does not prove who made the app. The build is not notarized.

Report security issues through [GitHub private vulnerability reporting](https://github.com/gmperkinsmba/change-approval/security/advisories/new).
