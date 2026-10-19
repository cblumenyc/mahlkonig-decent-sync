# Compatibility

| Component | Target | Evidence |
| --- | --- | --- |
| Grinder | Mahlkönig E64 WS, one per installation | Existing native integration; new release hardware acceptance pending |
| Classic | de1app plugin API, Tcl 8.5+, DUI / Streamline integration | Actual Tcl tests; exact app, tablet and firmware combination must be recorded during acceptance |
| Decaid | **v0.9.0**, plugin API 1 | Pinned source/API review, actual JavaScript host tests and Python delivery integration; physical validation pending |
| Linux server | AMD64 or ARM64, Docker Engine + Compose host networking | Native AMD64 and ARM64 clean-package installation, restart, recovery and image rollback passed in [CI](https://github.com/cblumenyc/mahlkonig-decent-sync/actions/runs/39294711668); real host/hardware acceptance pending |
| Python | 3.13+ | Development checks also run on 3.14; exact dependencies in `bridge/requirements.lock` |
| Test tools | Tcl/Tcllib `json`, `sha256`, `http`; Node.js 22+; pytest 9.1.1 | Tests must execute, never count skips as passes |
| Delivery | Xenia one-press, cached two-second polling | Supported automatic route |
| Controller transport | HTTP contracts 1/2; WebSocket contract 2 | Shared validation, persistence and durable ACK |
| Measurement | Classic finalized scale / explicit volume or automatic fallback; Decaid finalized scale | Provenance and rejection regression tests |
| Managed service | Existing contract-2 `coffee-bridge` installation | Restricted helper; asset support requires separately reviewed manager installation |

The public beta compatibility claim is limited to combinations recorded in `acceptance.json`. Until it contains an accepted release, these are targets rather than certified combinations. An earlier working plugin version does not establish acceptance of this renamed, modular package.

On 19 October 2026, Classic candidate 0.1.0-beta.4 was installed through the in-app updater on de1app 1.46.1.1, K811C / Android 8.1.0, with Streamline Dark. Settings, the bundled logo, skin controls, fonts and installed/latest version labels were confirmed on the tablet. Running version, package hashes, unchanged settings, targets and history, a single enabled plugin, an empty delivery queue, and server readiness were verified. These are startup, UI and update checks. Extraction, recovery and full release acceptance remain pending.

Not supported: tablet-only hosting, other grinder models, simultaneous controllers, Home Assistant, cloud delivery, firmware modification, access-control bypasses and custom burr algorithms. A different grinder requires a separate journal/installation. A controller switch requires idle state and fresh commissioning.

Compatibility identifiers remain explicit: `DE1-SYNTHETIC-BRIDGE`, Xenia device identity, `COFFEE-BRIDGE-MANUAL` protocol marker, schema-2 journal tables, `/api/v2/`, `/api/v1/controllers/`, `coffee-bridge` service/data/config names, and the local deployment socket. Product-facing names use **Mahlkönig - Decent sync**. Software paths use `mahlkonig-decent-sync`, Classic uses `mahlkonig_decent_sync`, and Decaid uses `mahlkonig_decent_sync.reaplugin`.
