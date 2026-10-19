source [lindex $argv 0]
source [file join [file dirname [info script]] classic_profile_vars.tcl]
package require json
package require sha256
set out [lindex $argv 1]
set scenario [lindex $argv 2]
file mkdir $out
namespace eval ::plugins::mahlkonig_decent_sync {
    variable settings
    array set settings {measurement_mode volume bridge_url http://unused.invalid controller_id classic-de1}
    set session fixture-session
}
set ::plugins::mahlkonig_decent_sync::outbox $out
array set ::settings {
    settings_profile_type settings_2c profile_filename test profile_title {Quoted "café" profile}
    final_desired_shot_volume 36 final_desired_shot_volume_advanced 45
    final_desired_shot_weight_advanced 42 scale_stop_at_half_shot 0
    advanced_shot {{name preinfusion seconds 4} {name pour seconds 60}}
    espresso_count 1987 espresso_clock 12345 espresso_last_time 22
    pressure_end 6 maximum_flow 0 insert_preinfusion_pause 0
}
array set ::de1 {pour_volume 45 scale_device_handle 0 scale_sensor_weight 0}
proc state {old oldsub new sub seconds} {
    ::plugins::mahlkonig_decent_sync::state_changed [dict create previous_state $old previous_substate $oldsub \
        this_state $new this_substate $sub event_time $seconds]
}
if {$scenario eq "scale" || $scenario eq "stale_scale" || $scenario eq "half_scale"} {
    set ::plugins::mahlkonig_decent_sync::settings(measurement_mode) scale
    set ::de1(scale_device_handle) 2
    set ::de1(scale_sensor_weight) 42.4
    if {$scenario ne "stale_scale"} {::plugins::mahlkonig_decent_sync::scale_updated de1 scale_sensor_weight write}
    if {$scenario eq "half_scale"} {set ::settings(scale_stop_at_half_shot) 1}
}
if {$scenario eq "counters"} {
    set initial [::plugins::mahlkonig_decent_sync::profile]
    incr ::settings(espresso_count)
    set ::settings(espresso_clock) 456789
    set ::settings(espresso_last_time) 31
    if {[dict get [::json::json2dict $initial] revision] ne [dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision]} {error "Counter changed execution revision"}
}
if {$scenario eq "cycle"} {
    set p [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]]
    set ::plugins::mahlkonig_decent_sync::cycle_offer [dict create cycle_id cycle-1 grind_uuid grind-1 session_id fixture-session profile_revision [dict get $p revision] expires_at [expr {[clock seconds]+290.125}]]
}
state Idle ready Espresso heating 1000
if {$scenario eq "cycle"} {
    set ::plugins::mahlkonig_decent_sync::cycle_offer [dict create cycle_id cycle-2]
}
if {$scenario eq "early_abort"} {
    state Espresso heating Idle ready 1002
    state Idle ready Espresso heating 1005
    state Espresso heating Espresso pouring 1009
    state Espresso pouring Idle ready 1033
} else {
    state Espresso heating Espresso preinfusion 1001
    state Espresso preinfusion Espresso pouring 1004
    if {$scenario ni {manual malformed}} {
        set notify [expr {$scenario in {scale stale_scale half_scale} ? "saw_stop" : "sav_stop"}]
        ::plugins::mahlkonig_decent_sync::stop_notification [list notify $notify] enter
    }
    if {$scenario eq "malformed"} {set ::de1(pour_volume) not-a-number}
    state Espresso pouring Espresso ending 1028
    state Espresso ending Idle ready 1029
}
foreach path [lsort [glob -directory $out *.json]] {
    set fd [open $path r];fconfigure $fd -encoding utf-8;puts [read $fd];close $fd
}
