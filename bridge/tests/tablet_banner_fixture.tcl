# Exercise actual banner callbacks without any machine/network commands.
source [lindex $argv 0]
package require json
package require sha256
source [file join [file dirname [info script]] classic_profile_vars.tcl]
set out [lindex $argv 1]
set scenario [lindex $argv 2]
file mkdir $out
set ::plugins::mahlkonig_decent_sync::outbox $out
array set ::plugins::mahlkonig_decent_sync::settings {bridge_url http://192.168.10.2 controller_id classic-de1 measurement_mode auto grams_per_ml 1.0 fallback_policy during_shot appearance auto show_popups 1 show_badge 1}
array set ::settings {skin Streamline settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_weight 36 final_desired_shot_volume 0}
set ::native_popups {}
proc popup {message} {lappend ::native_popups $message}
set original_popup [info body popup]
set ::saves 0
proc save_plugin_settings {plugin} {incr ::saves;set ::saved [array get ::plugins::mahlkonig_decent_sync::settings]}
set ::canvas_items [dict create decent_existing [dict create type text options {-tags decent_existing -text Native}]]
set ::item_id 0
set ::bindings {}
set ::actions {}
proc winfo {op widget} {return [expr {$widget eq ".can"}]}
proc .can {operation args} {
    switch -- $operation {
        delete {
            if {$args ne "coffee_sync_banner"} {error "deleted Decent canvas items: $args"}
            foreach id [dict keys $::canvas_items] {
                if {$id ne "decent_existing"} {dict unset ::canvas_items $id}
            }
        }
        create {
            set kind [lindex $args 0];set args [lrange $args 1 end];set coords {}
            while {[llength $args] && [string is double -strict [lindex $args 0]]} {
                lappend coords [lindex $args 0];set args [lrange $args 1 end]
            }
            array set opts $args
            if {$opts(-tags) ne "coffee_sync_banner"} {error {unscoped overlay}}
            dict set ::canvas_items [incr ::item_id] [list type $kind coords $coords options $args]
            return $::item_id
        }
        bind {dict set ::bindings [lindex $args 0] [lrange $args 1 end]}
        raise {if {$args ne "coffee_sync_banner"} {error {raised another UI item}}}
        default {error "unexpected canvas operation $operation"}
    }
}
proc dui {family operation args} {
    if {$family eq "platform"} {return [expr {[lindex $args 0]/2.0}]}
    if {$family eq "item" && $operation eq "config"} {return}
    if {$family eq "page" && $operation eq "add_action"} {lappend ::actions $args;return}
    error "unexpected DUI call $family $operation"
}
proc status {shot phase {ready true}} {
    set notice [dict create id $shot:$phase title {Long native title} detail {A full paragraph that must never enter the banner.}]
    set body [dict create display [dict create sync_ready $ready sync_warning null grinder_connected true active_cycle null cycle_expires_at null notice $notice]]
    ::plugins::mahlkonig_decent_sync::update_display $body
}
proc banner_text {} {
    if {$::plugins::mahlkonig_decent_sync::banner_record eq ""} {return ""}
    return [dict get $::plugins::mahlkonig_decent_sync::banner_record text]
}
set queued {{"event_id":"preserved-event","cycle":{"cycle_id":"preserved-cycle"}}}
set fd [open [file join $out queued.json] w];puts -nonewline $fd $queued;close $fd
set revision [dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision]
set original [array get ::plugins::mahlkonig_decent_sync::settings]
switch -- $scenario {
    flow {
        foreach phase {matched delivering validating movement_checking} {status quiet $phase}
        if {[banner_text] ne ""} {error {progress became a notification}}
        status shot1 awaiting_arm false
        if {[banner_text] ne "Press grinder"} {error {arming instruction missing}}
        if {![dict get $::plugins::mahlkonig_decent_sync::banner_record sticky] || $::plugins::mahlkonig_decent_sync::banner_timer ne ""} {error {action banner is not persistent}}
        set serial $::plugins::mahlkonig_decent_sync::banner_serial
        status shot1 awaiting_arm false
        if {$serial!=$::plugins::mahlkonig_decent_sync::banner_serial} {error {repeated action banner}}
        status shot1 delivering false
        if {[banner_text] ne ""} {error {resolved press instruction remains}}
        status shot1 movement_checking
        if {[banner_text] ne ""} {error {normal movement verification raised a banner}}
        status shot1 movement_pending false
        if {[banner_text] ne "Movement unconfirmed"} {error {persistent movement warning missing}}
        status shot1 within_tolerance
        if {[banner_text] ne "Shot received" || [dict get $::plugins::mahlkonig_decent_sync::banner_record sticky]} {error {receipt classification wrong}}
        set serial $::plugins::mahlkonig_decent_sync::banner_serial
        status shot1 adjusted
        if {$serial!=$::plugins::mahlkonig_decent_sync::banner_serial} {error {outcome reclassification repeated receipt}}
        status shot2 awaiting_arm false
        if {[banner_text] ne "Press grinder"} {error {replacement did not take over}}
        status shot1 adjusted
        if {[banner_text] ne "Press grinder"} {error {older repeated phase hid current action}}
        ::plugins::mahlkonig_decent_sync::clear_banner
        status shot2 awaiting_arm false
        if {[banner_text] ne ""} {error {dismissed warning reappeared}}
        status shot3 history_only false
        if {[banner_text] ne "Feedback missed"} {error {missing feedback warning absent}}
        status shot3 history_only true
        if {[banner_text] ne ""} {error {healthy sync did not clear warning}}
    }
    expiry {
        status shot1 verified
        set old $::plugins::mahlkonig_decent_sync::banner_serial
        status shot2 awaiting_arm false
        ::plugins::mahlkonig_decent_sync::expire_banner $old
        if {[banner_text] ne "Press grinder"} {error {stale timer hid replacement}}
        ::plugins::mahlkonig_decent_sync::preview_banner
        if {[banner_text] ne "Press grinder"} {error {preview hid action}}
        status shot2 verified
        dict set ::plugins::mahlkonig_decent_sync::banner_record started [expr {[clock milliseconds]-4100}]
        ::plugins::mahlkonig_decent_sync::expire_banner $::plugins::mahlkonig_decent_sync::banner_serial
        if {[banner_text] ne "" || $::plugins::mahlkonig_decent_sync::banner_timer ne ""} {error {success did not expire}}
        status shot3 awaiting_arm false
        ::plugins::mahlkonig_decent_sync::expire_banner $::plugins::mahlkonig_decent_sync::banner_serial
        if {[banner_text] ne "Press grinder"} {error {persistent warning expired}}
    }
    preferences {
        ::plugins::mahlkonig_decent_sync::save_banner_preference banner_position bottom_center
        ::plugins::mahlkonig_decent_sync::save_banner_preference banner_duration_s 7
        if {[dict get $::saved banner_position] ne "bottom_center" || [dict get $::saved banner_duration_s]!=7} {error {preference not persisted}}
        foreach {key value} $original {if {$::plugins::mahlkonig_decent_sync::settings($key) ne $value} {error "changed existing setting $key"}}
        set saves $::saves
        foreach value {0 1 16 abc 3.5 Inf NaN} {::plugins::mahlkonig_decent_sync::save_banner_preference banner_duration_s $value}
        ::plugins::mahlkonig_decent_sync::save_banner_preference banner_position sideways
        if {$::saves!=$saves} {error {invalid option persisted}}
        rename save_plugin_settings successful_save
        proc save_plugin_settings {plugin} {error {disk full}}
        ::plugins::mahlkonig_decent_sync::save_banner_preference banner_position top_right
        ::plugins::mahlkonig_decent_sync::save_banner_preference banner_duration_s 8
        if {[::plugins::mahlkonig_decent_sync::banner_position] ne "bottom_center" || [::plugins::mahlkonig_decent_sync::banner_duration]!=7 || [::plugins::mahlkonig_decent_sync::settings_notice] ne "Save failed"} {error {failed save changed preference}}
        rename save_plugin_settings {}
        rename successful_save save_plugin_settings
        set ::plugins::mahlkonig_decent_sync::settings(show_popups) 0
        status shot1 verified
        if {[banner_text] ne ""} {error {disabled banner appeared}}
        ::plugins::mahlkonig_decent_sync::preview_banner
        if {[banner_text] ne "Banner preview" || $::plugins::mahlkonig_decent_sync::settings(show_popups)!=0} {error {preview changed enable preference}}
    }
    canvas {
        ::plugins::mahlkonig_decent_sync::install_banners
        ::plugins::mahlkonig_decent_sync::install_banners
        if {[llength $::actions]!=1} {error {duplicate page hook}}
        foreach theme {light dark} {
            set ::plugins::mahlkonig_decent_sync::settings(appearance) $theme
            foreach position {top_center top_right bottom_center} {
                set ::plugins::mahlkonig_decent_sync::settings(banner_position) $position
                ::plugins::mahlkonig_decent_sync::show_banner test "Shot received" success
                if {[dict size $::canvas_items]!=6} {error {overlay stacked or lost an item}}
                foreach id [dict keys $::canvas_items] {
                    if {$id eq "decent_existing"} {continue}
                    foreach {x y} [dict get $::canvas_items $id coords] {
                        if {$x<0 || $x>1280 || $y<0 || $y>800} {error {overlay outside canvas}}
                    }
                }
                set record $::plugins::mahlkonig_decent_sync::banner_record
                ::plugins::mahlkonig_decent_sync::draw_banner off espresso
                if {[dict size $::canvas_items]!=6 || $::plugins::mahlkonig_decent_sync::banner_record ne $record} {error {page navigation reset or stacked banner}}
            }
        }
        set ::plugins::mahlkonig_decent_sync::settings(show_popups) 1
        ::plugins::mahlkonig_decent_sync::toggle_display show_popups
        if {[dict size $::canvas_items]!=1 || [banner_text] ne ""} {error {disabled overlay not removed}}
    }
    default {error {unknown scenario}}
}
if {[info body popup] ne $original_popup || [llength $::native_popups]} {error {Decent popup path changed or called}}
popup {Native scale notification}
if {$::native_popups ne {{Native scale notification}} || ![dict exists $::canvas_items decent_existing]} {error {Decent UI not preserved}}
if {[dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision] ne $revision} {error {banner settings changed shot profile}}
set fd [open [file join $out queued.json] r];set actual [read $fd];close $fd
if {$actual ne $queued || $::plugins::mahlkonig_decent_sync::sequence!=0} {error {queued event/history changed}}
::plugins::mahlkonig_decent_sync::clear_banner
puts "$scenario: one banner, preserved native notifications and outbox passed"
