# Installation and migration

Mahlkönig - Decent sync needs a local server. The first community beta targets one Decent and one E64 WS, with either Classic or Decaid active. A Raspberry Pi with 64-bit Linux, an AMD64 Linux computer, or a NAS that supports Docker Compose and host networking can run it. Tablet-only hosting is not supported.

The software is currently a release candidate under development. Check [acceptance](acceptance.md) and [compatibility](compatibility.md) before installing. Building a package does not certify it for unattended use.

## Prepare the host

Use Docker Engine with the Compose plugin, Python 3.13 or newer, and a current GitHub CLI supporting `gh attestation verify`. Give the host a reserved LAN IPv4 address and accurate clock. The tablet, grinder and server must reach each other without guest-network isolation. Reserve several GB for images, journals and backups; a nearly full disk cannot safely update or migrate.

The grinder expects Xenia on **port 80**. Check that the host has no existing service listening there. This package uses Linux host networking so the server sees the actual grinder peer address. Do not substitute port mapping that hides that address. If another service owns port 80, use a dedicated host or a separately configured LAN address/network namespace; the provided Compose file binds port 80 on the host. See Docker's [host networking documentation](https://docs.docker.com/engine/network/drivers/host/).

## Download and start

Use a published, accepted version shown on the repository's Releases page. From an audited source checkout, download on the installation host:

```sh
python ops/download_release.py --version VERSION --kind compose --destination /srv/sync-downloads/VERSION
```

Replace `VERSION` everywhere with the exact release version. The downloader verifies the tag, release workflow provenance, package size, compatibility and SHA-256. It retains the manifest and verification results. Do not bypass a provenance failure. The first accepted release does not exist until the hardware gates are complete.

Extract the verified `mahlkonig-decent-sync-VERSION-linux-compose.tar.gz` into a **new** version directory such as `/srv/sync-releases/VERSION`. Copy `environment.example` to `.env` there. Set `SYNC_ORIGIN` to the server's LAN HTTP address and `SYNC_LAN_CIDR` to the trusted subnet; retain the package's `SYNC_VERSION`. Then, from that directory:

```sh
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build --wait
```

The runtime uses UID/GID 10001, a read-only filesystem, a persistent `sync-data` volume, automatic restart, a health check and daily online journal backups. Configuration is created only when absent. Reboots and new images retain the volume. Never use `down --volumes` on a real installation.

Open the configured LAN address. The dashboard is password-free on that trusted LAN; host, origin, subnet and grinder-peer checks still apply. Do not expose it to the Internet. Health means the process and journal work; **Ready** additionally requires the current controller, commissioned profile, verified targets, native grinder state and usable measurements.

## Guided setup

1. Choose Classic or Decaid and enter the tablet's REST address. Classic commonly uses port 8888; Decaid v0.9.0 uses port 8080. Enable the app's local API on the tablet.
2. Enter the grinder address and its Owner credential if native readback requires it. Read and confirm its two certificate fingerprints while on the trusted LAN. Credentials stay in a separate private file; they are not returned by the API, exported or packaged. A later certificate change requires inspection and re-enrollment, not automatic trust.
3. Configure the grinder's supported Xenia connection to the server's address on port 80. Preserve its native recipe and learning data.
4. Install the matching tablet plugin below. Wait for the effective current profile to appear.
5. In **Profile setup**, select the existing grinder recipe and the desired time after preinfusion. Confirm that changing grind can affect this profile's extraction time. Set yield/time on the grinder and wait for native readback to match. The Decent's effective workflow owns yield; the grinder owns dose. No recipe-writing interface is provided.
6. Select measurement behavior in Classic. Scale is the new-install default. Volume estimates and automatic fallback require an explicit selection. Decaid initially requires finalized scale weight. Missing measurements or correlation produce history-only results.
7. Feedback starts **disabled**. Enable it only after commissioning, target verification and deliberate beta acknowledgement. Existing approved managed settings are retained during an application update. Physical acceptance is separately recorded.

The plugin never starts extraction. Start the shot on the Decent. One physical grinder press arms feedback before, during or after extraction within the original native pending window; it never extends that window.

## Classic installation

Download the matching `classic` package with the same verification tool. Close de1app. For a new installation, extract the complete `mahlkonig_decent_sync` folder into the app's plugin directory and enable **Mahlkönig - Decent sync**. Open its settings, enter the server address and save. Pairing verifies and stores the server's durable installation UUID.

Keep settings and `outbox/` in the app's plugin data directory. Do not copy only `plugin.tcl`: the complete package includes modules, artwork and notices. Existing `decent_sync` installations must use the assisted migration below. Never enable both names.

After initial migration, **Updates → Check updates** obtains the complete matching package from the server. Downloads are size/hash/identity checked and committed only while both sides are idle. Reopen de1app to activate the selected generation. Settings and event bytes are untouched. Loader API changes require another assisted complete-package installation; normal updates cannot silently replace the stable loader.

## Assisted Classic rename

This one-time tool supports the existing `decent_sync` 0.5.x installation. It runs on a computer with Python and Tcl/Tcllib, against the actual mounted tablet directories, while **de1app is closed**. Coordinate the transfer with the owner; no unattended tablet-copy mechanism is assumed.

First update the existing server through its managed updater so `/api/v1/identity` exists. Confirm that this is the same server address already stored by Classic. Record its `server_id`. Verify the new Classic package with `ops/download_release.py`; use the reported SHA-256. Locate the old plugin code directory, the old plugin data directory and de1app's global settings file from the app's own paths. Code and data may be the same folder. Do not guess an Android path.

```sh
python ops/migrations/classic_rename.py prepare --app-closed \
  --code /mounted/tablet/plugins/decent_sync \
  --data /mounted/tablet-data/plugins/decent_sync \
  --global-settings /mounted/tablet-data/settings.tdb \
  --package /verified/classic.zip --package-sha256 VERIFIED_SHA256 \
  --server-id EXISTING_SERVER_UUID --recovery /private/sync-migration-recovery
python ops/migrations/classic_rename.py resume --app-closed \
  --recovery /private/sync-migration-recovery
```

Preparation validates the existing server and package, reads settings strictly as Tcl data, and verifies a private recovery copy before changing live files. Activation temporarily disables both plugin names, installs the complete package and exact outbox bytes, removes the retired hook source, then restores the prior enabled/disabled choice under the new name. Unknown old files remain in the private backup; they never become executable input. Server history is unchanged.

After interruption, leave the app closed and run `resume` again. Before restarting the app, `rollback` restores the exact original files and settings. Once the new app has saved settings/events, automatic rollback refuses to discard them; retain both data copies and recover with the owner. Verify the running version, server UUID, saved-event drain, settings, badge and readiness before retiring the old backup.

The tool is separate from runtime packages. Its support window ends when the current 0.5.x installation has migrated successfully and the first community beta migration window closes. Remove it and its migration-specific tests in the next incompatible release; preserve the verified recovery copy privately.

## Decaid installation

Initially use **Decaid v0.9.0**. Select Decaid on the server during an idle transition; changing controller invalidates old commissioning and disables feedback. Install the verified matching `decaid` ZIP through Decaid's native plugin mechanism. Its ID is `mahlkonig_decent_sync.reaplugin`. Set **Server address** and **Appearance** in native plugin settings. Open the plugin's custom settings/status page for the logo, readiness, current measured Grind and queue status. Commission the effective workflow on the server.

Imports, edited history and the first replayed shot-state frame after connection never initiate eligible captures. Scale loss saves history without feedback; Decaid's water counter is not beverage yield. Plugin updates use Decaid's native mechanism and preserve plugin storage. Physical behavior and appearance still need the acceptance procedure.

## Existing managed Linux installation

The existing `coffee-bridge` service, account, data/config paths and restricted `coffee-sync-deploy` authority are compatibility contracts. Keep using them. New application packages do not install system units or privileged helpers. An administrator must separately review/install `ops/deployment/install_manager.py` and the exact `deploy.py` digest before packages containing the new assets can be accepted. That step is not part of routine server-update authorization.

Managed configuration may remain administrator-owned and read-only to the service. The setup API reports this explicitly; an administrator must save controller/pin changes within the existing installation policy. Application updates preserve previously approved feedback settings. See [recovery and updates](recovery.md).
