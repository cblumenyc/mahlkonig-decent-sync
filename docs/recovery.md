# Updates, backups and recovery

Keep the current and previous verified packages, provenance results, configuration, certificate pins and private credentials. Back up private material outside the public repository with owner-only permissions. Do not put a live journal, passwords, keys or raw diagnostic exports in an issue or release archive.

## Compose update and code rollback

Download and verify the next Compose release on the server with `ops/download_release.py`. Extract it into a new directory without overwriting the current release or volume. Keep the current `.env` and its exact image available. Use the current package's updater:

```sh
python update.py prepare --current /srv/sync-releases/CURRENT \
  --target /srv/sync-releases/NEXT --transaction /srv/sync-private/update.json
python update.py resume --transaction /srv/sync-private/update.json
```

Preparation builds without stopping the running service, copies only the installation environment, records both image identities and fingerprints the release trees including both environment files. Activation requires the shared idle gate, takes an online backup, stops the service, checks the frozen journal again, starts the next image and checks health/version. The second check catches a grind that raced the stop. The Docker operator's temporary privileged exec reaches the root-only local control socket; the running server remains UID 10001. No administrative HTTP endpoint exists.

If the response is lost after successful activation, `resume` recognizes the running version without restarting it. If interruption happened while the original service was stopped, it restarts that release and rechecks idle state. If a failed new service cannot prove idle, rollback is deferred; keep the volume and transaction intact, inspect connectivity and journal state, and recover with the owner. Do not force replacement underneath an active or uncertain delivery.

For a later deliberate rollback, with both directories/images retained:

```sh
python update.py rollback --transaction /srv/sync-private/update.json
```

Rollback selects the prior executable and keeps the **current journal**. It never restores the pre-update snapshot. Missing/changed image or release fingerprints stop the operation. After success, use the selected release directory for future Compose commands. Never prune its image or the previous rollback image before verifying the next update.

## Existing managed installation

Only the installed restricted `/usr/local/sbin/coffee-sync-deploy` may activate or roll back application code. The independently reviewed manager update accepts an explicit ordinary operator and SHA-256 of `deploy.py`; it retains the old manager privately. Do not run this installation step under routine application-update authorization.

After administrator review, the operator inbox is `~/mahlkonig-decent-sync-inbox`. Download a verified `server` package on that host. Copy it into the inbox as `SHA256.tar.gz`, where `SHA256` is the verified archive digest. Then:

```sh
sudo /usr/local/sbin/coffee-sync-deploy submit SHA256
sudo /usr/local/sbin/coffee-sync-deploy status
sudo /usr/local/sbin/coffee-sync-deploy status OPERATION_SHA256
sudo /usr/local/sbin/coffee-sync-deploy rollback PREVIOUS_RELEASE_SHA256
```

The helper seals the operator upload, rejects unsafe/unlisted files and incompatible dependencies/schema, runs the portable check as an isolated unprivileged account, waits for idle, backs up the journal and checks activation health. Its rollback never rewinds the journal. A running application cannot replace the helper, system units or sudo policy. Existing `coffee-bridge` account and service names are intentional compatibility exceptions.

## Backup and disaster recovery

New Compose installations make one online SQLite snapshot daily under `/data/backups`, retain 14 dated snapshots and retry failures hourly. The existing managed timer uses the same `backup.py`; do not also enable its container scheduler. Snapshot work is bounded and off the polling loop. Online snapshots include installation UUID, controller history, frozen cycles and uncertain delivery evidence.

Daily journal snapshots do **not** include private configuration or credentials. Keep an owner-only copy of the data volume's `config.json` and referenced `grinder-password`, plus `.env`, pins and the exact release package. Managed equivalents live under `/etc/coffee-bridge`. Take a new private copy after approved setup changes. These files must not be emailed, logged or published by the tooling.

To recover after disk loss, stop the service and use an empty recovery volume/path. From the matching package runtime:

```sh
python backup.py /private/verified-snapshot.sqlite3 --restore-to /new-data/bridge.sqlite3
```

The destination must not already exist and must have no WAL/SHM sidecars. Integrity, schema and durable installation identity are checked. Restore private configuration/credential files with mode 0600 and data ownership 10001:10001 for Compose (the existing service owner for managed installs). Correct absolute config paths for the new host. Start the same verified release and let native reconciliation finish before making a shot. Verify server UUID, history, uncertain records, targets and readiness before enabling further feedback.

An interrupted restore may leave a new incomplete destination. Keep it for inspection and retry into another empty path; never overwrite a live journal. Pre-community snapshots without installation identity require assisted recovery and pairing review; normal restore deliberately rejects them. A code rollback is never a reason to restore old data.

## Classic recovery

Before first restart after rename, use the assisted migration's `rollback`; it verifies that live data has not changed. After normal in-app update, close de1app and restore the saved `previous-generation` value as `active-generation` atomically. An empty previous value means the initial complete package: remove the active pointer while the app is closed. Keep settings/outbox untouched. Never select a generation whose complete files/hashes cannot be verified.

Both the renamed and old hook implementations must not run together. If the app was started during migration or new events appeared, stop and preserve both copies; automatic rollback refuses to lose those events. See [installation](installation.md) for the supported migration window.


## Journals created before telemetry retention

The server compacts old routine payloads in short background transactions while no cycle or update is active. SQLite reuses the freed pages immediately. New journals enable incremental auto-vacuum so normal retention can return unused pages to the filesystem. Existing journals created with auto-vacuum disabled require one coordinated, idle, offline `PRAGMA auto_vacuum=INCREMENTAL; VACUUM;` to physically shrink the file. Use the current journal, a verified fresh backup, ample temporary space, and the managed installation's existing idle/maintenance gates. Do not copy an earlier snapshot over the live file or discard a WAL. Preserve logical records and installation identity through compaction. Backups keep their existing retention policy.

Pre-retention executables cannot interpret old routine SHA-256 receipts. Before a code rollback, let the current controller queue drain; keep all saved events if it cannot. Current supported executables retain exact lifecycle payloads and interpret both full routine payloads and compact receipts. Never restore an earlier journal to make old code accept a receipt.
