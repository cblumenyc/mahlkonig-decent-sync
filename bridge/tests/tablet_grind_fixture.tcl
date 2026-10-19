# Exercise real extension callbacks with the installed skin's variable and
# button-binding contracts. No machine commands or network services exist here.
source [lindex $argv 0]
source [file join [file dirname [info script]] classic_profile_vars.tcl]
package require json
package require sha256
set out [lindex $argv 1]
file mkdir $out
set scenario [lindex $argv 2]
array set ::settings {skin {Streamline Dark} settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_weight 36 final_desired_shot_volume 0 grinder_setting 7.5 profile_grinder_setting 7.5}
array set ::plugins::mahlkonig_decent_sync::settings {bridge_url http://192.168.10.2 controller_id classic-de1 measurement_mode auto grams_per_ml 1.0 fallback_policy during_shot show_popups 0 show_badge 1 appearance dark}
namespace eval ::plugins::DYE {variable settings;array set settings {next_grinder_setting 7.5}}
set ::saved 0
proc save_plugin_settings {args} {incr ::saved}
proc save_profile_and_update_de1 {args} {incr ::saved}
proc ifexists {name} {upvar #0 $name v;return $v}
set ::plugins::mahlkonig_decent_sync::outbox $out
set payload {{"event_id":"original","cycle":{"cycle_id":"frozen"}}}
set fd [open [file join $out queued.json] w];puts -nonewline $fd $payload;close $fd
set original_settings [lsort [array get ::settings]]
set original_plugin [lsort [array get ::plugins::mahlkonig_decent_sync::settings]]
set original_profile [::plugins::mahlkonig_decent_sync::profile]
namespace eval ::dui::page {
    variable pages_data
    proc add_variable {page id code} {
        variable pages_data
        set i [lsearch -exact $pages_data($page,variables) $id]
        set pages_data($page,variables) [lreplace $pages_data($page,variables) [expr {$i+1}] [expr {$i+1}] $code]
    }
}
set original_code {[ifexists ::settings(grinder_setting)]}
set ::dui::page::pages_data(off,variables) [list 101 $original_code 104 {$::settings(final_desired_shot_weight)}]
foreach page {espresso water flush hotwaterrinse steam} {set ::dui::page::pages_data($page,variables) [list 102 $original_code]}
set ::dui::page::pages_data(history,variables) [list 301 $original_code]
set ::items [dict create 101 {p:off} 102 {p:espresso p:water p:flush p:hotwaterrinse p:steam} 104 {p:off} \
    201 {p:off editor} 202 {p:off streamline_minus_grind_btn} 203 {p:off streamline_plus_grind_btn} \
    204 {p:off dose_editor} 205 {p:streamline_entry streamline_plus_grind_btn0} \
    206 {p:espresso streamline_minus_grind_btn-lbl}]
set ::bindings [dict create \
    201 [dict create <ButtonPress-1> {ask_for_data_entry_number GRIND [ifexists ::settings(grinder_setting)] ::settings(grinder_setting)}] \
    202 [dict create <ButtonPress-1> {longpress_press streamline_minus_grind_btn {streamline_adjust_grind -}} <ButtonRelease-1> {longpress_unpress streamline_minus_grind_btn {streamline_adjust_grind --}}] \
    203 [dict create <ButtonPress-1> {streamline_adjust_grind ++}] \
    204 [dict create <ButtonPress-1> {ask_for_data_entry_number DOSE ::settings(grinder_dose_weight)}] \
    205 [dict create <ButtonPress-1> {streamline_entry_page_button 0}]]
set original_bindings $::bindings
set ::options {}
proc .can {operation id args} {
    if {$operation eq "gettags" && $id eq ".zoom"} {return {p:off_zoomed p:espresso_zoomed}}
    if {$operation ne "bind"} {error "unsupported canvas operation $operation"}
    if {![llength $args]} {
        if {![dict exists $::bindings $id]} {return {}}
        return [dict keys [dict get $::bindings $id]]
    }
    set event [lindex $args 0]
    if {[llength $args]==1} {return [dict get $::bindings $id $event]}
    dict set ::bindings $id $event [lindex $args 1]
}
proc winfo {operation widget} {return [expr {$widget eq ".zoom"}]}
proc .zoom {operation args} {
    if {$operation eq "tag"} {return $::zoom_range}
    if {$operation eq "delete"} {
        if {[lindex $args 0] eq "1.0"} {set ::zoom_text "";return}
        lassign $args start end
        set ::zoom_text [string replace $::zoom_text $start [expr {$end-1}]]
        return
    }
    if {$operation eq "insert"} {
        lassign $args start text tag
        if {$start eq "end"} {set start [string length $::zoom_text]}
        set ::zoom_text "[string range $::zoom_text 0 [expr {$start-1}]]$text[string range $::zoom_text $start end]"
        if {$tag eq "grind_tag"} {set ::zoom_range [list $start [expr {$start+[string length $text]}]]}
        return
    }
    error {unexpected zoom widget operation}
}
proc ::refresh_.zoom {args} {
    .zoom delete 1.0 end
    .zoom insert end "Grind " label_tag
    .zoom insert end "$::settings(grinder_setting)" grind_tag
    .zoom insert end "    Dose 18 g" dose_tag
}
proc dui {family operation args} {
    if {$family ne "item"} {error {unexpected DUI family}}
    if {$operation eq "get"} {
        lassign $args page pattern
        set ids {}
        dict for {id tags} $::items {
            if {"p:$page" ni $tags} {continue}
            foreach tag $tags {if {[string match $pattern $tag]} {lappend ids $id;break}}
        }
        return $ids
    }
    if {$operation eq "config"} {
        foreach {key value} [lrange $args 1 end] {dict set ::options [lindex $args 0] $key $value}
        return
    }
    error "unsupported DUI item $operation"
}
proc display {reading {connected true}} {
    ::plugins::mahlkonig_decent_sync::update_display [dict create display [dict create grinder_connected $connected \
        sync_ready true active_cycle null cycle_expires_at null notice null grind_position $reading]]
}
if {$scenario eq "readings"} {
    display [dict create um 99.9 age_s 3]
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne "99.9 µm"} {error {wrong measured position}}
    display [dict create um 0 age_s 0]
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne "0.0 µm"} {error {zero position lost}}
    foreach reading [list null {} [dict create um 9] [dict create um 9 age_s 35] [dict create um 9 age_s -1] \
            [dict create um NaN age_s 1] [dict create um Inf age_s 1] [dict create um -1 age_s 1] \
            [dict create um 99.9 age_s NaN] [dict create um {1;error injected} age_s 1]] {
        display $reading
        if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne ""} {error {invalid reading was displayed}}
    }
    display [dict create um 99.9 age_s 1] false
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne ""} {error {offline position remains}}
    display [dict create um 99.9 age_s 1]
    set ::plugins::mahlkonig_decent_sync::last_server_ack [expr {[clock seconds]-11}]
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne ""} {error {stale server position remains}}
    display [dict create um 99.9 age_s 34.5]
    if {$::plugins::mahlkonig_decent_sync::grind_position_deadline-[clock milliseconds]>500} {error {reading freshness reset}}
    set ::plugins::mahlkonig_decent_sync::grind_position_deadline [clock milliseconds]
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne ""} {error {expired sensor position remains}}
    display [dict create um 99.9 age_s 1]
    ::plugins::mahlkonig_decent_sync::update_display {}
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne ""} {error {old server data reused}}
} elseif {$scenario eq "skin"} {
    set ::settings(skin) Streamline
    ::plugins::mahlkonig_decent_sync::install_grind_display
    if {$::bindings ne $original_bindings || $::options ne ""} {error {light skin was changed}}
    set ::streamline_dark_mode 1
    ::plugins::mahlkonig_decent_sync::install_grind_display
    ::plugins::mahlkonig_decent_sync::install_grind_display
    foreach page {off espresso water flush hotwaterrinse steam} {
        if {[lindex $::dui::page::pages_data($page,variables) 1] ne {[::plugins::mahlkonig_decent_sync::grind_position_label]}} {error "readout not replaced on $page"}
    }
    if {[lindex $::dui::page::pages_data(history,variables) 1] ne $original_code} {error {history was changed}}
    if {[dict get $::options 101 -font] ne "Inter-Bold12"} {error {readout font does not fit}}
    foreach id {201 202 203} {
        dict for {event command} [dict get $::bindings $id] {if {$command ne ""} {error {grind editor remains active}}}
    }
    foreach id {204 205} {
        if {[dict get $::bindings $id] ne [dict get $original_bindings $id]} {error {unrelated editor disabled}}
    }
    foreach id {202 203 206} {
        if {[dict get $::options $id -initial_state] ne "hidden" || [dict get $::options $id -state] ne "hidden"} {error {grind controls remain visible}}
    }
    set ::settings(skin) {Streamline Dark}
} elseif {$scenario eq "zoom"} {
    display [dict create um 99.9 age_s 1]
    ::plugins::mahlkonig_decent_sync::install_grind_display
    ::plugins::mahlkonig_decent_sync::install_grind_display
    ::refresh_.zoom
    if {$::zoom_text ne "Grind 99.9 µm    Dose 18 g"} {error {zoomed position or other fields changed}}
    display null
    ::refresh_.zoom
    if {$::zoom_text ne "Grind     Dose 18 g"} {error {zoomed field did not clear quietly}}
    if {[llength [trace info execution ::refresh_.zoom]] != 1} {error {zoom refresh installed twice}}
} elseif {$scenario eq "preservation"} {
    ::plugins::mahlkonig_decent_sync::install_grind_display
    display [dict create um 99.9 age_s 1]
    set ::plugins::mahlkonig_decent_sync::sync_ready 0
    if {[::plugins::mahlkonig_decent_sync::grind_position_label] ne "99.9 µm"} {error {valid position incorrectly depends on feedback readiness}}
    if {[::plugins::mahlkonig_decent_sync::profile] ne $original_profile} {error {live position changed profile revision}}
} else {error {unknown scenario}}
set fd [open [file join $out queued.json] r];set retained [read $fd];close $fd
if {$retained ne $payload || [llength [glob $out/*.json]]!=1} {error {queued events changed}}
if {$::saved || [lsort [array get ::settings]] ne $original_settings || [lsort [array get ::plugins::mahlkonig_decent_sync::settings]] ne $original_plugin || $::plugins::DYE::settings(next_grinder_setting) ne "7.5"} {error {saved recipe or preferences changed}}
puts {Read-only measured grind, skin controls and preserved settings/history passed}
