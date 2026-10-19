# Actual asynchronous updater, isolated local HTTP and disposable plugin files.
source [lindex $argv 0]
package require json
package require http
package require sha256
source [file join [file dirname [info script]] classic_profile_vars.tcl]
set root [lindex $argv 1]
set origin [lindex $argv 2]
set scenario [lindex $argv 3]
set ::plugins::mahlkonig_decent_sync::outbox [file join $root outbox]
file mkdir $::plugins::mahlkonig_decent_sync::outbox
array set ::plugins::mahlkonig_decent_sync::settings [list bridge_url $origin server_id 11111111-1111-1111-1111-111111111111 controller_id classic-de1 measurement_mode auto grams_per_ml 1.0 fallback_policy during_shot show_popups 1 show_badge 1 appearance dark user_preference retained]
array set ::settings {skin {Streamline Dark} settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_weight 36 final_desired_shot_volume 0 grinder_setting 7.5}
set ::machine_state Idle
namespace eval ::de1::state {proc current_state {} {return $::machine_state}}
proc save_plugin_settings {args} {error {Updater must not save preferences}}
set settings_before [lsort [array get ::settings]]
set plugin_before [lsort [array get ::plugins::mahlkonig_decent_sync::settings]]
set profile_before [::plugins::mahlkonig_decent_sync::profile]
set code_before [::plugins::mahlkonig_decent_sync::update_read_binary $::plugins::mahlkonig_decent_sync::plugin_file]
set data_before {}
foreach name {settings.tdb history/previous.shot} {
    dict set data_before $name [::plugins::mahlkonig_decent_sync::update_read_binary [file join $root $name]]
}
set ::plugins::mahlkonig_decent_sync::last_server_ack [clock seconds]

proc wait_update {} {
    set deadline [expr {[clock milliseconds]+12000}]
    while {$::plugins::mahlkonig_decent_sync::update_phase in {checking downloading}} {
        if {[clock milliseconds]>$deadline} {error {Updater did not finish asynchronously}}
        after 5 {set ::tick 1};vwait ::tick
    }
}
::plugins::mahlkonig_decent_sync::check_updates
set job $::plugins::mahlkonig_decent_sync::update_job
::plugins::mahlkonig_decent_sync::check_updates
if {$::plugins::mahlkonig_decent_sync::update_job!=$job} {error {overlapping update check}}
wait_update

if {$scenario in {current older}} {
    if {$::plugins::mahlkonig_decent_sync::update_phase ne "current"} {error {current/older version offered for install}}
} elseif {$scenario in {bad_manifest incompatible wrong_health manifest_redirect}} {
    if {$::plugins::mahlkonig_decent_sync::update_phase ne "error"} {error {invalid catalogue accepted}}
} else {
    if {$::plugins::mahlkonig_decent_sync::update_phase ne "available"} {error "No candidate: $::plugins::mahlkonig_decent_sync::update_status"}
    switch -- $scenario {
        local_busy {set ::plugins::mahlkonig_decent_sync::shot_id shot-in-progress}
        pending_scale {set ::plugins::mahlkonig_decent_sync::pending_scale {shot_id final-weight-pending}}
        active_cycle {set ::plugins::mahlkonig_decent_sync::active_cycle real-cycle}
        stale_ack {set ::plugins::mahlkonig_decent_sync::last_server_ack [expr {[clock seconds]-11}]}
        local_steam {set ::machine_state Steam}
        saved_events {
            set fd [open [file join $root outbox preserved.json] w];puts -nonewline $fd {original queued event};close $fd
        }
        outbox_after_download {
            rename ::plugins::mahlkonig_decent_sync::update_downloaded ::plugins::mahlkonig_decent_sync::downloaded_original
            proc ::plugins::mahlkonig_decent_sync::update_downloaded {name bytes} {
                set fd [open [file join $::plugins::mahlkonig_decent_sync::outbox preserved.json] w]
                puts -nonewline $fd {original queued event};close $fd
                downloaded_original $name $bytes
            }
        }
        rename_failure {
            rename file original_file
            proc file {operation args} {
                if {$operation eq "rename" && [lindex $args end] eq [file join [file dirname $::plugins::mahlkonig_decent_sync::plugin_file] active-generation]} {error {simulated rename failure}}
                uplevel 1 [list original_file $operation {*}$args]
            }
        }
        source_changed {
            rename ::plugins::mahlkonig_decent_sync::update_downloaded ::plugins::mahlkonig_decent_sync::downloaded_original
            proc ::plugins::mahlkonig_decent_sync::update_downloaded {name bytes} {
                atomic_write $::plugins::mahlkonig_decent_sync::plugin_file {externally replaced code}
                downloaded_original $name $bytes
            }
        }
        server_changed {set ::plugins::mahlkonig_decent_sync::settings(bridge_url) http://changed.local}
    }
    ::plugins::mahlkonig_decent_sync::install_update
    ::plugins::mahlkonig_decent_sync::install_update
    wait_update
    set phase $::plugins::mahlkonig_decent_sync::update_phase
    if {$scenario eq "install"} {
        if {$phase ne "installed" || $::plugins::mahlkonig_decent_sync::update_status ne "Reopen de1app"} {error "Install failed: $phase $::plugins::mahlkonig_decent_sync::update_status"}
        set pointer [file join [file dirname $::plugins::mahlkonig_decent_sync::plugin_file] active-generation]
        if {![file exists $pointer]} {error "No active generation"}
        ::plugins::mahlkonig_decent_sync::update_action
        if {$::plugins::mahlkonig_decent_sync::update_job!=$job} {error {installed code rechecked before restart}}
    } elseif {$scenario in {local_busy pending_scale active_cycle stale_ack local_steam saved_events remote_busy busy_after_download outbox_after_download}} {
        if {$phase ne "available"} {error "Idle gate did not retain update: $phase"}
    } else {
        if {$phase ne "error"} {error "bad update installed: $scenario $phase"}
    }
}
if {[info exists ::download_executed]} {error {Downloaded Tcl was evaluated before restart}}
if {$scenario ni {install source_changed} && [::plugins::mahlkonig_decent_sync::update_read_binary $::plugins::mahlkonig_decent_sync::plugin_file] ne $code_before} {error {failed update replaced running code}}
if {$scenario eq "source_changed" && [::plugins::mahlkonig_decent_sync::update_read_binary $::plugins::mahlkonig_decent_sync::plugin_file] ne "externally replaced code"} {error {external file change overwritten}}
if {$scenario eq "server_changed"} {set ::plugins::mahlkonig_decent_sync::settings(bridge_url) $origin}
if {[lsort [array get ::settings]] ne $settings_before || [lsort [array get ::plugins::mahlkonig_decent_sync::settings]] ne $plugin_before || [::plugins::mahlkonig_decent_sync::profile] ne $profile_before} {error {settings or frozen profile changed}}
dict for {name bytes} $data_before {
    if {[::plugins::mahlkonig_decent_sync::update_read_binary [file join $root $name]] ne $bytes} {error {settings/history file changed}}
}
if {$scenario in {saved_events outbox_after_download} && [::plugins::mahlkonig_decent_sync::update_read_binary [file join $root outbox preserved.json]] ne "original queued event"} {error {queued event lost}}
puts [::plugins::mahlkonig_decent_sync::jo phase [::plugins::mahlkonig_decent_sync::js $::plugins::mahlkonig_decent_sync::update_phase] status [::plugins::mahlkonig_decent_sync::js $::plugins::mahlkonig_decent_sync::update_status] preserved true]
