# Classic de1app extension. Loading metadata has no side effects; main installs hooks.
namespace eval ::plugins::mahlkonig_decent_sync {
    variable author "Mahlkönig - Decent sync contributors"
    variable contact "Community integration"
    variable version 0.0.0
    variable name "Mahlkönig - Decent sync"
    variable description "Community integration for the Mahlkönig E64 WS and Decent Espresso."
    variable status_text "Server not connected"
    variable running 0
    variable busy 0
    variable sequence 0
    variable shot_id ""
    variable shot_start 0
    variable pi_sent 0
    variable stop_reason "manual_or_unknown"
    variable last_profile ""
    variable last_profile_at 0
    variable quality {}
    variable outbox ""
    variable session ""
    variable last_scale_update 0
    variable automatic_shot 0
    variable shot_source ""
    variable fallback_reason ""
    variable fallback_target 0
    variable fallback_policy "during_shot"
    variable pending_scale {}
    variable pending_scale_timer ""
    variable cycle_offer ""
    variable shot_cycles {}
    variable server_input ""
    variable last_server_ack 0
    variable grinder_connected 0
    variable sync_ready 0
    variable active_cycle ""
    variable active_cycle_expires 0
    variable notices_seen {}
    variable server_testing 0
    variable sync_warning ""
    variable settings_entry ""
    variable settings_drawn ""
    variable banner_record {}
    variable banner_timer ""
    variable banner_serial 0
    variable banner_seen {}
    variable banner_hooks 0
    variable banner_options_drawn ""
    variable grind_position ""
    variable grind_position_deadline 0
    variable module_root [file dirname [file normalize [info script]]]
    variable update_phase idle
    variable update_status "Not checked"
    variable update_manifest {}
    variable update_server ""
    variable update_server_version ""
    variable update_job 0
    variable update_payload ""
    variable update_original ""
    variable update_drawn ""

    proc main {} {
        variable settings;variable running;variable session;variable outbox;variable server_input
        if {$running} {return}
        if {[value ::plugins::decent_sync::running 0]} {error "Complete the assisted migration before enabling both plugin names"}
        package require http
        package require json
        package require sha256
        foreach {key default} {bridge_url {} server_id {} controller_id classic-de1 measurement_mode scale grams_per_ml 1.0 fallback_policy during_shot show_popups 1 show_badge 1 appearance auto banner_position top_center banner_duration_s 4} {
            if {![info exists settings($key)]} {set settings($key) $default}
        }
        if {$settings(measurement_mode) ni {volume scale auto}} {error "Select volume, scale or automatic mode explicitly"}
        if {[num $settings(grams_per_ml)] eq "null" || $settings(grams_per_ml) < 0.1 || $settings(grams_per_ml) > 3} {error "Invalid volume conversion factor"}
        if {$settings(fallback_policy) ni {at_start during_shot}} {error "Invalid automatic fallback policy"}
        set settings(bridge_url) [string trimright $settings(bridge_url) /]
        set outbox [file join [file dirname [plugin_settings_file mahlkonig_decent_sync]] outbox]
        file mkdir $outbox
        set session "classic-[clock seconds]-[clock clicks]-[pid]"
        foreach command {::profile_vars ::add_de1_variable ::de1::event::listener::on_all_state_change_add ::gui::notify::de1_event ::gui::notify::scale_event ::de1::sav::check_for_sav ::de1::sav::skip_sav_check ::device::scale::saw::check_for_saw} {
            if {![llength [info commands $command]]} {error "Required capture hook unavailable: $command"}
        }
        set server_input $settings(bridge_url)
        set page [build_ui]
        install_banners
        catch {install_badge}
        catch {install_grind_display}
        install_stop_hooks
        # Capture synchronously so queued UI callbacks cannot shift the stop boundary.
        ::de1::event::listener::on_all_state_change_add -sync [namespace current]::state_changed
        foreach command {::gui::notify::de1_event ::gui::notify::scale_event} {
            trace add execution $command enter [namespace current]::stop_notification
        }
        trace add variable ::de1(scale_sensor_weight) write [namespace current]::scale_updated
        set running 1
        plugins gui mahlkonig_decent_sync $page
        heartbeat
        drain
    }
}
foreach module {common capture transport display settings updates} {
    source [file join $::plugins::mahlkonig_decent_sync::module_root "$module.tcl"]
}
unset module
