# Native SAV/SAW contracts exercised through the real extension's wrappers.
source [lindex $argv 0]
source [file join [file dirname [info script]] classic_profile_vars.tcl]
package require json
package require sha256
set out [lindex $argv 1]
set scenario [lindex $argv 2]
file mkdir $out
array set ::settings {settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_weight 45 final_desired_shot_volume 0 scale_stop_at_half_shot 0}
array set ::de1 {pour_volume 0 scale_device_handle 2 scale_sensor_weight 0 app_autostop_triggered 0}
set ::stops 0
set ::scale_checks 0
namespace eval ::plugins::mahlkonig_decent_sync {
    variable settings
    array set settings {measurement_mode auto grams_per_ml 1.0 fallback_policy during_shot}
    set session auto-fixture
}
set ::plugins::mahlkonig_decent_sync::outbox $out
namespace eval ::gui::notify {
    proc de1_event {event} {}
    proc scale_event {event} {}
}
namespace eval ::de1::sav {
    variable _target 0
    proc skip_sav_check {} {return 1}
    proc check_for_sav {} {
        variable _target
        if {$_target>0 && ![skip_sav_check] && !$::de1(app_autostop_triggered) && $::de1(pour_volume)>=$_target} {
            incr ::stops
            set ::de1(app_autostop_triggered) 1
            ::gui::notify::de1_event sav_stop
        }
    }
}
namespace eval ::device::scale::saw {
    variable _target 45
    proc check_for_saw {} {
        variable _target
        incr ::scale_checks
        if {!$::de1(app_autostop_triggered) && $::de1(scale_sensor_weight)>=$_target} {
            incr ::stops
            set ::de1(app_autostop_triggered) 1
            ::gui::notify::scale_event saw_stop
        }
    }
}
foreach cmd {::gui::notify::de1_event ::gui::notify::scale_event} {
    trace add execution $cmd enter ::plugins::mahlkonig_decent_sync::stop_notification
}
::plugins::mahlkonig_decent_sync::install_stop_hooks
proc state {old oldsub new sub seconds} {
    ::plugins::mahlkonig_decent_sync::capture_state [dict create previous_state $old previous_substate $oldsub this_state $new this_substate $sub event_time $seconds]
}
::plugins::mahlkonig_decent_sync::scale_updated de1 scale_sensor_weight write
if {$scenario eq "absent"} {set ::de1(scale_device_handle) 0}
if {$scenario eq "at_start_only"} {set ::plugins::mahlkonig_decent_sync::settings(fallback_policy) at_start}
state Idle ready Espresso heating 1000
state Espresso heating Espresso preinfusion 1001
state Espresso preinfusion Espresso pouring 1004
if {$scenario in {lost reconnect at_start_only}} {
    set ::plugins::mahlkonig_decent_sync::last_scale_update [expr {[clock milliseconds]-3000}]
    ::gui::notify::scale_event timeout_updates
}
set ::de1(pour_volume) 46
::de1::sav::check_for_sav
if {$::de1::sav::_target!=0} {error {fallback modified the persistent native target}}
if {$scenario in {fresh reconnect}} {
    set ::de1(scale_sensor_weight) 45.2
    ::plugins::mahlkonig_decent_sync::scale_updated de1 scale_sensor_weight write
    ::device::scale::saw::check_for_saw
}
if {$scenario eq "fresh" && ($::stops!=1 || $::scale_checks!=1)} {error {scale was not preferred}}
if {$scenario in {absent lost reconnect} && ($::stops!=1 || $::scale_checks!=0)} {error {fallback did not latch or stop}}
if {$scenario eq "at_start_only" && $::stops!=0} {error {unauthorized mid-shot fallback}}
state Espresso pouring Espresso ending 1028
state Espresso ending Idle ready 1029
if {$::plugins::mahlkonig_decent_sync::automatic_shot} {error {automatic mode escaped the shot}}
if {![::de1::sav::skip_sav_check]} {error {native skip behavior not restored}}
foreach path [lsort [glob -directory $out *.json]] {
    set fd [open $path r];fconfigure $fd -encoding utf-8;puts [read $fd];close $fd
}
