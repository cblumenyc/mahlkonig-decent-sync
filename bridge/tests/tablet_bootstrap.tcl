# Reproduce the documented de1app GUI wrapper contract without an Android canvas.
source [lindex $argv 0]
source [file join [file dirname [info script]] classic_profile_vars.tcl]
set ::fixture_out [lindex $argv 1]
array set ::settings {settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_volume 45 espresso_count 1987 espresso_clock 1234 pressure_end 6 maximum_flow 0 insert_preinfusion_pause 0}
array set ::de1 {scale_device_handle 0}
proc plugin_settings_file {plugin} {return [file join $::fixture_out $plugin settings.tdb]}
proc add_de1_page {args} {}
proc add_de1_button {args} {}
proc rescale_x_skin {x} {return $x}
proc add_de1_text {args} {
    if {[lsearch -exact $args -textvariable]>=0} {error {canvas dtext: unknown option "-textvariable"}}
}
proc add_de1_variable {args} {
    if {[lindex $args end-1] ne "-textvariable"} {error {add_de1_variable requires -textvariable last}}
    dict set ::bound_codes [lindex $args [expr {[lsearch -exact $args -tags]+1}]] [lindex $args end]
}
proc dui {args} {}
proc save_plugin_settings {plugin} {set ::saved_settings [array get ::plugins::mahlkonig_decent_sync::settings]}
proc plugins {args} {set ::registered_page $args}
namespace eval ::gui::notify {
    proc de1_event {event} {}
    proc scale_event {event} {}
}
namespace eval ::de1::event::listener {
    proc on_all_state_change_add {args} {set ::registered_listener $args}
}
namespace eval ::de1::state {
    proc current_state {} {return Idle}
    proc current_substate {} {return ready}
}
namespace eval ::de1::sav {
    proc check_for_sav {} {}
    proc skip_sav_check {} {return 1}
}
namespace eval ::device::scale::saw {proc check_for_saw {} {}}
# Network behavior is tested separately against a real loopback HTTP server.
rename ::plugins::mahlkonig_decent_sync::drain ::plugins::mahlkonig_decent_sync::network_drain
proc ::plugins::mahlkonig_decent_sync::drain {} {set ::drain_called 1}
::plugins::mahlkonig_decent_sync::main
::plugins::mahlkonig_decent_sync::main
if {!$::plugins::mahlkonig_decent_sync::running} {error {plugin failed to start}}
if {$::registered_page ne {gui mahlkonig_decent_sync plugin_mahlkonig_decent_sync}} {error {plugin page not registered}}
if {$::registered_listener ne {-sync ::plugins::mahlkonig_decent_sync::state_changed}} {error {capture listener not registered}}
if {[llength [trace info execution ::gui::notify::de1_event]]!=1} {error {duplicate stop hook}}
set ::plugins::mahlkonig_decent_sync::last_server_ack [clock seconds]
set ::plugins::mahlkonig_decent_sync::grinder_connected 1
set ::plugins::mahlkonig_decent_sync::sync_ready 1
if {[subst [dict get $::bound_codes cs_sync_text]] ne {Ready}} {error {status label is not live}}
if {$::plugins::mahlkonig_decent_sync::settings(appearance) ne "auto"} {error {appearance default missing}}
if {$::plugins::mahlkonig_decent_sync::settings(banner_position) ne "top_center" || $::plugins::mahlkonig_decent_sync::settings(banner_duration_s)!=4} {error {banner defaults missing}}
set base [dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision]
foreach key {pressure_end maximum_flow insert_preinfusion_pause} {
    set saved $::settings($key)
    incr ::settings($key)
    if {[dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision] eq $base} {error "$key is missing from revision"}
    set ::settings($key) $saved
}
set p [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]]
if {[dict get $p adapter_version] ne "classic-0.5"} {error {wrong extension version}}
::plugins::mahlkonig_decent_sync::select_measurement_mode auto
if {[dict get $::saved_settings measurement_mode] ne "auto"} {error {mode not persisted}}
if {[subst [dict get $::bound_codes cs_settings_notice]] ne {Profile setup required}} {error {mode notice not updated}}
set ::plugins::mahlkonig_decent_sync::shot_id active-shot
::plugins::mahlkonig_decent_sync::select_measurement_mode scale
if {$::plugins::mahlkonig_decent_sync::settings(measurement_mode) ne "auto"} {error {mid-shot mode change accepted}}
set ::plugins::mahlkonig_decent_sync::shot_id ""
rename save_plugin_settings original_save
proc save_plugin_settings {plugin} {error {disk full}}
::plugins::mahlkonig_decent_sync::select_measurement_mode volume
if {$::plugins::mahlkonig_decent_sync::settings(measurement_mode) ne "auto"} {error {failed save changed mode}}
puts {bootstrap and profile contract passed}
