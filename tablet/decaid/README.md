# Mahlkönig - Decent sync for Decaid

Targets **Decaid v0.9.0**, plugin API 1. The release ZIP uses Decaid’s native plugin installation and update mechanism. Select Decaid in the local server’s Device setup first, then install the matching `-decaid.zip` release asset. Set **Server address** in the plugin’s native settings. The app-facing status/settings page is `/api/v1/plugins/mahlkonig_decent_sync.reaplugin/settings` on the tablet REST server. It includes the locally bundled logo, readiness, current measured Grind and connection/queue status. Appearance offers Auto, Light and Dark.

The plugin captures a fresh live shot ID, freezes the effective workflow yield and server cycle, and observes the machine’s preinfusion and extraction-stop boundaries. Only a matching, unedited finalized record can provide final scale weight. Imported shots, history edits and initial/reconnected stream snapshots cannot create eligible extractions. Scale loss or missing boundaries result in history-only records. Decaid’s integrated water volume is recorded separately; it is not a beverage-yield estimate.

Events are persisted using the host’s plugin storage. `storageWrite` is the durability acknowledgement. Sending waits for it; retries preserve the exact JSON event. Server acknowledgement is separate from native grinder receipt. Saved events retain the paired installation identity across app restarts and native plugin upgrades. A changed server identity blocks sending and requires recovery; it never silently repoints the queue.

This plugin makes no machine state, recipe or scale writes. Start extraction on the Decent. The server may wake an idle/sleeping machine only if that existing option is explicitly configured; it never starts extraction. One controller owns the grinder. Switching from Classic requires an idle transition and fresh commissioning.

The source targets tag `v0.9.0`, commit `b9f9d0559106809a70dea2023f6bce34b5bdd3ca`. See the [official API](https://github.com/decentespresso/decaid/blob/v0.9.0/doc/Api.md) and [plugin host contract](https://github.com/decentespresso/decaid/blob/v0.9.0/doc/Plugins.md). Automated host-contract tests do not establish physical tablet or grinder acceptance.

Software: GPL-3.0-or-later. Artwork attribution and separate terms: ASSET-NOTICES.md. This is a community integration without manufacturer endorsement.
