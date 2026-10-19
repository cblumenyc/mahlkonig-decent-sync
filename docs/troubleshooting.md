# Troubleshooting

| Status | Check / action |
| --- | --- |
| Server offline | Check host power, container/service health, LAN address and port 80. Direct-IP success with hostname failure indicates DNS, not a failed server. |
| Healthy but not Ready | Inspect the stated action: controller events, measurements, profile commissioning, targets, pending native state or disabled feedback. Health alone is not readiness. |
| Port 80 unavailable | Use a dedicated host/address. Do not silently change Xenia's port or bypass grinder-peer checks with a proxy. |
| Certificate changed | Confirm the actual grinder and its firmware/certificate change on the trusted LAN, then enroll the new fingerprints. Never disable pins. |
| Profile setup required | Select the current effective profile/workflow, recipe slot and time after preinfusion. Program changes need commissioning; yield changes need matching native readback. |
| Targets differ | Set yield/time on the existing grinder recipe; retain its dose. Wait for verified native values before grinding. |
| Saved events pending | Restore the same installation UUID/address. Do not repoint queued events at another server or delete the queue to make the status green. |
| Server identity changed | Recover the original journal/identity or perform assisted queue recovery. A new server at an old IP is a different installation. |
| History only | Inspect source, timing, stop reason, frozen cycle and quality flags. Missing evidence is not repaired by replaying an old shot against a new grind. |
| Press required | Press once within the original pending window. After expiry, the shot stays history-only. |
| Movement unconfirmed | Check native sensor/motion evidence. Receipt and changed target do not prove physical movement. Within-tolerance evidence is a separate outcome. |
| Update deferred | Finish the current cycle, restore tablet/native connectivity and let queued events drain. Keep rollback files; do not bypass idle checks. |
| Disk full | Stop attempting updates. Preserve journals, credentials and recovery material; free only identified disposable files or expand storage. |
| Decaid incompatible | Use v0.9.0 for this adapter. Imports, edited shots and reconnect snapshots intentionally do not qualify. |

For a private, read-only journal report, run `python ops/diagnose_feedback.py /path/to/bridge.sqlite3`. Exports include shot/profile details even though they omit credentials; review and redact them before sharing. Issue reports should include release/app/tablet/firmware versions, the concise status, expected/observed behavior and whether the issue is simulated or physical. Never attach passwords, keys, full configuration or an unredacted production journal.
