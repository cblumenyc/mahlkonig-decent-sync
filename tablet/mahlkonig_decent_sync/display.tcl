# Display for the Classic controller.
namespace eval ::plugins::mahlkonig_decent_sync {
    proc connection_label {} {
        variable last_server_ack;variable grinder_connected
        if {[clock seconds]-$last_server_ack > 10} {return "Server offline · events saved on tablet"}
        return [expr {$grinder_connected ? "Server connected · Grinder connected" : "Server connected · Grinder offline"}]
    }
    proc badge_label {} {
        if {![value ::plugins::mahlkonig_decent_sync::settings(show_badge) 1]} {return ""}
        return "Mahlkönig"
    }
    proc badge_connected {} {
        variable last_server_ack;variable grinder_connected;variable sync_ready
        return [expr {[clock seconds]-$last_server_ack <= 10 && $grinder_connected && $sync_ready}]
    }
    proc refresh_badge {widget args} {
        if {[badge_label] eq ""} {return}
        set color [value ::dataline_label_color #707485]
        if {![badge_connected]} {set color [value ::progress_bar_red #cd5360]}
        $widget tag configure coffee_sync_label -font Inter-Bold18 -foreground $color
        $widget tag configure coffee_sync_gap -font Inter-Bold16
        $widget tag configure coffee_sync_dot -font Inter-Bold18 -foreground [value ::progress_bar_green #58a577]
        $widget insert end "    " coffee_sync_gap
        $widget insert end [badge_label] coffee_sync_label
        if {[badge_connected]} {$widget insert end " ●" coffee_sync_dot}
    }
    proc update_display {body} {
        variable settings;variable last_server_ack;variable grinder_connected;variable sync_ready
        variable sync_warning
        variable active_cycle;variable active_cycle_expires;variable notices_seen
        set last_server_ack [clock seconds]
        set sync_ready 0
        set sync_warning ""
        if {![dict exists $body display]} {set grinder_connected 0;update_grind_position {};return}
        set display [dict get $body display]
        if {[dict exists $display sync_ready]} {set sync_ready [expr {[dict get $display sync_ready] eq "true"}]}
        if {[dict exists $display sync_warning] && [dict get $display sync_warning] ne "null"} {
            set sync_warning [dict get $display sync_warning]
        }
        set grinder_connected [expr {[dict get $display grinder_connected] eq "true"}]
        update_grind_position $display
        set active_cycle [dict get $display active_cycle]
        if {$active_cycle eq "null"} {set active_cycle ""}
        set active_cycle_expires [dict get $display cycle_expires_at]
        if {$active_cycle_expires eq "null"} {set active_cycle_expires [expr {[clock seconds]+300}]}
        reconcile_banner $display
        set n [dict get $display notice]
        if {$n eq "null" || ![dict exists $n id]} {return}
        set id [dict get $n id]
        if {$id in $notices_seen} {return}
        lappend notices_seen $id
        set notices_seen [lrange $notices_seen end-49 end]
        offer_banner $n
    }
    proc toggle_display {key} {
        variable settings;variable status_text
        if {$key ni {show_popups show_badge}} {return}
        set old $settings($key);set settings($key) [expr {!$old}]
        if {[catch {save_plugin_settings mahlkonig_decent_sync}]} {
            set settings($key) $old;set status_text "Could not save display preference"
        }
        if {$key eq "show_popups" && !$settings($key)} {clear_banner}
    }
    proc display_label {key label} {
        variable settings
        return "[expr {$settings($key) ? {●} : {○}}] $label"
    }
    proc banner_position {} {
        set position [value ::plugins::mahlkonig_decent_sync::settings(banner_position) top_center]
        if {$position ni {top_center top_right bottom_center}} {return top_center}
        return $position
    }
    proc banner_duration {} {
        set seconds [value ::plugins::mahlkonig_decent_sync::settings(banner_duration_s) 4]
        if {![string is integer -strict $seconds] || $seconds < 2 || $seconds > 15} {return 4}
        return $seconds
    }
    proc save_banner_preference {key value} {
        variable settings;variable status_text
        if {$key eq "banner_position"} {
            if {$value ni {top_center top_right bottom_center}} {return}
            set previous [banner_position]
        } elseif {$key eq "banner_duration_s"} {
            if {![string is integer -strict $value] || $value < 2 || $value > 15} {return}
            set previous [banner_duration]
        } else {return}
        if {$previous eq $value} {return}
        set existed [info exists settings($key)]
        set original [value ::plugins::mahlkonig_decent_sync::settings($key)]
        set settings($key) $value
        if {[catch {save_plugin_settings mahlkonig_decent_sync}]} {
            if {$existed} {set settings($key) $original} else {unset settings($key)}
            set status_text "Could not save banner preference"
        }
        schedule_banner_expiry
        refresh_banner_options
        draw_banner
    }
    proc change_banner_duration {delta} {
        save_banner_preference banner_duration_s [expr {max(2,min(15,[banner_duration]+$delta))}]
    }
    proc banner_shot_key {id} {
        set split [string last : $id]
        if {$split < 0} {return $id}
        return [string range $id 0 [expr {$split-1}]]
    }
    proc clear_banner {} {
        variable banner_record;variable banner_timer;variable banner_serial
        if {$banner_timer ne ""} {after cancel $banner_timer;set banner_timer ""}
        incr banner_serial
        set banner_record {}
        if {[llength [info commands .can]]} {.can delete coffee_sync_banner}
    }
    proc schedule_banner_expiry {} {
        variable banner_record;variable banner_timer;variable banner_serial
        if {$banner_timer ne ""} {after cancel $banner_timer;set banner_timer ""}
        if {$banner_record eq "" || [dict get $banner_record sticky]} {return}
        set remaining [expr {[dict get $banner_record started]+[banner_duration]*1000-[clock milliseconds]}]
        set banner_timer [after [expr {max(0,$remaining)}] [list [namespace current]::expire_banner $banner_serial]]
    }
    proc expire_banner {serial} {
        variable banner_record;variable banner_serial
        if {$serial != $banner_serial || $banner_record eq "" || [dict get $banner_record sticky]} {return}
        if {[clock milliseconds] < [dict get $banner_record started]+[banner_duration]*1000} {
            schedule_banner_expiry
        } else {clear_banner}
    }
    proc show_banner {key text kind {notice_id ""}} {
        variable banner_record;variable banner_serial
        if {$kind ne "preview" && ![value ::plugins::mahlkonig_decent_sync::settings(show_popups) 1]} {return}
        clear_banner
        set banner_record [dict create key $key text $text kind $kind notice_id $notice_id sticky [expr {$kind eq "warning"}] started [clock milliseconds]]
        schedule_banner_expiry
        draw_banner
    }
    proc preview_banner {} {
        variable banner_record
        if {$banner_record ne "" && [dict get $banner_record sticky]} {draw_banner;return}
        show_banner preview "Banner preview" preview
    }
    proc reconcile_banner {display} {
        variable banner_record;variable notices_seen
        if {$banner_record eq ""} {return}
        set current_key [dict get $banner_record key]
        set notice [dict get $display notice]
        if {$notice ne "null" && [dict exists $notice id]} {
            set id [dict get $notice id]
            # A repeated older phase must not dismiss a newer action banner.
            if {$id in $notices_seen && $id ne [dict get $banner_record notice_id]} {return}
            set next_key [banner_shot_key $id]
            if {$current_key ne "preview" && $next_key ne $current_key} {clear_banner;return}
            if {[dict get $banner_record sticky] && $next_key eq $current_key &&
                [lindex [split [dict get $notice id] :] end] in {matched delivering validating adjusted within_tolerance held unchanged verified}} {
                clear_banner;return
            }
        }
        if {[dict get $banner_record sticky] && [dict exists $display sync_ready] && [dict get $display sync_ready] eq "true"} {clear_banner}
    }
    proc offer_banner {notice} {
        variable banner_seen
        if {![value ::plugins::mahlkonig_decent_sync::settings(show_popups) 1]} {return}
        set id [dict get $notice id]
        set key [banner_shot_key $id]
        set phase [lindex [split $id :] end]
        switch -- $phase {
            adjusted - within_tolerance - held - unchanged - verified {set text "Shot received";set kind success}
            awaiting_arm {set text "Press grinder";set kind warning}
            history_only {set text "Feedback missed";set kind warning}
            movement_pending {set text "Movement unconfirmed";set kind warning}
            uncertain - rejected - failed {set text "Check feedback";set kind warning}
            default {return}
        }
        set group [list $key $text]
        if {$group in $banner_seen} {return}
        lappend banner_seen $group
        set banner_seen [lrange $banner_seen end-49 end]
        show_banner $key $text $kind $id
    }
    proc banner_box {} {
        switch -- [banner_position] {
            top_right {return {1380 80 1100 160}}
            bottom_center {return {730 1360 1100 160}}
            default {return {730 80 1100 160}}
        }
    }
    proc banner_round {x y width height radius fill} {
        set right [expr {$x+$width}];set bottom [expr {$y+$height}]
        set points [list [expr {$x+$radius}] $y [expr {$right-$radius}] $y $right $y \
            $right [expr {$y+$radius}] $right [expr {$bottom-$radius}] $right $bottom \
            [expr {$right-$radius}] $bottom [expr {$x+$radius}] $bottom $x $bottom \
            $x [expr {$bottom-$radius}] $x [expr {$y+$radius}] $x $y]
        set scaled {}
        foreach {px py} $points {lappend scaled [dui platform rescale_x $px] [dui platform rescale_y $py]}
        .can create polygon {*}$scaled -smooth 1 -fill $fill -outline "" -tags coffee_sync_banner
    }
    proc draw_banner {args} {
        variable banner_record
        if {![llength [info commands .can]] || ![llength [info commands winfo]] || ![winfo exists .can]} {return}
        .can delete coffee_sync_banner
        if {$banner_record eq ""} {return}
        if {[dict get $banner_record kind] ne "preview" && ![value ::plugins::mahlkonig_decent_sync::settings(show_popups) 1]} {return}
        if {![dict get $banner_record sticky] && [clock milliseconds] >= [dict get $banner_record started]+[banner_duration]*1000} {clear_banner;return}
        set p [settings_palette]
        lassign [banner_box] x y width height
        set role [expr {[dict get $banner_record kind] eq "warning" ? "warning" : "accent"}]
        banner_round $x $y $width $height 38 [dict get $p border]
        banner_round [expr {$x+3}] [expr {$y+3}] [expr {$width-6}] [expr {$height-6}] 34 [dict get $p card]
        .can create oval [dui platform rescale_x [expr {$x+52}]] [dui platform rescale_y [expr {$y+70}]] \
            [dui platform rescale_x [expr {$x+72}]] [dui platform rescale_y [expr {$y+90}]] \
            -width 0 -fill [dict get $p $role] -tags coffee_sync_banner
        .can create text [dui platform rescale_x [expr {$x+115}]] [dui platform rescale_y [expr {$y+80}]] \
            -text [dict get $banner_record text] -font Helv_12 -anchor w -fill [dict get $p ink] -tags coffee_sync_banner
        .can create text [dui platform rescale_x [expr {$x+$width-75}]] [dui platform rescale_y [expr {$y+80}]] \
            -text × -font Helv_14 -fill [dict get $p muted] -tags coffee_sync_banner
        .can bind coffee_sync_banner <ButtonPress-1> "[list [namespace current]::clear_banner]; break"
        .can raise coffee_sync_banner
    }
    proc install_banners {} {
        variable banner_hooks
        if {$banner_hooks} {return}
        dui page add_action {} show [namespace code draw_banner]
        set banner_hooks 1
    }
    proc install_badge {} {
        # Append to Streamline's actual rich-text data row after its own refresh.
        # That preserves the skin's layout, baseline, tags and scale controls.
        if {[value ::settings(skin)] ni {Streamline {Streamline Dark}}} {return}
        foreach refresh [info procs ::refresh_*] {
            set body [info body $refresh]
            if {[string first {group_head_heater_temperature} $body] < 0 ||
                [string first {time_format} $body] < 0} {continue}
            set widget [string range $refresh [string length ::refresh_] end]
            if {![winfo exists $widget]} {continue}
            set callback [list [namespace current]::refresh_badge $widget]
            if {[lsearch -exact [trace info execution $refresh] [list leave $callback]] >= 0} {continue}
            # Use the remaining row width, with the skin's existing right margin.
            set x [lindex [.can coords $widget] 0]
            set char_width [font measure [$widget cget -font] 0]
            set width [expr {int(([rescale_x_skin 2490]-$x)/$char_width)}]
            $widget configure -width $width -wrap none
            trace add execution $refresh leave $callback
            $refresh
        }
    }
    proc update_grind_position {display} {
        variable grind_position;variable grind_position_deadline
        set grind_position "";set grind_position_deadline 0
        if {![dict exists $display grind_position]} {return}
        set reading [dict get $display grind_position]
        if {$reading eq "null" || ![dict exists $reading um] || ![dict exists $reading age_s]} {return}
        set position [dict get $reading um];set age [dict get $reading age_s]
        if {[num $position] eq "null" || [num $age] eq "null" || $position < 0 || $age < 0 || $age >= 35} {return}
        set grind_position $position
        set grind_position_deadline [expr {[clock milliseconds]+int((35-$age)*1000)}]
    }
    proc grind_position_label {} {
        variable grind_position;variable grind_position_deadline;variable last_server_ack;variable grinder_connected
        if {$grind_position eq "" || !$grinder_connected || [clock seconds]-$last_server_ack > 10 ||
                [clock milliseconds] >= $grind_position_deadline} {return ""}
        return [format {%.1f µm} $grind_position]
    }
    proc refresh_grind_row {widget tag args} {
        set range [$widget tag ranges $tag]
        if {[llength $range] != 2} {return}
        $widget delete {*}$range
        $widget insert [lindex $range 0] [grind_position_label] $tag
    }
    proc install_grind_display {} {
        # Replace only the live Streamline Dark readout, never recipe/DYE/history
        # data. DUI rebuilds the original widgets when the extension is removed.
        set skin [value ::settings(skin)]
        if {$skin ne "Streamline Dark" && !($skin eq "Streamline" &&
                [string is true -strict [value ::streamline_dark_mode 0]])} {return}
        if {![llength [info commands .can]] || ![array exists ::dui::page::pages_data]} {return}
        set pages {off espresso water flush hotwaterrinse steam}
        set found 0
        foreach page $pages {
            foreach {id code} [value ::dui::page::pages_data($page,variables)] {
                if {[string trim $code] ne {[ifexists ::settings(grinder_setting)]}} {continue}
                ::dui::page::add_variable $page $id {[::plugins::mahlkonig_decent_sync::grind_position_label]}
                dui item config $id -font Inter-Bold12
                set found 1
            }
        }
        if {!$found} {return}
        foreach page $pages {
            foreach id [dui item get $page *] {
                # Numeric ID bindings include both press and long-press/release.
                # Clear only the actual Grind editor; other data entry survives.
                foreach event [.can bind $id] {
                    set command [.can bind $id $event]
                    if {[string first streamline_adjust_grind $command] >= 0 ||
                            ([string first ask_for_data_entry_number $command] >= 0 &&
                             [string first {::settings(grinder_setting)} $command] >= 0)} {
                        .can bind $id $event {}
                    }
                }
            }
            foreach tag {streamline_minus_grind_btn streamline_plus_grind_btn} {
                foreach id [dui item get $page ${tag}*] {
                    dui item config $id -initial_state hidden -state hidden
                }
            }
        }
        # Zoomed mode uses a rich-text row instead of the canvas value. Replace
        # only its tagged Grind segment after the skin refreshes the other data.
        foreach refresh [info procs ::refresh_*] {
            set widget [string range $refresh [string length ::refresh_] end]
            if {![winfo exists $widget]} {continue}
            set tags [.can gettags $widget]
            if {"p:off_zoomed" ni $tags && "p:espresso_zoomed" ni $tags} {continue}
            if {![regexp {insert end "\$::settings\(grinder_setting\)" ([^ \n]+)} [info body $refresh] _ tag]} {continue}
            set callback [list [namespace current]::refresh_grind_row $widget $tag]
            if {[lsearch -exact [trace info execution $refresh] [list leave $callback]] >= 0} {continue}
            trace add execution $refresh leave $callback
            $refresh
        }
    }
}
