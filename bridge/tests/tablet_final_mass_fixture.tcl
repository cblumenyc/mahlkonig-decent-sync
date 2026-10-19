source [lindex $argv 0]
source [file join [file dirname [info script]] classic_profile_vars.tcl]
package require json
package require sha256
set out [lindex $argv 1]
set scenario [lindex $argv 2]
file mkdir $out
namespace eval ::plugins::mahlkonig_decent_sync {
    array set settings {measurement_mode scale bridge_url http://unused.invalid controller_id classic-de1}
    set session final-mass-fixture
}
set ::plugins::mahlkonig_decent_sync::outbox $out
array set ::settings {settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_weight 36 scale_stop_at_half_shot 0}
array set ::de1 {pour_volume 44.54 scale_device_handle 2 scale_sensor_weight 35 final_espresso_weight 35}
proc state {old oldsub new sub seconds} {
    ::plugins::mahlkonig_decent_sync::capture_state [dict create previous_state $old previous_substate $oldsub this_state $new this_substate $sub event_time $seconds]
}
::plugins::mahlkonig_decent_sync::scale_updated de1 scale_sensor_weight write
state Idle ready Espresso heating 1000
state Espresso heating Espresso preinfusion 1001
state Espresso preinfusion Espresso pouring 1007
::plugins::mahlkonig_decent_sync::stop_notification {::gui::notify::scale_event saw_stop} enter
state Espresso pouring Espresso ending 1027.095
state Espresso ending Idle ready 1029
if {[llength [glob -directory $out *.json]] != 3} {error {pump-stop checkpoint was not saved separately}}
set ::de1(scale_sensor_weight) 36.2
set ::de1(final_espresso_weight) 36.2
::plugins::mahlkonig_decent_sync::scale_updated de1 scale_sensor_weight write
switch $scenario {
    lost {set ::de1(scale_device_handle) 0}
    stale {set ::plugins::mahlkonig_decent_sync::last_scale_update [expr {[clock milliseconds]-3000}]}
    timeout {::plugins::mahlkonig_decent_sync::finalize_scale final_scale_timeout}
    interrupted {state Idle ready HotWater pouring 1030}
}
::plugins::mahlkonig_decent_sync::stop_notification {::gui::notify::scale_event record_complete} enter
::plugins::mahlkonig_decent_sync::stop_notification {::gui::notify::scale_event record_complete} enter
foreach path [lsort [glob -directory $out *.json]] {
    set fd [open $path r];fconfigure $fd -encoding utf-8;puts [read $fd];close $fd
}
