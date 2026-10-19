# API contract

The same bridge engine can accept a Classic or Decaid adapter. Only the configured controller ID is admitted. This LAN API requires JSON, no password, and the existing origin/host/network checks. `/api/v2/` stays reserved for Xenia.

## Routes

- `POST /api/v1/controllers/{id}/events`: durable event receipt, HTTP 202.
- `GET /api/v1/controllers/{id}/status`: connectivity, operating mode, effective profile, manual target readback, wake progress and controller shot history.
- `POST /api/v1/controllers/{id}/profiles/{profile_id}/commission`: idle-only profile setup with the current revision, existing `recipe_index`, `target_time_s` (default 24), `grams_per_ml` (default 1.0), and explicit `suitable_for_feedback: true`. This writes only bridge settings.

Manual `/api/v1/shots` compatibility is preserved with `first_drip` timing. Automatic events use `after_preinfusion`; they do not repurpose that manual endpoint.

A timely start with otherwise matching but older-than-six-second native readback can enter `awaiting_native_validation`. Receipt is acknowledged immediately; an exact cycle/grind/shot reservation is stored and a fresh grinder read requested in the background. Validation must finish within ten seconds, use a read completed after reservation, and pass the original identity/profile/pending-state checks. Status exposes `binding_pending` while waiting and `binding_validation` on success. Timeout, conflicting state or restart makes the shot history-only. This does not make delayed/replayed starts eligible and never allows feedback before fresh verification.

## Envelope

```json
{
  "schema_version": 1,
  "event_id": "session-a-3",
  "session_id": "session-a",
  "sequence": 3,
  "occurred_at": "2026-09-27T18:00:00Z",
  "kind": "shot_finished",
  "shot_id": "session-a-shot-1",
  "data": {
    "elapsed_ms": 28000,
    "profile": {
      "id": "stable-profile-id", "revision": "effective-settings-hash",
      "title": "Current profile", "mode": "volume",
      "target": {"value": 45, "unit": "mL"},
      "effective_settings": {"program": "adapter-specific normalized settings"}
    },
    "completion_reason": "target_volume",
    "quality_flags": [],
    "pour_volume": {"value": 45.5, "unit": "mL", "source": "de1_pour_volume"},
    "scale_mass": null
  }
}
```

This is a format example, not a payload to send to a real grinder.

Kinds are `profile`, `status`, `shot_started`, `preinfusion_complete`, `shot_finished`, and `shot_finalized`. Lifecycle events require a stable shot ID, same session, ordered sequences, and elapsed milliseconds relative to shot start. Extraction duration is finish elapsed minus preinfusion-complete elapsed. Session changes, missing phases, profile changes and unknown completion reasons never infer successful extraction.

Classic adapters 0.4/0.5 save `shot_finished` immediately at pump stop with `scale_mass_basis: "extraction_stop"`. When scale is the selected source, the bridge waits for `shot_finalized` after the app's scale `record_complete` signal. Its data repeats the frozen stop `elapsed_ms` and profile, and supplies `scale_mass`, `scale_mass_basis: "de1_final_espresso_weight"`, and `quality_flags`. Final drips affect beverage mass, not extraction duration. The bridge stores `scale_mass_at_stop_g` separately and sends only the finalized mass. Volume feedback retains its immediate stop-counter behavior.

The tablet expires the final-weight wait after 15 seconds; the bridge expires missing finalization after 20 seconds. Missing/disconnected/stale scales, an intervening machine activity, session or profile mismatch, and restart before finalization withhold feedback. The already-durable pump-stop event remains in history if the tablet restarts before the final event. Version 0.3 events remain readable for historical compatibility and are labelled as readings at stop; use the current adapter for scale-feedback commissioning.

`target_weight` requires scale mode or automatic mode selecting `scale`, plus `{value, unit:"g", source:"scale"}`. `target_volume` requires volume mode or automatic mode selecting `volume_estimate`, plus the app's profile-aware pour counter, not total pump water. Scale-only mode never substitutes an estimate for missing scale data.

In the explicitly selected `auto` mode, `target` is the effective weight target in grams. The profile also carries `fallback_target: {value:45, unit:"mL"}` and `fallback_policy: "during_shot"` (or `"at_start"`). Commissioning verifies that backup volume times conversion factor equals the native gram target. Start and finish events carry `measurement_source` (`scale` or `volume_estimate`). A volume finish requires `fallback_reason` (`scale_unavailable_at_start` or `scale_lost_during_shot`) and a native `target_volume` stop. The chosen volume source stays latched through scale reconnection. A scale finish requires a valid measured value and a native `target_weight` stop. Sources, estimates, actual readings, and fallback reasons remain separate in history and the dashboard. A manual/programmed end is never made eligible by fallback.

Classic includes effective runtime profile/override settings in the revision. It keeps a stable ID based on the file/title identity. Pure yield changes can reuse the profile’s commissioned program and time target but require fresh matching native target readback before another grind. Program changes require setup again.

An event ID is immutable. Exact retries return the same durable meaning; changing the payload or reusing a session sequence under another event ID returns 409. Unknown controllers return 404. Timestamps need explicit timezone. Fresh starts must arrive within 10 seconds and not more than 2 seconds ahead of bridge time. Keep tablet/server clocks synchronized. Delayed start events are saved but cannot attach retroactively to a grind.

## Journal and delivery

SQLite schema version 2 adds controller events, shots, profiles and read-only tablet archives; original tables/history remain. The event, correlation and delivery intent commit together. Dispatch is journaled before exposing Xenia completion. Each native grind and external result has a uniqueness constraint. Restart reconciles native history and conservatively retains uncertain results rather than replaying them.

Targets must match before the grind announcement. The bridge freezes profile/targets/native recipe in that cycle and verifies the actual native grind's recorded recipe. A mismatch sticks for that cycle even if settings change back. Only a fresh successful Sync grind with a matching native pending UUID can bind at shot start. The local eligibility window is 290 seconds, intentionally below the researched native expiry.

202 acknowledgement fields are `event_id`, `durable: true`, `duplicate`, and an optional human-readable summary. It means committed to the local server journal. `queued`/`journaled` are still distinct from native delivery. A linked delivery exposes `exposed_at` (sent), `verified_at` (exact native record and pending consumption), outcome, changed target and physical readback. Physical movement is reported only from the existing sensor criteria.

On an idle startup the Classic REST adapter archives up to five recent shot files with version hashes. These are opaque recovery evidence: no Tcl evaluation, inferred stop reason, or retroactive feedback. Persistent controller events are the source of automatic results. Full archival parsing/backfill is intentionally not a feedback path.

## Contract 2

The same `/api/v1/controllers/{controller}/events` endpoint accepts `schema_version: 2`. Event acknowledgements and controller status include a nullable `cycle` offer containing `cycle_id`, native `grind_uuid`, controller `session_id`, `profile_revision` and `expires_at` (UTC epoch seconds). The server persists an offer before returning it. The tablet freezes it at shot start and includes the identical object as top-level `cycle` on every lifecycle event. No available offer is represented explicitly as `null`, producing history without feedback.

Binding joins that exact issued identity and the frozen execution settings. It does not infer a grind from receipt timing or reject a valid identity merely because a read cache passed a short age cutoff. Native operation barriers request a read begun after the relevant delivery/arming fact before authorizing completion. Native pending expiry, newer-grind detection, profile changes and measurement-quality checks remain enforced.

Eligible results may be `awaiting_events`, `awaiting_final_mass`, `awaiting_arm`, or `awaiting_native` before becoming `queued` and then `journaled`. Restart resumes proven unexposed facts only; exposed feedback remains subject to native-history reconciliation. A new controller session does not acquire an old session's unclaimed offer. Measurement units, scale finalization, and the approved automatic volume fallback remain unchanged.

ACKs also contain `display`: independent grinder connectivity, active-cycle identity/deadline, and a nullable notice with stable `id`, `title`, and `detail`. Notice IDs deduplicate UI popups. “Updating grinder” is distinct from “Grinder received shot” and from confirmed burr movement. An event ACK still means durable receipt, not feedback delivery.

The display contract includes nullable `display.grind_position`: `{"um":99.9,"age_s":3.2}` when cached native driver position is finite/nonnegative, its sensor reports valid, grinder connectivity is healthy and the read is under 35 seconds old. This is measured `ddValueDum / 10`, never a recipe target. Missing/invalid/stale evidence yields `null`. Display responses perform no extra network I/O. Classic expires the remaining sensor lifetime locally and blanks after ten seconds without a server ACK; older/missing fields also produce a blank readout. This field does not change profile revision, event identity, shot measurements or receipt semantics.

## Installation identity

`GET /api/v1/identity` returns `schema_version`, durable `server_id`, `product: "mahlkonig-decent-sync"`, display `name`, release `version`, active `controller_id`, `controller_contracts`, `journal_schema`, `setup_api`, supported `transports`/`feedback_modes`, `starts_extraction: false` and `multiple_controllers: false`. The UUID is stored in SQLite and restored with its backup. Changing controller does not change installation identity.

New HTTP clients send `X-Sync-Server-ID` on every event retry. ACKs include `server_id`; a client removes queued bytes only when both UUID and event ID match and `durable` is true. Earlier supported HTTP clients without the header remain admitted by the existing LAN/controller guards. A changed UUID is a recovery condition, never permission to redirect a saved queue.

## WebSocket

`GET /api/v1/controllers/{id}/ws` upgrades under the same host/origin/network and active-controller guards. Maximum message size is 128 KiB; server heartbeats run every 15 seconds. The server first sends `{type:"hello", ...identity}`. Client messages are:

```json
{"type":"event","server_id":"paired UUID","event":{"schema_version":2}}
```

The abbreviated event above stands for the complete contract-2 envelope. It uses the exact same validation, transaction and acknowledgement implementation as HTTP. Responses are `{type:"ack", server_id, event_id, durable, duplicate, summary, cycle, display}`. `{type:"status",server_id}` requests current controller status/display. Invalid input returns `{type:"error",server_id,error}` and does not authorize queue deletion. Contract 1 is HTTP-only. Reconnects retry identical stored events; they never reconstruct new eligible shots from historical snapshots.

## Decaid measurements

The v0.9.0 adapter advertises `adapter_version: "decaid-0.9.0"` and scale mode. A fresh live preheating identity precedes capture; the first connection snapshot is ignored. Machine snapshots establish preinfusion end and extraction stop. Finalized records must match the live ID, effective workflow, original/updated timestamps and native stop reason, within the finalization window.

`shot_finished` records pump-stop scale mass with `scale_mass_basis: "extraction_stop"`. `shot_finalized` repeats the frozen elapsed/profile and uses `scale_mass_basis: "decaid_finalized_weight"`, plus `provenance: {live_shot_id, record_id, unedited:true}`. Water is a separate `{value,unit:"mL",source:"decaid_water_volume"}` field; it never becomes estimated beverage grams. Scale loss, stale/reordered frames, missing phases, imported/edited records and reconnect-only evidence are rejected for feedback. `profile` heartbeats carry `measurement_ready` from fresh live scale state.

## Setup API 1

All mutations require JSON and retain the existing origin/host/network safeguards. These interfaces do not administer the host or privileged updater.

| Route | Contract |
| --- | --- |
| `GET /api/v1/setup` | Version, controller/address, pin enrollment, profile/measurement/target state, feedback state, readiness and required action; no credentials |
| `POST /api/v1/setup/certificates` | `{schema_version:1,host}`; bounded read-only certificate fingerprint probe, no credential transmission |
| `POST /api/v1/setup/devices` | `{schema_version:1,controller_type,controller_url,grinder_host,certificates,owner_password?}`; pins rechecked before atomic configuration replacement |
| `POST /api/v1/setup/feedback` | `{schema_version:1,enabled:boolean,acknowledge_beta?:boolean}`; idle and verified targets required; no implicit physical acceptance |

Controller selection is idle-only, disables feedback on a new selection and invalidates prior commissioning. A durable capture identity reconciles interruption between configuration replacement and the journal commit. Existing approved settings survive unchanged selection. A different grinder requires a separate installation. Managed read-only configurations require administrator changes under the installed policy.

## Operations and manual API

`GET /healthz` reports process/journal access and version/UUID, not sync readiness. `GET /api/v1/status` includes `update_idle_reason`. `POST /api/v1/refresh` requests a background native read; `POST /api/v1/cancel` preserves the current supported cancellation behavior. Administrative maintenance exists only on the local Unix socket.

`POST /api/v1/shots` retains `{source:"manual",shot_id,grind_uuid,weight_g,duration_ms,timing_basis:"first_drip",valid:true,record_only:boolean}` with stable retry identity. `GET /api/v1/shots/{id}` returns its public result. Manual and controller timings remain distinct. These examples are documentation, not instructions to submit synthetic data to hardware.

`GET /api/v1/updates/classic` and `/api/v1/updates/classic/files/{name}` serve the server release's complete allowlisted Classic package. The catalogue includes installation UUID, matching server/package version, bootstrap API, contract and per-file size/SHA-256. Files are served locally without private installer hosts. `/api/v1/notices` returns software/artwork notices.
