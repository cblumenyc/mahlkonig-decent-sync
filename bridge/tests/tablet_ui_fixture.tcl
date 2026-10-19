source [lindex $argv 0]
package require json
set out [lindex $argv 1]
file mkdir $out
set ::plugins::mahlkonig_decent_sync::outbox $out
array set ::plugins::mahlkonig_decent_sync::settings {bridge_url http://sync.local controller_id classic-de1 server_id {} show_badge 1 show_popups 1 measurement_mode auto}
set ::saved 0
proc save_plugin_settings {args} {incr ::saved}
proc popup {message} {lappend ::popups $message}
set ::popups {}
set ::plugins::mahlkonig_decent_sync::session ui-session
foreach {input expected} {sync.local http://sync.local 192.168.10.2:8080 http://192.168.10.2:8080 http://test.local/ http://test.local} {
    if {[::plugins::mahlkonig_decent_sync::normalize_server $input] ne $expected} {error "server normalization failed"}
}
foreach input {{} {http://name/path} {http://user:pass@name} {https://name} {name:99999} {name:0} {name?query} {name;command}} {
    if {![catch {::plugins::mahlkonig_decent_sync::normalize_server $input}]} {error "invalid server accepted: $input"}
}
if {[::plugins::mahlkonig_decent_sync::badge_label] ne "Mahlkönig" || [::plugins::mahlkonig_decent_sync::badge_connected]} {error "offline badge incorrect"}
set body [dict create display [dict create sync_ready true grinder_connected true active_cycle null cycle_expires_at null notice [dict create id shot1:matched title {Shot matched} detail {Grind 1}]]]
::plugins::mahlkonig_decent_sync::update_display $body
::plugins::mahlkonig_decent_sync::update_display $body
if {[llength $::popups] || $::plugins::mahlkonig_decent_sync::banner_record ne ""} {error "routine matching notification shown"}
if {[::plugins::mahlkonig_decent_sync::connection_label] ne "Server connected · Grinder connected"} {error "connection badge wrong"}
if {![::plugins::mahlkonig_decent_sync::badge_connected]} {error "connected badge wrong"}
dict set body display grinder_connected false
dict set body display notice [dict create id shot1:delivering title {Updating grinder} detail {Saved result}]
::plugins::mahlkonig_decent_sync::update_display $body
if {[llength $::popups] || $::plugins::mahlkonig_decent_sync::banner_record ne ""} {error "routine delivery notification shown"}
dict set body display notice [dict create id shot1:verified title {Grinder received shot} detail {Setting held}]
::plugins::mahlkonig_decent_sync::update_display $body
if {[dict get $::plugins::mahlkonig_decent_sync::banner_record text] ne "Shot received"} {error "completion banner missing"}
set original_banner $::plugins::mahlkonig_decent_sync::banner_record
::plugins::mahlkonig_decent_sync::update_display $body
if {$::plugins::mahlkonig_decent_sync::banner_record ne $original_banner} {error "duplicate banner restarted"}
::plugins::mahlkonig_decent_sync::toggle_display show_popups
dict set body display notice [dict create id shot2:verified title {Grinder received shot} detail {Setting held}]
::plugins::mahlkonig_decent_sync::update_display $body
if {[llength $::popups] || $::plugins::mahlkonig_decent_sync::banner_record ne ""} {error "disabled banner shown"}
::plugins::mahlkonig_decent_sync::toggle_display show_badge
if {[::plugins::mahlkonig_decent_sync::badge_label] ne ""} {error "badge toggle failed"}
set ::plugins::mahlkonig_decent_sync::server_input other.local
set ::plugins::mahlkonig_decent_sync::active_cycle active
set ::plugins::mahlkonig_decent_sync::active_cycle_expires [expr {[clock seconds]+290}]
::plugins::mahlkonig_decent_sync::save_server
if {![string match {Finish the current cycle*} $::plugins::mahlkonig_decent_sync::status_text]} {error "server changed mid-cycle"}
set ::plugins::mahlkonig_decent_sync::active_cycle ""
set fd [open [file join $out queued.json] w];puts $fd {};close $fd
::plugins::mahlkonig_decent_sync::save_server
if {$::plugins::mahlkonig_decent_sync::settings(bridge_url) ne "http://sync.local"} {error "queued events moved to another server"}
# Use real request handling elsewhere; here check the callback's verification/save contract.
file delete [file join $out queued.json]
namespace eval ::http {
    proc status {token} {return ok}
    proc ncode {token} {return 200}
    proc data {token} {return {{"product":"mahlkonig-decent-sync","server_id":"11111111-1111-1111-1111-111111111111","controller_id":"classic-de1","controller_contracts":[1,2]}}}
    proc cleanup {token} {}
}
::plugins::mahlkonig_decent_sync::server_checked http://other.local 1 fake-token
if {$::plugins::mahlkonig_decent_sync::settings(bridge_url) ne "http://other.local"} {error "server was not saved"}
if {$::saved!=3} {error "preferences were not persisted"}
rename save_plugin_settings old_save
proc save_plugin_settings {args} {error {disk full}}
::plugins::mahlkonig_decent_sync::server_checked http://third.local 1 fake-token
if {$::plugins::mahlkonig_decent_sync::settings(bridge_url) ne "http://other.local"} {error "failed save changed destination"}
puts {UI preferences, server editing, and essential banner deduplication passed}

# Exercise integration with the skin's existing refresh callback: the field is
# appended after Weight on the same widget, once per refresh, with native tags.
array set ::settings {skin {Streamline Dark}}
set ::plugins::mahlkonig_decent_sync::settings(show_badge) 1
set ::progress_bar_red red
set ::progress_bar_green green
set ::dataline_label_color normal
proc winfo {op widget} {return [expr {$widget eq ".row"}]}
proc .can {args} {return {690 330}}
proc font {args} {return 10}
proc rescale_x_skin {x} {return $x}
proc .row {op args} {
    switch $op {
        delete {set ::row_text ""}
        insert {append ::row_text [lindex $args 1]}
        cget {return Inter-Bold18}
        tag {dict set ::row_tags [lindex $args 1] [lrange $args 2 end]}
        configure {set ::row_options $args}
    }
}
proc ::refresh_.row {} {
    # group_head_heater_temperature time_format: actual skin-row identification
    .row delete 1.0 end
    .row insert end {Group 90  Calib 1.0  Weight 36g}
}
set ::plugins::mahlkonig_decent_sync::last_server_ack [clock seconds]
set ::plugins::mahlkonig_decent_sync::grinder_connected 1
set ::plugins::mahlkonig_decent_sync::sync_ready 1
::plugins::mahlkonig_decent_sync::install_badge
::plugins::mahlkonig_decent_sync::install_badge
::refresh_.row
if {$::row_text ne "Group 90  Calib 1.0  Weight 36g    Mahlkönig ●"} {error "not inline or duplicated"}
if {[dict get $::row_tags coffee_sync_label -font] ne "Inter-Bold18"} {error "wrong label font"}
if {[dict get $::row_tags coffee_sync_dot -foreground] ne "green"} {error "wrong dot color"}
set ::plugins::mahlkonig_decent_sync::sync_ready 0
::refresh_.row
if {[dict get $::row_tags coffee_sync_label -foreground] ne "red" || [string first ● $::row_text]>=0} {error "connected but paused must be red"}
set ::plugins::mahlkonig_decent_sync::sync_ready 1
set ::plugins::mahlkonig_decent_sync::grinder_connected 0
::refresh_.row
if {$::row_text ne "Group 90  Calib 1.0  Weight 36g    Mahlkönig" || [dict get $::row_tags coffee_sync_label -foreground] ne "red"} {error "offline grinder indicator wrong"}
set ::plugins::mahlkonig_decent_sync::grinder_connected 1
set ::plugins::mahlkonig_decent_sync::last_server_ack [expr {[clock seconds]-20}]
::refresh_.row
if {[dict get $::row_tags coffee_sync_label -foreground] ne "red" || [string first ● $::row_text]>=0} {error "stale server still green"}
set ::plugins::mahlkonig_decent_sync::settings(show_badge) 0
::refresh_.row
if {$::row_text ne "Group 90  Calib 1.0  Weight 36g"} {error "disabled badge remains"}
puts {Inline skin status, refresh deduplication, and disconnection colors passed}
