# Capture for the Classic controller.
namespace eval ::plugins::mahlkonig_decent_sync {
    proc profile {} {
        variable settings;variable version
        set advanced [expr {[value ::settings(settings_profile_type)] in {settings_2c settings_2c2}}]
        set mode $settings(measurement_mode)
        set target_key [expr {$mode in {scale auto} ? "final_desired_shot_weight" : "final_desired_shot_volume"}]
        if {$advanced} {append target_key _advanced}
        set target [num [value ::settings($target_key) 0]]
        set unit [expr {$mode in {scale auto} ? "g" : "mL"}]
        set fields {}
        # Use de1app's authoritative profile key list. Prefix matching would include
        # live counters/clocks and omit pressure endpoints and flow limit overrides.
        foreach key [lsort -unique [concat [::profile_vars] {profile_filename scale_stop_at_half_shot}]] {
            if {$key in {author read_only read_only_backup profile_notes profile_language profile_hide profile_grinder_dose_weight profile_grinder_setting}} {continue}
            if {[info exists ::settings($key)]} {
                lappend fields $key [js $::settings($key)]
            }
        }
        set effective [jo {*}$fields]
        set identity "[value ::settings(profile_filename)]|[value ::settings(profile_title)]"
        set id "classic-[::sha2::sha256 -hex $identity]"
        set extra {}
        if {$mode eq "auto"} {
            set factor $settings(grams_per_ml)
            set backup [expr {$target eq "null" ? 0 : $target / double($factor)}]
            set extra [list fallback_target [jo value [num $backup] unit [js mL]] fallback_policy [js $settings(fallback_policy)]]
        }
        set revision [::sha2::sha256 -hex "$effective|$mode|$target|$extra"]
        # UI patch versions do not change the server's capture/final-weight contract.
        return [jo id [js $id] revision [js $revision] adapter_version [js classic-0.5] extension_version [js $version] title [js [value ::settings(profile_title) "Current profile"]] \
            mode [js $mode] target [jo value $target unit [js $unit]] effective_settings $effective {*}$extra]
    }
    proc stop_notification {command op} {
        variable shot_id;variable stop_reason;variable quality;variable automatic_shot;variable status_text
        if {[lindex $command 0] eq "::gui::notify::scale_event" && [lindex $command 1] eq "record_complete"} {
            if {[catch {finalize_scale} error]} {set status_text "Could not save final scale weight; feedback withheld"}
        }
        if {$shot_id eq ""} {return}
        set notification [lindex $command 1]
        if {$notification eq "sav_stop"} {set stop_reason target_volume}
        if {$notification eq "saw_stop"} {set stop_reason target_weight}
        if {$notification in {not_connected timeout_updates abandoning_updates}} {
            if {$automatic_shot && [lindex $command 0] eq "::gui::notify::scale_event"} {
                update_automatic_source
            } else {lappend quality $notification}
        }
    }
    proc scale_updated {name index op} {
        variable last_scale_update
        set last_scale_update [clock milliseconds]
    }
    proc finalize_scale {{failure ""}} {
        variable pending_scale;variable pending_scale_timer;variable last_scale_update;variable shot_cycles
        if {$pending_scale eq ""} {return}
        set pending $pending_scale
        set pending_scale {}
        if {$pending_scale_timer ne ""} {after cancel $pending_scale_timer;set pending_scale_timer ""}
        set mass null
        set flags {}
        if {$failure ne ""} {
            lappend flags [js $failure]
        } elseif {[value ::de1(scale_device_handle) 0] == 0 || [clock milliseconds]-$last_scale_update > 2000 || [value ::settings(scale_stop_at_half_shot) 0]} {
            lappend flags [js missing_final_scale_mass]
        } else {
            set weight [num [value ::de1(final_espresso_weight)]]
            if {$weight eq "null" || $weight <= 0} {
                lappend flags [js missing_final_scale_mass]
            } else {set mass [jo value $weight unit [js g] source [js scale]]}
        }
        enqueue shot_finalized [jo elapsed_ms [dict get $pending elapsed_ms] \
            profile [dict get $pending profile] scale_mass $mass \
            scale_mass_basis [js de1_final_espresso_weight] quality_flags [ja $flags]] [dict get $pending shot_id]
        dict unset shot_cycles [dict get $pending shot_id]
    }
    proc scale_available {} {
        variable last_scale_update
        set weight [num [value ::de1(scale_sensor_weight)]]
        return [expr {[value ::de1(scale_device_handle) 0] != 0 &&
            [clock milliseconds]-$last_scale_update <= 2000 &&
            $weight ne "null" && ![value ::settings(scale_stop_at_half_shot) 0]}]
    }
    proc update_automatic_source {} {
        variable automatic_shot;variable shot_source;variable fallback_reason;variable fallback_policy
        if {$automatic_shot && $shot_source eq "scale" && $fallback_policy eq "during_shot" && ![scale_available]} {
            set shot_source volume_estimate
            set fallback_reason scale_lost_during_shot
        }
    }
    proc measurement_metadata {} {
        variable shot_source;variable fallback_reason
        set reason [expr {$fallback_reason eq "" ? "null" : [js $fallback_reason]}]
        return [list measurement_source [js $shot_source] fallback_reason $reason]
    }
    proc check_for_volume {} {
        variable automatic_shot;variable fallback_target;variable shot_source;variable quality
        if {!$automatic_shot} {return [::de1::sav::mahlkonig_decent_sync_original_check_for_sav]}
        update_automatic_source
        if {$shot_source ne "volume_estimate" || $fallback_target <= 0} {return}
        # Use de1app's own profile-aware counter, stop command and notification.
        # The temporary target applies only to this native check, never saved profiles.
        set previous $::de1::sav::_target
        set ::de1::sav::_target $fallback_target
        set failed [catch {::de1::sav::mahlkonig_decent_sync_original_check_for_sav} result]
        set ::de1::sav::_target $previous
        if {$failed} {lappend quality capture_error}
    }
    proc check_for_weight {} {
        variable automatic_shot;variable shot_source
        if {$automatic_shot} {
            update_automatic_source
            if {$shot_source eq "volume_estimate"} {return}
        }
        ::device::scale::saw::mahlkonig_decent_sync_original_check_for_saw
    }
    proc skip_volume_check {} {
        variable automatic_shot;variable shot_source
        # de1app explicitly provides skip_sav_check as an extension override.
        if {$automatic_shot} {return [expr {$shot_source ne "volume_estimate"}]}
        ::de1::sav::mahlkonig_decent_sync_original_skip_sav_check
    }
    proc install_stop_hooks {} {
        foreach {native saved replacement} {
            ::de1::sav::check_for_sav ::de1::sav::mahlkonig_decent_sync_original_check_for_sav check_for_volume
            ::de1::sav::skip_sav_check ::de1::sav::mahlkonig_decent_sync_original_skip_sav_check skip_volume_check
            ::device::scale::saw::check_for_saw ::device::scale::saw::mahlkonig_decent_sync_original_check_for_saw check_for_weight
        } {
            rename $native $saved
            proc $native {} [list [namespace current]::$replacement]
        }
    }
    proc state_changed {event} {
        variable status_text;variable quality;variable shot_id;variable automatic_shot
        # An extension failure must never interrupt the machine's own state handlers.
        if {[catch {capture_state $event} error]} {
            lappend quality capture_error
            set status_text "Sync capture failed; this shot will not adjust the grinder"
            if {[dict get $event this_state] ne "Espresso"} {set shot_id "";set automatic_shot 0}
        }
    }
    proc capture_state {event} {
        variable session;variable sequence;variable shot_id;variable shot_start
        variable pi_sent;variable stop_reason;variable quality;variable last_scale_update
        variable settings;variable automatic_shot;variable shot_source;variable fallback_reason;variable fallback_target;variable fallback_policy
        variable cycle_offer;variable shot_cycles
        set state [dict get $event this_state]
        set sub [dict get $event this_substate]
        set previous [dict get $event previous_state]
        set previous_sub [dict get $event previous_substate]
        set ms [expr {round([dict get $event event_time] * 1000)}]
        variable pending_scale;variable pending_scale_timer
        if {$pending_scale ne "" && $state ne $previous && $state in {Espresso HotWater HotWaterRinse Steam}} {
            # A new activity can reset the native final-weight accumulator.
            finalize_scale activity_started_before_final_weight
        }
        if {$state eq "Espresso" && $shot_id eq "" && $previous ne "Espresso"} {
            set shot_id "$session-shot-[expr {$sequence+1}]"
            set shot_start $ms;set pi_sent 0;set stop_reason manual_or_unknown;set quality {}
            set automatic_shot [expr {$settings(measurement_mode) eq "auto"}]
            set fallback_reason ""
            set shot_source [expr {$settings(measurement_mode) eq "volume" ? "volume_estimate" : "scale"}]
            set p [profile]
            set claim null
            set parsed_profile [::json::json2dict $p]
            if {$cycle_offer ne "" && [dict get $cycle_offer session_id] eq $session &&
                [dict get $cycle_offer profile_revision] eq [dict get $parsed_profile revision] &&
                [clock seconds] < [dict get $cycle_offer expires_at]} {
                set fields {}
                foreach key {cycle_id grind_uuid session_id profile_revision} {
                    lappend fields $key [js [dict get $cycle_offer $key]]
                }
                set claim [jo {*}$fields expires_at [format %.17g [dict get $cycle_offer expires_at]]]
            }
            dict set shot_cycles $shot_id $claim
            # One offer belongs to at most one shot, even before network acknowledgement.
            set cycle_offer ""
            if {$automatic_shot} {
                set parsed [::json::json2dict $p]
                set fallback_target [dict get $parsed fallback_target value]
                set fallback_policy [dict get $parsed fallback_policy]
                if {![scale_available]} {set shot_source volume_estimate;set fallback_reason scale_unavailable_at_start}
            }
            enqueue shot_started [jo elapsed_ms 0 profile $p {*}[measurement_metadata]] $shot_id
        }
        if {$shot_id eq ""} {return}
        if {$state eq "Espresso" && $sub eq "pouring" && !$pi_sent} {
            set pi_sent 1
            enqueue preinfusion_complete [jo elapsed_ms [expr {$ms-$shot_start}] profile [profile]] $shot_id
        }
        if {($previous eq "Espresso" && $state ne "Espresso") || \
            ($previous eq "Espresso" && $previous_sub in {preinfusion pouring} && $sub ni {preinfusion pouring})} {
            set pour [num [value ::de1(pour_volume)]]
            set scale null
            if {[value ::de1(scale_device_handle) 0] != 0 && [clock milliseconds]-$last_scale_update <= 2000} {
                set weight [num [value ::de1(scale_sensor_weight)]]
                # The app can double a one-cup reading. Such a value is not measured full-shot mass.
                if {$weight ne "null" && ![value ::settings(scale_stop_at_half_shot) 0]} {
                    set scale [jo value $weight unit [js g] source [js scale]]
                }
            }
            if {$pour eq "null"} {lappend quality missing_pour_volume}
            if {!$pi_sent} {lappend quality missing_preinfusion_boundary}
            set flags {};foreach flag [lsort -unique $quality] {lappend flags [js $flag]}
            set final_profile [profile]
            set elapsed [expr {$ms-$shot_start}]
            enqueue shot_finished [jo elapsed_ms $elapsed profile $final_profile \
                completion_reason [js $stop_reason] quality_flags [ja $flags] \
                pour_volume [jo value $pour unit [js mL] source [js de1_pour_volume]] scale_mass $scale \
                scale_mass_basis [js extraction_stop] {*}[measurement_metadata]] $shot_id
            if {$shot_source eq "scale"} {
                set pending_scale [dict create shot_id $shot_id elapsed_ms $elapsed profile $final_profile]
                set pending_scale_timer [after 15000 [namespace code {finalize_scale final_scale_timeout}]]
            } else {
                dict unset shot_cycles $shot_id
            }
            set shot_id ""
            set automatic_shot 0
        }
    }
    proc heartbeat {} {
        variable last_profile;variable last_profile_at;variable outbox;variable status_text;variable server_testing
        variable telemetry_poll;variable update_phase
        if {$server_testing} {after 2000 [namespace code heartbeat];return}
        if {[catch {
            set p [profile]
            set empty [expr {![llength [glob -nocomplain -directory $outbox *.json]]}]
            # After an outage, the queued profile may be too old to become live.
            # Refresh it once the queue drains even if its settings have not changed.
            set polling [expr {[info exists telemetry_poll] && $telemetry_poll}]
            if {$p ne $last_profile || ($update_phase ne "waiting" && !$polling && $empty && [clock seconds]-$last_profile_at >= 30)} {
                enqueue profile [jo profile $p]
                set last_profile $p
                set last_profile_at [clock seconds]
            } elseif {$empty && $update_phase ne "waiting"} {
                if {$polling} {
                    poll_status
                } else {
                    enqueue status [jo state [js [::de1::state::current_state]] substate [js [::de1::state::current_substate]]]
                }
            }
        } error]} {set status_text "Sync capture error: $error"}
        after 2000 [namespace code heartbeat]
    }
}
