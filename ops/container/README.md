# Linux package

**Mahlkönig - Decent sync** · Linux ARM64 / AMD64 · One E64 WS + Decent

This directory is the complete versioned Compose build context. The canonical [installation guide](https://github.com/cblumenyc/mahlkonig-decent-sync/blob/main/docs/installation.md) covers verified downloads, port 80, host networking, setup and plugin installation. The [recovery guide](https://github.com/cblumenyc/mahlkonig-decent-sync/blob/main/docs/recovery.md) covers updates, backups and rollback.

Use `environment.example` for a new `.env`; retain its release version. Existing configuration and the persistent `sync-data` volume must survive image changes. `update.py` handles prepared, idle-gated updates and code-only rollback. Never delete the production volume during an update.

The runtime uses UID/GID 10001 and starts with feedback disabled. Health is process/journal availability, not sync readiness. Review the published compatibility and hardware acceptance record before use. Docker Desktop and proxy/NAT arrangements that hide the grinder peer address are not deployment targets.
