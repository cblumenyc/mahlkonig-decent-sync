# Actual extension callbacks against the documented DUI item/entry contract.
# No network, machine commands or production files are available in this fixture.
source [lindex $argv 0]
source [file join [file dirname [info script]] classic_profile_vars.tcl]
package require json
package require sha256
set out [lindex $argv 1]
file mkdir $out
set ::plugins::mahlkonig_decent_sync::outbox $out
set payload {{"event_id":"existing-id","cycle":{"cycle_id":"original-cycle"}}}
set fd [open [file join $out queued.json] w];puts -nonewline $fd $payload;close $fd
array set ::settings {skin Streamline settings_profile_type settings_2a profile_filename default profile_title Default final_desired_shot_weight 36 final_desired_shot_volume 0}
array set ::plugins::mahlkonig_decent_sync::settings {bridge_url http://192.168.10.2 controller_id classic-de1 retained_user_preference 1 measurement_mode auto grams_per_ml 1.0 fallback_policy during_shot show_badge 0 show_popups 1 user_setting keep-me}
set ::plugins::mahlkonig_decent_sync::server_input http://192.168.10.2
set ::saved 0
set ::items {}
set ::actions {}
set ::bindings {}
set ::buttons {}
set ::entry_config {}
proc save_plugin_settings {name} {
    incr ::saved
    set ::persisted [array get ::plugins::mahlkonig_decent_sync::settings]
}
proc rescale_x_skin {x} {return [expr {$x * 0.5}]}
source [file join [file dirname [info script]] classic_image_fixture.tcl]
proc add_de1_text {page x y args} {
    array set opts $args
    if {[info exists opts(-textvariable)]} {error {canvas text does not accept -textvariable}}
    dict set ::items [lindex $opts(-tags) 0] [list page $page type text tags $opts(-tags) options $args]
}
proc add_de1_variable {page x y args} {
    if {[lindex $args end-1] ne "-textvariable"} {error {variable code must be last}}
    array set opts $args
    dict set ::bindings [lindex $opts(-tags) 0] $opts(-textvariable)
    dict set ::items [lindex $opts(-tags) 0] [list page $page type text tags $opts(-tags) options [lrange $args 0 end-2]]
}
proc add_de1_button {page command x0 y0 x1 y1 args} {
    lappend ::buttons [list $command $x0 $y0 $x1 $y1]
}
proc .panels_entry {op args} {
    if {$op ne "configure"} {error {unsupported entry operation}}
    foreach {key value} $args {dict set ::entry_config $key $value}
}
proc dui {family operation args} {
    if {$family eq "page"} {
        if {$operation eq "add_action"} {lappend ::actions $args}
        return
    }
    if {$family eq "add"} {
        set opts {}
        if {$operation eq "shape"} {
            if {[lindex $args 0] ne "round"} {error {unsupported shape}}
            set opts [lrange $args 4 end]
            set type shape
        } elseif {$operation eq "canvas_item"} {
            set type [lindex $args 0]
            set opts [lrange $args [expr {$type eq "image" ? 4 : 6}] end]
            if {$type eq "image"} {
                array set image_options $opts
                if {![dict exists $::logo_scales $image_options(-image)]} {error {unscaled or deferred logo}}
            }
        } elseif {$operation eq "entry"} {
            set opts [lrange $args 3 end]
            array set options $opts
            if {$options(-textvariable) ne "::plugins::mahlkonig_decent_sync::server_input"} {error {entry rebound}}
            return .panels_entry
        } else {error "unsupported DUI add: $operation"}
        array set options $opts
        set tags $options(-tags)
        set tag [lindex $tags 0]
        if {[dict exists $::items $tag]} {error "duplicate UI tag $tag"}
        dict set ::items $tag [list page [lindex $args 1] type $type tags $tags options $opts]
        return $tag
    }
    if {$family eq "item" && $operation eq "config"} {
        lassign $args page tag
        foreach key [dict keys $::items] {
            if {[dict get $::items $key page] ne $page} {continue}
            if {$tag ni [dict get $::items $key tags]} {continue}
            foreach {option value} [lrange $args 2 end] {
                if {$option ne "-fill"} {error "unexpected state/geometry mutation: $option"}
                dict set ::items $key fill $value
            }
        }
        return
    }
    error "unsupported DUI command: $family $operation"
}
proc fill {tag} {return [dict get $::items $tag fill]}
proc bound {tag} {return [subst [dict get $::bindings $tag]]}
set original [array get ::plugins::mahlkonig_decent_sync::settings]
set revision [dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision]
::plugins::mahlkonig_decent_sync::build_ui
if {[dict get $::logo_scales plugin_mahlkonig_decent_sync-cs_logo] ne {0.5 0.5}} {error {logo not scaled for the tablet}}
if {$::saved!=0 || [array get ::plugins::mahlkonig_decent_sync::settings] ne $original} {error {building settings mutated preferences}}
foreach button $::buttons {
    lassign $button command x0 y0 x1 y1
    if {$x1-$x0<100 || $y1-$y0<100 || $x0<0 || $y0<0 || $x1>2560 || $y1>1600} {error {invalid touch target}}
}
if {[llength $::actions]!=6} {error {settings/banner/update page refresh hooks missing}}
foreach pair {{Streamline light} {{Streamline Dark} dark} {{Insight Dark} dark} {Other light}} {
    lassign $pair ::settings(skin) expected
    if {[::plugins::mahlkonig_decent_sync::appearance_mode] ne $expected} {error "Auto chose wrong appearance for $::settings(skin)"}
}
set ::settings(skin) Streamline
set ::streamline_dark_mode 1
if {[::plugins::mahlkonig_decent_sync::appearance_mode] ne "dark"} {error {skin dark flag ignored}}
set ::streamline_dark_mode 0
foreach mode {dark light auto} {
    ::plugins::mahlkonig_decent_sync::select_appearance $mode
    if {[dict get $::persisted appearance] ne $mode} {error {appearance was not saved}}
    foreach {key expected} $original {
        if {$::plugins::mahlkonig_decent_sync::settings($key) ne $expected} {error "appearance changed existing setting $key"}
    }
    if {[dict get [::json::json2dict [::plugins::mahlkonig_decent_sync::profile]] revision] ne $revision} {error {appearance changed shot identity}}
    set p [::plugins::mahlkonig_decent_sync::settings_palette]
    if {[fill cs_settings_bg] ne [dict get $p bg] || [dict get $::entry_config -foreground] ne [dict get $p ink]} {error {palette or entry failed to update}}
    if {[fill cs_appearance_${mode}_fill] ne [dict get $p accent]} {error {appearance selection not visible}}
}
set ::settings(skin) {Streamline Dark}
::plugins::mahlkonig_decent_sync::select_appearance light
if {[::plugins::mahlkonig_decent_sync::appearance_mode] ne "light"} {error {Light did not override dark skin}}
set saves $::saved
rename save_plugin_settings working_save
proc save_plugin_settings {name} {error {disk full}}
::plugins::mahlkonig_decent_sync::select_appearance dark
if {$::plugins::mahlkonig_decent_sync::settings(appearance) ne "light" || [bound cs_settings_notice] ne "Save failed"} {error {failed save changed appearance or hid failure}}
rename save_plugin_settings {}
rename working_save save_plugin_settings
set body [dict create display [dict create sync_ready true sync_warning null grinder_connected true active_cycle null cycle_expires_at null notice null]]
::plugins::mahlkonig_decent_sync::update_display $body
::plugins::mahlkonig_decent_sync::refresh_settings_ui
if {[bound cs_sync_text] ne "Ready" || [bound cs_server_state] ne "Online" || [bound cs_grinder_state] ne "Online"} {error {healthy status incorrect}}
dict set body display sync_ready false
dict set body display sync_warning grinder_press_required
::plugins::mahlkonig_decent_sync::update_display $body
::plugins::mahlkonig_decent_sync::refresh_settings_ui
if {[bound cs_sync_text] ne "Press required" || [fill cs_sync_text] ne [dict get [::plugins::mahlkonig_decent_sync::settings_palette] warning]} {error {connected but blocked status not red}}
dict unset body display sync_ready
::plugins::mahlkonig_decent_sync::update_display $body
if {[::plugins::mahlkonig_decent_sync::badge_connected]} {error {missing readiness became green}}
set ::plugins::mahlkonig_decent_sync::last_server_ack [expr {[clock seconds]-11}]
::plugins::mahlkonig_decent_sync::refresh_settings_ui
if {[bound cs_sync_text] ne "Server offline" || [bound cs_grinder_state] ne "Unknown"} {error {stale connectivity not shown}}
set ::plugins::mahlkonig_decent_sync::last_server_ack [clock seconds]
dict set body display sync_ready true
dict set body display sync_warning null
::plugins::mahlkonig_decent_sync::update_display $body
::plugins::mahlkonig_decent_sync::toggle_display show_badge
::plugins::mahlkonig_decent_sync::refresh_settings_ui
if {[fill cs_badge_right] ne [dict get [::plugins::mahlkonig_decent_sync::settings_palette] thumb]} {error {switch did not track saved preference}}
set fd [open [file join $out queued.json] r];set actual [read $fd];close $fd
if {$actual ne $payload || $::plugins::mahlkonig_decent_sync::sequence!=0 || $::plugins::mahlkonig_decent_sync::session ne ""} {error {UI modified queued history or event state}}
foreach tag [dict keys $::items] {
    set record [dict get $::items $tag]
    if {[dict get $record type] ne "text"} {continue}
    set options [dict get $record options]
    set i [lsearch -exact $options -text]
    if {$i<0} {continue}
    set text [lindex $options [expr {$i+1}]]
    set limit [expr {[string match cs_up_about* $tag] ? 45 : 30}]
    if {[string first "\n" $text]>=0 || [string length $text]>$limit} {error "prose in settings: $text"}
}
puts {Panels appearance, status, persistence, touch targets and preserved outbox passed}
