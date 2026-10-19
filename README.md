# Mahlkönig - Decent sync

A community integration that sends Decent Espresso results to a Mahlkönig E64 WS, so the grinder’s native algorithm can adjust subsequent grinds. Extraction always starts on the Decent. A physical grinder press arms feedback within the original pending window.

The community beta uses a local Python/aiohttp server and SQLite journal, with plugins for Classic de1app and Decaid 0.9.0. A Raspberry Pi or compatible Linux/NAS host runs the server; tablet-only hosting is future work. Each installation supports one Decent, one E64 WS and one active controller.

**Implementation and release validation are in progress. No community beta is hardware-accepted yet.** Release evidence and gates are recorded in [Acceptance](docs/acceptance.md). Do not confuse passing automated tests with verified physical operation.

[Installation](docs/installation.md) · [Architecture](docs/architecture.md) · [API](docs/controller-api.md) · [Compatibility](docs/compatibility.md) · [Recovery](docs/recovery.md) · [Troubleshooting](docs/troubleshooting.md) · [Audit](docs/audit/README.md)

## Design

- Cached two-second Xenia polling; background native reads never block poll handlers.
- Durable controller events, immutable retry payloads and frozen cycle/profile identity.
- Explicit profile commissioning and native target verification; new installations start with feedback disabled.
- Separate facts for server acknowledgement, native receipt, target changes, physical movement and position tolerance.
- Panels, Auto/Light/Dark themes, concise banners and read-only measured Grind in Classic; matching server readiness and a Decaid status/settings page.
- Reproducible, allowlisted server, Classic, Decaid and Linux Compose packages from one release model.

Classic retains its measured scale and explicitly selected volume-fallback modes. Decaid initially requires finalized scale weight; its water-volume counter is preserved separately and never treated as beverage yield. Missing measurements or correlation save history without feedback.

## Development

Use Python 3.13 or newer, Tcl 8.5 or newer with Tcllib `json`/`sha256` and `http`, and Node.js 22 or newer. Create a virtual environment outside the source checkout and install `bridge/requirements.txt` plus `pytest==9.1.1`.

```sh
python -m pytest -q bridge/tests
python ops/deployment/deploy_smoke.py bridge
python bridge/tests/demo_automation.py
python ops/release.py --version 0.1.0-beta.1 --output /absolute/path/outside/checkout/release
```

The version and destination are mandatory. A build never edits source. The demonstration listens only on `127.0.0.1:8681` and uses fake devices. Tests never submit fabricated measurements to real hardware.

The journal and existing compatibility-sensitive service/API identifiers remain readable. Production credentials, private research, historical bundles and hardware evidence are excluded from the product source and all package allowlists. Migration-only tooling is separate from the normal runtime.

## License and attribution

Software is [GPL-3.0-or-later](LICENSE). Authentic Mahlkönig artwork has separate terms recorded in [ASSET-NOTICES.md](ASSET-NOTICES.md). This project is not endorsed by Mahlkönig, Hemro Group or Decent Espresso.
