# Acceptance

No community beta has completed hardware acceptance. `acceptance.json` deliberately contains no accepted releases. Local tests, a screenshot, a successful copy or a healthy process cannot stand in for observations on the tablet and grinder.

## Software checks

Run from a clean allowlisted checkout with the documented dependencies:

```sh
python ops/audit_source.py
python -m pytest -q bridge/tests
python ops/deployment/deploy_smoke.py bridge
python ops/release.py --version VERSION --output /outside/source/release
```

The suite includes real Tcl execution, real JavaScript host behavior, JavaScript-to-Python delivery, duplicate/reordered events, disconnect/restart reconciliation, expired/replaced cycles, uncertain delivery, setup, recovery and package reproducibility. Tests and demos use fake devices only. The CI matrix installs the packaged runtime on fresh AMD64 and ARM64 Linux runners, verifies nonroot startup, the port-binding-only capability, disabled feedback, process-restart identity, backup restoration, the actual update idle gate, and image upgrade/rollback with current data in an unconfigured test volume. A real host reboot and hardware-active updates still require acceptance. Read the actual CI results before claiming either architecture passed.

## Hardware record

For each Classic and Decaid combination, record the exact package SHA-256 values, app version, tablet model/OS, grinder firmware, date and observer. Keep raw journals, photographs, logs and installation details privately. A sanitized public record can identify the checked versions and hash the private evidence file.

| Check | Required observation |
| --- | --- |
| Late press | Press before, during and after extraction; one original native pending UUID, one receipt, no extraction start from the plugin |
| Invalid shot recovery | Manual/invalid/incomplete extraction remains history-only; native wait clears correctly; the next valid grind works |
| Mid-shot scale loss | Classic's selected fallback behaves as labelled, without changing an actual measurement into an estimate silently; Decaid remains history-only; verify actual machine stopping behavior |
| Wi-Fi recovery | Disconnect/reconnect during capture and delivery; app remains usable, exact queued bytes survive and no new grind inherits an old result |
| Three consecutive cycles | Three distinct real native grinds, one press each, prompt clearance, receipt and reviewed native outcomes; record motion separately from target/tolerance |
| Tablet appearance | Full title, authentic logo, touch targets, Panels, themes and read-only Grind on the actual tablet |
| Readiness / banners | Connectivity alone is never green Ready; concise, non-overlapping notices agree with the server; normal Decent notifications remain intact |
| Plugin update | Matching release installed through Classic updater / Decaid native mechanism; running version verified after restart; settings and events retained |
| Reboot | Server/tablet restart retains identity, queued events, commissioned settings and history |
| Backup / restore | Empty recovery path, integrity and identity verified; private config/pins recovered; uncertain delivery reconciled |
| Upgrade / rollback | Both executables available; idle gate enforced; failed activation/rollback retains the current journal |
| Interrupted migration | Classic rename interruption resumes/rolls back without changed queue bytes or dual hooks; Decaid interrupted plugin replacement retains host storage and rejects replayed live snapshots |

Use `tools/commissioning.py --url SERVER --output PRIVATE_FILE` for a read-only snapshot. Its optional observation flags annotate an exact real shot; they do not create hardware evidence by themselves. `ops/validate_receipt.py` checks existing journal facts and keeps observer assertions explicit. Never submit fabricated measurements to the real grinder to satisfy a test.

## Release gate

The release workflow first requires both native-architecture CI jobs. It then builds the exact tag and calls `ops/release_gate.py`. The gate rejects missing/partial observations, missing tested versions, unsupported combinations or artifact hashes that differ from the tested packages. A code or package change requires new corresponding acceptance evidence.

An acceptance entry has `version`, the exact release manifest `artifacts` mapping, and `controllers.classic` / `controllers.decaid`. Each controller record includes `app_version`, `tablet_model`, `tablet_os`, `grinder_model: "E64 WS"`, `grinder_firmware`, `observed_at`, `reviewer`, `private_evidence_sha256`, and every named boolean check in `ops/release_gate.py`. Add only observations that actually occurred. CI signs provenance and publishes a prerelease only after this gate passes.

The first release goes to a small volunteer group. Record issues and tested combinations before broadening the compatibility statement. Privileged helper installation and physical/tablet operations require owner coordination; routine local code work does not authorize those steps.
