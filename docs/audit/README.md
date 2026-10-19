# Consolidation audit

Baseline: 19 October 2026. The original tree contained 882 non-cache files. A private, byte-verified snapshot retains all 882. The file ledger assigns every original item a disposition: 50 retain, 29 consolidate, 5 remove, 4 replace, 1 migrate, and 793 preserve privately. Every original item now has a resolved disposition and current owner or private preservation reason. The final counts in the private ledger supersede this initial classification. Hardware acceptance is a separate release gate.

The public first-party ledger is `baseline-files.json`; `baseline-tests.json` maps 97 original Python test functions to their behavior and intended disposition. Parametrization produced 316 passing baseline tests, with actual Tcl execution and no skips. The portable deployment smoke check also passed. Private inventory, operational status, historical manifests and the original instructions remain outside the public tree. No credentials, live journals, firmware, downloaded implementations or rendered design previews belong in the release allowlist.

## Entry points and dynamic consumers

- `bridge/app.py` starts aiohttp, Store, Engine and one Controllers instance. The application lifecycle owns background reads, controller polling and the local deployment socket.
- HTTP manual results and controller events share Engine delivery. `/api/v2/` is reserved for grinder Xenia polling. Native HTTP/driver traffic is confined to E64 and its read-only command allowlist.
- `cycles.py` imports controller normalization helpers inside operations to avoid a module initialization cycle. It persists issued offers and read barriers; a static unused-code report cannot remove those methods.
- Classic's `plugin.tcl` is sourced first for metadata by de1app, then `main` installs synchronous lifecycle listeners, execution and variable traces, native stop wrappers, DUI page actions, deferred callbacks, and the asynchronous HTTP outbox. Metadata loading must remain inert.
- The managed systemd units invoke `app.py`, `backup.py`, and the independent root-owned deployment helper. The helper invokes the isolated smoke checker. It must never import application code as root.
- Python fixtures now live in `bridge/tests/support.py`. Tcl fixtures source the real extension and mock only the app/device boundary. Demos use those same fake devices and are excluded from runtime packages.

## Retired responsibilities and retained protection

| Retired owner | Disposition and current protection |
| --- | --- |
| `ops/manage.py` and its timed trial service | Remove: the trial-to-production cutover is complete. No supported workflow merges a retired trial journal into production. Managed deployment tests retain idle/race checks, backup, crash recovery and rollback without journal rewind. |
| `bridge/install.sh`, `tools/verify_installation.py` | Remove: original installation and release-0.4 verification have no current deployment consumer. New installation belongs to Compose; existing managed updates retain their restricted helper. |
| Two release builders | Replace with `ops/release.py`, explicit version/destination, explicit input allowlist, deterministic archives and no source mutation. |
| Completion-only transport | Remove executable branches. Measurement/finalization/fallback tests use one-press delivery. Rejection tests retain unsupported-mode coverage. Historical records remain readable. |
| Recipe writer protocols | Remove: no implementation or caller; target changes remain manual with native readback. |
| Household hostname repair | Remove: completed installation-specific repair. Portable configuration and installation identity must replace private addresses. |
| First privileged-bootstrap migration | Remove: existing installation is already managed and contract 2. Pre-contract-2 rollback is rejected. Manager source updates require a separate administrator-reviewed installation. |
| Historical runbooks, decompilation helpers, staging folders and previews | Preserve privately, outside the product tree. Relevant invariants are consolidated into current documentation. |

## Manifest comparison

Nineteen historical manifests were inventoried. The current installed release, `cycle-sync-0.5.4-20261019`, has 18 declared runtime/checker files and matches the baseline source. Earlier manifests differ in controller, cycle, movement-reporting and tablet behavior and are rollback/design evidence, not alternate source trees. Several early manifests included broad development contents, reinforcing the requirement for an explicit shipping allowlist. Full comparisons are private because historical paths identify the installation.

## Compatibility consumers

The Xenia machine serial and device identifier preserve grinder learning and native receipt matching. The `coffee-bridge` service, data paths, health service marker and deployment socket preserve the installed managed-service contract. Manual input and controller contracts 1 and 2 remain supported. Old scale-capture versions and historical pending-motion records remain readable with their original provenance. Existing reviewed daily-use authorization stays distinct from hardware acceptance. Every remaining compatibility branch must name one of these consumers or a bounded migration and have regression coverage.

## Final verification

`source-allowlist.json` declares every public file. `current-inventory.json` assigns each one an active responsibility; `current-tests.json` maps every Python regression entry to capture, measurements, correlation, delivery, persistence, UI, installation or recovery. Python wrappers run the actual Tcl fixtures and 14 Node.js Decaid cases. Both baseline ledgers have resolved dispositions and replacement locations. The full 882-item private ledger also resolves every preserved research, release and operational artifact.

`python ops/audit_source.py` checks exact inventory equality, resolved targets, test independence and obvious credential material. `--export` produces a new byte-verified source directory from that allowlist. Runtime packages use only `release-spec.json`; package regressions prove deterministic output, source immutability, exclusion of settings/private files and compatibility with the reviewed manager.

The final local suite passed 354 cases with no skips, including real Tcl and JavaScript, on 19 October 2026. CI runs the same suite, smoke and fresh install/restart/backup/restore on native AMD64 and ARM64. Its current result must be checked separately. Physical acceptance is deliberately empty in `docs/acceptance.json`; release publication rejects missing observations or different artifact hashes.
