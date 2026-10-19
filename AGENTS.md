# Mahlkönig - Decent sync

Read the current architecture, API contract and acceptance procedure before changing behavior. If a local `HANDOFF.md` exists, read it and verify dated state against current evidence. Keep local operational observations there, outside the public source allowlist. Historical evidence and credentials belong outside the source tree.

## Structure

- `bridge/`: flat Python/aiohttp modules and SQLite. `engine.py` owns delivery; `cycles.py` owns identity/read barriers; `controllers.py` owns lifecycle/profiles/display; `measurements.py` owns measurement qualification; `store.py` owns transactions.
- `bridge/static/`: plain dashboard HTML/CSS/JavaScript; no frontend build pipeline.
- `tablet/mahlkonig_decent_sync/`: Classic loader, capture, transport, display/settings and update modules.
- `tablet/decaid/`: Decaid v0.9.0 fields, durable outbox, live capture and host integration.
- `bridge/tests/`: Python tests, actual Tcl/JavaScript, shared fake-device fixtures and loopback demos.
- `ops/release.py` / `release-spec.json`: one canonical release model and explicit runtime allowlists.
- `ops/deployment/`: existing managed authority and portable smoke. `ops/container/`: new Linux installation/update context. `ops/migrations/`: bounded one-time Classic rename, excluded from runtime.
- `docs/`: authoritative architecture, installation, API, compatibility, recovery, acceptance and audit records.

## Development

Use Python 3.13+, Node.js 22+, Tcl 8.5+ with `json`, `sha256` and `http`. Create a virtual environment outside the checkout and install `bridge/requirements.txt` and `pytest==9.1.1`.

```sh
python ops/audit_source.py
python -m pytest -q bridge/tests
python ops/deployment/deploy_smoke.py bridge
python bridge/tests/demo_automation.py
python ops/release.py --version EXPLICIT_VERSION --output /outside/checkout/release
```

The demo is fake-device loopback only. Builds require an explicit version/new external destination and never modify source. Tests must execute; skips are not passes. No formatter/linter pass is currently configured. Follow adjacent four-space Python/Tcl style and plain JavaScript; keep functions small and state explicit.

Update `source-allowlist.json` and audit mappings for new or removed files. No old/legacy/attic source directories, copied runtime implementations, private installers, stale release defaults or generated bundles belong in the active product tree. Keep one authoritative implementation per supported responsibility. Compatibility code must identify a current consumer and regression; migration code must have a retirement condition.

## Invariants

- Preserve `DE1-SYNTHETIC-BRIDGE`, Xenia identity, cached two-second polling, `/api/v2/`, manual input and controller contracts 1/2. Never wait for network I/O in a poll handler. Bound native export/processing work and use native read barriers.
- Commit receipt, correlation and intent with `Store.atomic()`, preserving nested savepoints, uniqueness and exact retry payloads. ACK means durable storage, not grinder receipt.
- Freeze cycle/native UUID, session, effective profile and recipe at shot start. Never infer a new match for an old shot, replay uncertain delivery or use two transports for a cycle.
- One controller owns one grinder. Switching requires idle state and recommissioning. Queues remain pinned to the original server UUID.
- A grinder press arms feedback in the original pending window; it never starts extraction or extends expiry. Neither plugin starts extraction. Purges/replayed announcements never wake the machine.
- Decent effective runtime targets own yield; grinder owns dose. Commission programs and verify native targets. Do not infer a recipe from `recipe_active` alone or replace live overrides with saved defaults.
- Keep finalized grams, pump-stop mass, pour volume, estimated grams and Decaid water volume distinct. Duration excludes preinfusion and ends at extraction stop. Classic's selected automatic mode retains labelled mid-shot fallback; Decaid initially requires final scale weight.
- Receipt, target change, movement and tolerance are different facts. Missing movement does not imply purge requirements or firmware rounding. Use valid sensor/idle evidence for tolerance.
- Imported/edited history and reconnect snapshots cannot initiate eligible Decaid captures. Classic networking stays asynchronous; downloaded shot/settings files are data, never evaluated code. Outboxes and settings survive updates.
- Use the shared readiness model; connectivity alone is not Ready. Keep Panels, themes, concise non-overlapping banners, read-only Grind and unchanged native Decent notifications. Product labels are compact and functional: no promotional prose, rhetorical headings or redundant instructions. Put explanations in setup/About/docs.
- Keep the trusted-LAN dashboard password-free with host/origin/subnet/grinder-peer guards and native certificate pins. Direct-IP success plus hostname failure is DNS evidence.

## Validation and deployment

Run focused regressions for changes, then the full Python/actual Tcl/JavaScript suite and portable smoke before releases. Verify clean source/package allowlists and native AMD64/ARM64 installations. Never submit fabricated results to real hardware.

Simulation cannot prove prompt clearance, physical movement, wake behavior, real scale-loss stopping or tablet appearance. Coordinate physical tests and tablet copying with the owner, and record exactly observed versions/results. Follow `docs/acceptance.md`; do not mark physical acceptance complete from software tests.

For an existing managed installation, use only its installed restricted updater with idle/backup/health checks. Never bypass it with retired installers or restore an older journal during code rollback. Existing owner authorization for routine application updates remains effective within its stated scope. Changes to privileged helpers/bootstrap, access rules, pairing/cloud accounts or production recipes beyond the agreed workflow require specific owner authorization. Prepare concrete reviewed code before asking.

Do not publish operational credentials, private keys, firmware, research copies or production journals. Preserve host-key verification and existing security boundaries. Firmware modification, access-control bypasses and impersonated device credentials are outside scope. Tablet hosting, other grinders, simultaneous controllers and Home Assistant remain outside this release.
