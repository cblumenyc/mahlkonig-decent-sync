# Settings for the Classic controller.
namespace eval ::plugins::mahlkonig_decent_sync {
    proc measurement_label {} {
        variable settings
        if {$settings(measurement_mode) eq "auto"} {
            return "Mode: scale preferred; automatic volume backup"
        }
        if {$settings(measurement_mode) eq "scale"} {
            return "Mode: measured scale weight (g)"
        }
        return "Mode: volume estimate (mL; uncalibrated grams)"
    }
    proc select_measurement_mode {mode} {
        variable settings;variable shot_id;variable status_text;variable last_profile
        if {$mode ni {volume scale auto}} {error "Unknown measurement mode"}
        if {$shot_id ne "" || [::de1::state::current_state] eq "Espresso"} {
            set status_text "Finish the shot before changing measurement mode"
            return
        }
        if {$settings(measurement_mode) eq $mode} {return}
        set previous $settings(measurement_mode)
        set settings(measurement_mode) $mode
        if {[catch {save_plugin_settings mahlkonig_decent_sync} error]} {
            set settings(measurement_mode) $previous
            set status_text "Could not save measurement mode; previous mode retained"
            return
        }
        set last_profile ""
        set status_text "Measurement mode changed; profile setup required on the server dashboard"
    }
    proc appearance_mode {} {
        set mode [value ::plugins::mahlkonig_decent_sync::settings(appearance) auto]
        if {$mode in {light dark}} {return $mode}
        set skin [value ::settings(skin)]
        if {[string match -nocase {*dark*} $skin] ||
            ($skin eq "Streamline" && [string is true -strict [value ::streamline_dark_mode 0]])} {return dark}
        return light
    }
    proc settings_palette {} {
        if {[appearance_mode] eq "dark"} {
            return {bg #141b17 card #202824 ink #eef4f0 muted #a7b8ae border #39483f accent #a5d7b7 on_accent #152c1e selected #233e2d ok #8ed0a6 warning #ffb5a8 warning_bg #492b26 thumb #eef4f0}
        }
        return {bg #eff3f0 card #ffffff ink #192b23 muted #607168 border #dce5df accent #23563f on_accent #ffffff selected #eaf4ed ok #2c7451 warning #a8362a warning_bg #fbece9 thumb #ffffff}
    }
    proc select_appearance {mode} {
        variable settings;variable status_text
        if {$mode ni {auto light dark}} {return}
        set previous [value ::plugins::mahlkonig_decent_sync::settings(appearance) auto]
        if {$mode eq $previous} {return}
        set settings(appearance) $mode
        if {[catch {save_plugin_settings mahlkonig_decent_sync}]} {
            set settings(appearance) $previous
            set status_text "Could not save appearance"
        }
        refresh_settings_ui
        refresh_banner_options
        refresh_update_ui
        draw_banner
    }
    proc settings_connection_state {device} {
        variable last_server_ack;variable grinder_connected
        if {[clock seconds]-$last_server_ack > 10} {
            return [expr {$device eq "server" ? "Offline" : "Unknown"}]
        }
        return [expr {$device eq "server" || $grinder_connected ? "Online" : "Offline"}]
    }
    proc settings_sync_label {} {
        variable sync_ready;variable sync_warning
        if {[settings_connection_state server] ne "Online"} {return "Server offline"}
        if {[settings_connection_state grinder] ne "Online"} {return "Grinder offline"}
        if {$sync_ready} {return Ready}
        switch -glob -- $sync_warning {
            awaiting_arm - grinder_press_required {return "Press required"}
            awaiting_native* - waiting_for_native* - waiting_for_fresh_grinder_state -
            verifying_late_grinder_press - waiting_for_reconciliation {return "Checking grinder"}
            server_update_in_progress {return "Server updating"}
            automatic_feedback_disabled {return "Sync disabled"}
            grinder_movement_unconfirmed {return "Movement unconfirmed"}
            *target* - *recipe* - *profile* {return "Check targets"}
            *history* - *shot_not_sent* - *no_cycle* - *expired* {return "Feedback missed"}
            default {return "Sync paused"}
        }
    }
    proc settings_notice {} {
        variable status_text
        # Keep detailed protocol/pop-up messages intact; the settings page uses labels.
        foreach {pattern label} {
            {Could not save final*} {Final weight unavailable}
            {Could not save*} {Save failed}
            {Finish the*} {Finish current cycle}
            {Enter a server*} {Invalid server address}
            {Port must*} {Invalid port}
            {Could not reach*} {Server unavailable}
            {Could not verify*} {Server unverified}
            {Server verified.*} {Saved events pending}
            {Checking server*} {Checking server…}
            {Server saved.*} {Connecting…}
            {Measurement mode changed*} {Profile setup required}
            {Sync capture error:*} {Capture error}
            {Server has not acknowledged*} {Storage unconfirmed}
            {Server unreachable*} {Events saved locally}
        } {
            if {[string match $pattern $status_text]} {return $label}
        }
        return ""
    }
    proc settings_shape {tag x y width height {role card} {radius 36} {page plugin_mahlkonig_decent_sync}} {
        set color [dict get [settings_palette] $role]
        dui add shape round $page $x $y -bwidth $width -bheight $height \
            -radius $radius -fill $color -disabledfill $color -tags $tag
    }
    proc settings_text {tag x y text {font Helv_12} {role ink} {anchor w} {page plugin_mahlkonig_decent_sync}} {
        add_de1_text $page $x $y -text $text -font $font -anchor $anchor \
            -tags [list $tag cs_settings_$role] -fill [dict get [settings_palette] $role]
    }
    proc refresh_banner_options {} {
        variable banner_options_drawn
        set signature [list [appearance_mode] [banner_position] [banner_duration] [settings_notice]]
        if {$signature eq $banner_options_drawn} {return}
        set page plugin_mahlkonig_decent_sync_banners
        set p [settings_palette]
        dui item config $page cs_bo_bg -fill [dict get $p bg]
        dui item config $page cs_bo_card -fill [dict get $p card]
        foreach role {ink muted} {dui item config $page cs_settings_$role -fill [dict get $p $role]}
        foreach position {top_center top_right bottom_center} {
            set selected [expr {$position eq [banner_position]}]
            dui item config $page cs_bo_${position}_fill -fill [dict get $p [expr {$selected ? "accent" : "bg"}]]
            dui item config $page cs_bo_${position}_text -fill [dict get $p [expr {$selected ? "on_accent" : "ink"}]]
        }
        foreach tag {cs_bo_minus_fill cs_bo_plus_fill} {dui item config $page $tag -fill [dict get $p bg]}
        foreach tag {cs_bo_preview_fill cs_bo_done_fill} {dui item config $page $tag -fill [dict get $p accent]}
        foreach tag {cs_bo_preview_text cs_bo_done_text} {dui item config $page $tag -fill [dict get $p on_accent]}
        dui item config $page cs_bo_notice -fill [dict get $p warning]
        set banner_options_drawn $signature
    }
    proc refresh_update_ui {} {
        variable update_drawn
        set signature [appearance_mode]
        if {$signature eq $update_drawn} {return}
        set page plugin_mahlkonig_decent_sync_updates;set p [settings_palette]
        dui item config $page cs_up_bg -fill [dict get $p bg]
        dui item config $page cs_up_card -fill [dict get $p card]
        foreach role {ink muted} {dui item config $page cs_settings_$role -fill [dict get $p $role]}
        foreach tag {cs_up_action_fill cs_up_done_fill} {dui item config $page $tag -fill [dict get $p accent]}
        foreach tag {cs_up_action_text cs_up_done_text} {dui item config $page $tag -fill [dict get $p on_accent]}
        set update_drawn $signature
    }
    proc build_update_ui {} {
        variable version;variable update_drawn
        set update_drawn ""
        set page plugin_mahlkonig_decent_sync_updates;set p [settings_palette]
        dui page add $page -bg_img "" -bg_color [dict get $p bg] -theme default
        dui add canvas_item rect $page 0 0 2560 1600 -fill [dict get $p bg] -width 0 -tags cs_up_bg
        settings_text cs_up_title 180 175 Updates Helv_20_bold ink w $page
        settings_shape cs_up_card 160 310 2240 900 card 36 $page
        settings_text cs_up_about 245 1120 "Community integration · GPL-3.0-or-later" Helv_9 muted w $page
        settings_text cs_up_about_logo 245 1280 "Logo: Hemro Group / Oelna · CC BY-SA 3.0" Helv_9 muted w $page
        settings_text cs_up_plugin 245 440 "Mahlkönig - Decent sync" Helv_15 ink w $page
        settings_text cs_up_installed_label 245 640 Installed Helv_12 muted w $page
        settings_text cs_up_installed 2280 640 $version Helv_15 ink e $page
        settings_text cs_up_latest_label 245 810 Latest Helv_12 muted w $page
        add_de1_variable $page 2280 810 -font Helv_15 -anchor e -tags {cs_up_latest cs_settings_ink} -fill [dict get $p ink] \
            -textvariable {[::plugins::mahlkonig_decent_sync::update_version_label]}
        add_de1_variable $page 245 1020 -font Helv_12 -anchor w -tags {cs_up_status cs_settings_muted} -fill [dict get $p muted] \
            -textvariable {$::plugins::mahlkonig_decent_sync::update_status}
        settings_shape cs_up_action_fill 1480 1395 560 118 accent 32 $page
        add_de1_variable $page 1760 1455 -font Helv_12 -anchor center -tags cs_up_action_text -fill [dict get $p on_accent] \
            -textvariable {[::plugins::mahlkonig_decent_sync::update_button_label]}
        add_de1_button $page ::plugins::mahlkonig_decent_sync::update_action 1480 1395 2040 1513 ""
        settings_shape cs_up_done_fill 2090 1395 310 118 accent 32 $page
        settings_text cs_up_done_text 2245 1455 Done Helv_12 on_accent center $page
        add_de1_button $page {page_to_show_when_off plugin_mahlkonig_decent_sync} 2090 1395 2400 1513 ""
        dui page add_action $page show [namespace code refresh_update_ui]
        dui page add_action $page update_vars [namespace code refresh_update_ui]
        refresh_update_ui
    }
    proc build_banner_ui {} {
        variable banner_options_drawn
        set banner_options_drawn ""
        set page plugin_mahlkonig_decent_sync_banners
        set p [settings_palette]
        dui page add $page -bg_img "" -bg_color [dict get $p bg] -theme default
        dui add canvas_item rect $page 0 0 2560 1600 -fill [dict get $p bg] -width 0 -tags cs_bo_bg
        settings_text cs_bo_title 180 175 Banners Helv_20_bold ink w $page
        settings_shape cs_bo_card 160 310 2240 900 card 36 $page
        settings_text cs_bo_position 245 425 Position Helv_15 ink w $page
        foreach {position x title} {top_center 245 {Top centre} top_right 965 {Top right} bottom_center 1685 {Bottom centre}} {
            settings_shape cs_bo_${position}_fill $x 510 630 160 bg 30 $page
            settings_text cs_bo_${position}_text [expr {$x+315}] 590 $title Helv_12 ink center $page
            add_de1_button $page [list ::plugins::mahlkonig_decent_sync::save_banner_preference banner_position $position] $x 510 [expr {$x+630}] 670 ""
        }
        settings_text cs_bo_duration 245 840 Duration Helv_15 ink w $page
        foreach {tag x label delta} {minus 245 − -1 plus 930 + 1} {
            settings_shape cs_bo_${tag}_fill $x 920 220 160 bg 30 $page
            settings_text cs_bo_${tag}_text [expr {$x+110}] 1000 $label Helv_15 ink center $page
            add_de1_button $page [list ::plugins::mahlkonig_decent_sync::change_banner_duration $delta] $x 920 [expr {$x+220}] 1080 ""
        }
        add_de1_variable $page 700 1000 -font Helv_20_bold -anchor center -tags {cs_bo_seconds cs_settings_ink} -fill [dict get $p ink] \
            -textvariable {[::plugins::mahlkonig_decent_sync::banner_duration] s}
        settings_text cs_bo_persistent 1390 1000 "Actions · persistent" Helv_11 muted w $page
        add_de1_variable $page 180 1455 -font Helv_11 -anchor w -tags cs_bo_notice -fill [dict get $p warning] \
            -textvariable {[::plugins::mahlkonig_decent_sync::settings_notice]}
        foreach {tag x label command} {preview 1680 Preview ::plugins::mahlkonig_decent_sync::preview_banner done 2090 Done {page_to_show_when_off plugin_mahlkonig_decent_sync}} {
            settings_shape cs_bo_${tag}_fill $x 1395 310 118 accent 32 $page
            settings_text cs_bo_${tag}_text [expr {$x+155}] 1455 $label Helv_12 on_accent center $page
            add_de1_button $page $command $x 1395 [expr {$x+310}] 1513 ""
        }
        dui page add_action $page show [namespace code refresh_banner_options]
        dui page add_action $page update_vars [namespace code refresh_banner_options]
        refresh_banner_options
    }
    proc refresh_settings_ui {} {
        variable settings;variable settings_entry;variable settings_drawn
        set page plugin_mahlkonig_decent_sync
        set label [settings_sync_label]
        set signature [list [appearance_mode] [value ::plugins::mahlkonig_decent_sync::settings(appearance) auto] \
            $settings(measurement_mode) $settings(show_popups) $settings(show_badge) $label \
            [settings_connection_state server] [settings_connection_state grinder] [settings_notice]]
        if {$signature eq $settings_drawn} {return}
        set p [settings_palette]
        foreach role {bg card ink muted} {
            dui item config $page cs_settings_$role -fill [dict get $p $role]
        }
        foreach tag {cs_connect_border cs_entry_border} {
            dui item config $page $tag -fill [dict get $p border]
        }
        foreach tag {cs_connect_fill cs_entry_fill} {
            dui item config $page $tag -fill [dict get $p card]
        }
        dui item config $page cs_done_fill -fill [dict get $p accent]
        dui item config $page cs_done_text -fill [dict get $p on_accent]
        dui item config $page cs_settings_notice -fill [dict get $p warning]
        set healthy [badge_connected]
        set status_color [dict get $p [expr {$healthy ? "ok" : "warning"}]]
        dui item config $page cs_sync_fill -fill [dict get $p [expr {$healthy ? "selected" : "warning_bg"}]]
        dui item config $page cs_sync_text -fill $status_color
        dui item config $page cs_sync_dot -fill $status_color
        foreach device {server grinder} {
            set state [settings_connection_state $device]
            set role [expr {$state eq "Online" ? "ok" : ($state eq "Unknown" ? "muted" : "warning")}]
            dui item config $page cs_${device}_state -fill [dict get $p $role]
            dui item config $page cs_${device}_dot -fill [dict get $p $role]
        }
        foreach mode {auto scale volume} {
            set selected [expr {$settings(measurement_mode) eq $mode}]
            dui item config $page cs_measure_${mode}_border -fill [dict get $p [expr {$selected ? "accent" : "card"}]]
            dui item config $page cs_measure_${mode}_fill -fill [dict get $p [expr {$selected ? "selected" : "card"}]]
            dui item config $page cs_measure_${mode}_radio -fill [dict get $p [expr {$selected ? "accent" : "border"}]]
            dui item config $page cs_measure_${mode}_dot -fill [dict get $p [expr {$selected ? "thumb" : "card"}]]
        }
        foreach {key left right} {show_popups cs_popups_left cs_popups_right show_badge cs_badge_left cs_badge_right} {
            set on [expr {!!$settings($key)}]
            dui item config $page cs_toggle_$key -fill [dict get $p [expr {$on ? "accent" : "border"}]]
            # Change fill only: hidden-page item states remain owned by DUI.
            dui item config $page $left -fill [dict get $p [expr {$on ? "accent" : "thumb"}]]
            dui item config $page $right -fill [dict get $p [expr {$on ? "thumb" : "border"}]]
        }
        foreach mode {auto light dark} {
            set selected [expr {[value ::plugins::mahlkonig_decent_sync::settings(appearance) auto] eq $mode}]
            dui item config $page cs_appearance_${mode}_fill -fill [dict get $p [expr {$selected ? "accent" : "card"}]]
            dui item config $page cs_appearance_${mode}_text -fill [dict get $p [expr {$selected ? "on_accent" : "muted"}]]
        }
        if {$settings_entry ne ""} {
            $settings_entry configure -background [dict get $p card] -foreground [dict get $p ink] \
                -insertbackground [dict get $p ink] -selectbackground [dict get $p accent] \
                -selectforeground [dict get $p on_accent] -highlightbackground [dict get $p card] \
                -highlightcolor [dict get $p accent]
        }
        set settings_drawn $signature
    }
    proc build_ui {} {
        variable settings_entry;variable settings_drawn;variable module_root
        set page plugin_mahlkonig_decent_sync
        set settings_drawn ""
        set p [settings_palette]
        dui page add $page -bg_img "" -bg_color [dict get $p bg] -theme default
        dui add canvas_item rect $page 0 0 2560 1600 -fill [dict get $p bg] -width 0 -tags cs_settings_bg
        set logo [file join $module_root mahlkonig.png]
        if {![file exists $logo]} {set logo [file join $module_root .. .. assets mahlkonig.png]}
        # Load before scaling: DUI may defer image files until the page is shown.
        # Classic's public scaler also supports DUI versions that do not export it.
        set logo_image ${page}-cs_logo
        ::image create photo $logo_image -file $logo
        set logo_scale [expr {[rescale_x_skin 220]/220.0}]
        ::photoscale $logo_image $logo_scale $logo_scale
        dui add canvas_item image $page 160 112 -image $logo_image -anchor nw -tags cs_logo
        settings_text cs_title 430 175 "Mahlkönig - Decent sync" Helv_15
        settings_text cs_updates_link 1820 1455 "Updates ›" Helv_11 muted center
        add_de1_button $page {page_to_show_when_off plugin_mahlkonig_decent_sync_updates} 1670 1395 2010 1513 ""
        settings_shape cs_sync_fill 1710 112 670 126 selected 126
        dui add canvas_item oval $page 1755 163 1777 185 -width 0 -fill [dict get $p ok] -tags cs_sync_dot
        add_de1_variable $page 1840 175 -font Helv_12 -anchor w -tags cs_sync_text -fill [dict get $p ok] \
            -textvariable {[::plugins::mahlkonig_decent_sync::settings_sync_label]}

        settings_shape {cs_connection_card cs_settings_card} 160 310 1090 780
        settings_shape {cs_measurement_card cs_settings_card} 1310 310 1090 780
        settings_text cs_connection_title 230 405 Connection Helv_15
        settings_text cs_server_label 230 535 Server
        settings_text cs_grinder_label 230 645 Grinder
        foreach {device y} {server 535 grinder 645} {
            dui add canvas_item oval $page 940 [expr {$y-9}] 958 [expr {$y+9}] -width 0 -fill [dict get $p muted] -tags cs_${device}_dot
            add_de1_variable $page 1175 $y -font Helv_11 -anchor e -tags cs_${device}_state -fill [dict get $p muted] \
                -textvariable [format {[::plugins::mahlkonig_decent_sync::settings_connection_state %s]} $device]
        }
        settings_text cs_address_label 230 765 "SERVER ADDRESS" Helv_9 muted
        settings_shape cs_entry_border 225 817 960 118 border 28
        settings_shape cs_entry_fill 228 820 954 112 card 24
        set settings_entry [dui add entry $page 260 876 -tags cs_server_entry -font Helv_12 \
            -canvas_width 875 -canvas_height 90 -canvas_anchor w -borderwidth 0 -relief flat -highlightthickness 0 \
            -textvariable ::plugins::mahlkonig_decent_sync::server_input]
        settings_shape cs_connect_border 225 965 960 108 border 28
        settings_shape cs_connect_fill 228 968 954 102 card 24
        settings_text cs_connect_text 705 1019 "Save & connect" Helv_12 ink center
        add_de1_button $page {::plugins::mahlkonig_decent_sync::save_server} 225 960 1185 1080 ""

        settings_text cs_measurement_title 1380 405 Measurement Helv_15
        foreach {mode y title subtitle} {auto 478 {Scale + backup} {Backup · uncalibrated} scale 685 {Scale only} {} volume 878 {Volume estimate} Uncalibrated} {
            settings_shape cs_measure_${mode}_border 1375 $y 960 182 card 32
            settings_shape cs_measure_${mode}_fill 1378 [expr {$y+3}] 954 176 card 28
            set title_y [expr {$y+($subtitle eq "" ? 91 : 64)}]
            settings_text cs_measure_${mode}_text 1420 $title_y $title Helv_12
            if {$subtitle ne ""} {settings_text cs_measure_${mode}_note 1420 [expr {$y+124}] $subtitle Helv_9 muted}
            dui add canvas_item oval $page 2220 [expr {$y+66}] 2270 [expr {$y+116}] -width 0 -fill [dict get $p border] -tags cs_measure_${mode}_radio
            dui add canvas_item oval $page 2237 [expr {$y+83}] 2253 [expr {$y+99}] -width 0 -fill [dict get $p card] -tags cs_measure_${mode}_dot
            add_de1_button $page [list ::plugins::mahlkonig_decent_sync::select_measurement_mode $mode] 1375 $y 2335 [expr {$y+182}] ""
        }

        settings_shape {cs_display_card cs_settings_card} 160 1145 2240 200
        settings_text cs_display_title 230 1245 Display Helv_15
        settings_text cs_popups_label 700 1245 "Banners ›"
        add_de1_button $page {page_to_show_when_off plugin_mahlkonig_decent_sync_banners} 650 1165 1170 1325 ""
        settings_text cs_badge_label 1600 1245 "Status badge"
        foreach {key x prefix x0 x1} {show_popups 1210 popups 1190 1450 show_badge 2180 badge 1550 2340} {
            settings_shape cs_toggle_$key $x 1205 140 80 accent 80
            foreach {side offset} {left 10 right 70} {
                dui add canvas_item oval $page [expr {$x+$offset}] 1215 [expr {$x+$offset+60}] 1275 \
                    -width 0 -fill [dict get $p thumb] -tags cs_${prefix}_$side
            }
            add_de1_button $page [list ::plugins::mahlkonig_decent_sync::toggle_display $key] $x0 1165 $x1 1325 ""
        }
        settings_text cs_appearance_label 185 1455 Appearance Helv_11 muted
        foreach {mode x title} {auto 550 Auto light 780 Light dark 1010 Dark} {
            settings_shape cs_appearance_${mode}_fill $x 1395 210 118 card 28
            settings_text cs_appearance_${mode}_text [expr {$x+105}] 1455 $title Helv_11 muted center
            add_de1_button $page [list ::plugins::mahlkonig_decent_sync::select_appearance $mode] $x 1395 [expr {$x+210}] 1513 ""
        }
        add_de1_variable $page 1290 1455 -font Helv_9 -anchor w -tags cs_settings_notice -fill [dict get $p warning] \
            -textvariable {[::plugins::mahlkonig_decent_sync::settings_notice]}
        settings_shape cs_done_fill 2090 1395 310 118 accent 32
        settings_text cs_done_text 2245 1455 Done Helv_12 on_accent center
        add_de1_button $page {page_to_show_when_off extensions} 2090 1395 2400 1513 ""
        dui page add_action $page show [namespace code refresh_settings_ui]
        dui page add_action $page update_vars [namespace code refresh_settings_ui]
        build_banner_ui
        build_update_ui
        refresh_settings_ui
        return $page
    }
}
