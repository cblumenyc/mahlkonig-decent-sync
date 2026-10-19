# Transport for the Classic controller.
namespace eval ::plugins::mahlkonig_decent_sync {
    proc enqueue {kind data {shot ""}} {
        variable settings;variable sequence;variable session;variable outbox;variable shot_cycles
        if {[info exists settings(server_id)] && $settings(server_id) eq ""} {return}
        incr sequence
        set id "$session-$sequence"
        set args [list schema_version 2 event_id [js $id] session_id [js $session] sequence $sequence \
            occurred_at [js [now]] kind [js $kind] data $data]
        if {$shot ne ""} {
            set cycle null
            if {[dict exists $shot_cycles $shot]} {set cycle [dict get $shot_cycles $shot]}
            lappend args shot_id [js $shot] cycle $cycle
        }
        atomic_write [file join $outbox [format {%s-%08d.json} $session $sequence]] [jo {*}$args]
        after 0 [namespace code drain]
    }
    proc drain {} {
        variable settings;variable busy;variable outbox;variable status_text
        variable verified_server
        if {$busy} {return}
        if {![info exists settings(server_id)] || $settings(server_id) eq ""} {
            set status_text "Pair this tablet with its server before sending events";return
        }
        if {![info exists verified_server] || $verified_server ne "$settings(bridge_url)|$settings(server_id)"} {
            verify_connection;return
        }
        set paths [lsort [glob -nocomplain -directory $outbox *.json]]
        if {![llength $paths]} {return}
        set path [lindex $paths 0]
        set fd [open $path r];fconfigure $fd -encoding utf-8;set body [read $fd];close $fd
        set id [dict get [::json::json2dict $body] event_id]
        set busy 1
        if {[catch {::http::geturl "$settings(bridge_url)/api/v1/controllers/$settings(controller_id)/events" \
            -headers [list X-Sync-Server-ID $settings(server_id)] -type application/json -query [encoding convertto utf-8 $body] -timeout 4000 \
            -command [list [namespace current]::ack $path $id]} error]} {
            set busy 0;set status_text "Server unreachable; events saved on tablet"
            after 5000 [namespace code drain]
        }
    }
    proc ack {path id token} {
        variable busy;variable status_text;variable cycle_offer;variable session;variable settings
        set accepted 0
        if {[::http::status $token] eq "ok" && [::http::ncode $token] == 202} {
            if {![catch {set body [::json::json2dict [::http::data $token]]}]} {
                if {[dict exists $body server_id] && [dict get $body server_id] eq $settings(server_id) && [dict exists $body durable] && [dict get $body durable] eq "true" && [dict exists $body event_id] && [dict get $body event_id] eq $id} {
                    set accepted 1
                    set status_text "Shot events saved on server"
                    if {[dict exists $body summary]} {set status_text [dict get $body summary]}
                    if {[string first "$session-" $id] == 0} {catch {update_display $body}}
                    # Only a response to this app session can replace its next-shot
                    # offer. A shot already in progress keeps its frozen copy.
                    if {[string first "$session-" $id] == 0 && [dict exists $body cycle]} {
                        set cycle_offer ""
                        set c [dict get $body cycle]
                        if {$c ne "null" && [dict exists $c session_id] && [dict get $c session_id] eq $session} {
                            set valid 1
                            foreach key {cycle_id grind_uuid session_id profile_revision expires_at} {
                                if {![dict exists $c $key]} {set valid 0}
                            }
                            if {$valid} {set cycle_offer $c}
                        }
                    }
                }
            }
        }
        ::http::cleanup $token;set busy 0
        if {$accepted} {
            file delete $path
            after 0 [namespace code drain]
        } else {
            set status_text "Server has not acknowledged storage; retrying saved events"
            after 5000 [namespace code drain]
        }
    }
    proc normalize_server {input} {
        set url [string trimright [string trim $input] /]
        if {![string match {*://*} $url]} {set url "http://$url"}
        if {![regexp {^http://([A-Za-z0-9][A-Za-z0-9.-]*)(:([0-9]{1,5}))?$} $url -> host suffix port]} {
            error "Enter a server hostname or IP address, optionally with a port"
        }
        if {$port ne "" && ($port < 1 || $port > 65535)} {error "Port must be between 1 and 65535"}
        return $url
    }
    proc save_server {} {
        variable server_input;variable server_testing;variable status_text;variable busy
        variable outbox;variable shot_id;variable pending_scale;variable active_cycle;variable active_cycle_expires
        if {$server_testing} {return}
        if {$busy || $shot_id ne "" || $pending_scale ne "" ||
            ($active_cycle ne "" && [clock seconds] < $active_cycle_expires) ||
            [llength [glob -nocomplain -directory $outbox *.json]]} {
            set status_text "Finish the current cycle and let saved events send before changing servers"
            return
        }
        if {[catch {set url [normalize_server $server_input]} error]} {set status_text $error;return}
        set server_testing 1;set status_text "Checking server…"
        if {[catch {::http::geturl "$url/api/v1/identity" -timeout 4000 -command [list [namespace current]::server_checked $url 1]}]} {
            set server_testing 0;set status_text "Could not reach that server; previous address retained"
        }
    }
    proc verify_connection {} {
        variable settings;variable server_testing;variable status_text
        if {$server_testing || $settings(bridge_url) eq ""} {return}
        set server_testing 1
        if {[catch {::http::geturl "$settings(bridge_url)/api/v1/identity" -timeout 4000 \
                -command [list [namespace current]::server_checked $settings(bridge_url) 0]}]} {
            set server_testing 0;set status_text "Server unreachable; saved events retained"
            after 5000 [namespace code drain]
        }
    }
    proc server_checked {url pairing token} {
        variable settings;variable server_testing;variable status_text;variable server_input;variable verified_server
        variable cycle_offer;variable last_profile;variable last_server_ack;variable grinder_connected
        variable busy;variable shot_id;variable pending_scale;variable outbox
        set valid 0
        catch {
            set body [::json::json2dict [::http::data $token]]
            set valid [expr {[::http::status $token] eq "ok" && [::http::ncode $token] == 200 &&
                [dict get $body product] eq "mahlkonig-decent-sync" &&
                [dict get $body controller_id] eq $settings(controller_id) &&
                [lsearch -exact [dict get $body controller_contracts] 2] >= 0 &&
                [regexp {^[a-f0-9-]{36}$} [dict get $body server_id]]}]
        }
        ::http::cleanup $token;set server_testing 0
        if {!$valid} {
            set status_text "Could not verify a compatible server; saved events retained"
            if {!$pairing} {after 5000 [namespace code drain]};return
        }
        set identity [dict get $body server_id]
        if {!$pairing} {
            if {$url ne $settings(bridge_url) || ![info exists settings(server_id)] || $identity ne $settings(server_id)} {
                set status_text "Server identity changed; saved events retained";return
            }
        } else {
            # A new event may have appeared while the verification was in flight.
            if {$busy || $shot_id ne "" || $pending_scale ne "" || [llength [glob -nocomplain -directory $outbox *.json]]} {
                set status_text "Finish sending current events before changing servers";return
            }
            set old_url $settings(bridge_url)
            set old_id [value ::plugins::mahlkonig_decent_sync::settings(server_id)]
            set settings(bridge_url) $url;set settings(server_id) $identity
            if {[catch {save_plugin_settings mahlkonig_decent_sync}]} {
                set settings(bridge_url) $old_url;set settings(server_id) $old_id
                set status_text "Could not save server; previous address retained";return
            }
            set server_input $url;set cycle_offer "";set last_profile "";set last_server_ack 0;set grinder_connected 0
        }
        set verified_server "$url|$identity"
        set status_text "Server verified. Connecting…"
        after 0 [namespace code drain]
    }
}
