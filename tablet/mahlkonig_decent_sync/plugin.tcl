# Stable Classic loader, bootstrap API 1. Hooks are installed only by main.
namespace eval ::plugins::mahlkonig_decent_sync {
    if {[info exists running] && $running} {return}
    variable plugin_file [file normalize [info script]]
    variable package_files {plugin.tcl product.tcl common.tcl capture.tcl transport.tcl display.tcl settings.tcl updates.tcl mahlkonig.png LICENSE ASSET-NOTICES.md README.md}
    set selected_root [file dirname $plugin_file]
    set pointer [file join $selected_root active-generation]
    if {[file exists $pointer]} {
        if {[file type $pointer] ne "file"} {error "Invalid active release pointer"}
        set stream [open $pointer r];set generation [string trim [read $stream 128]];close $stream
        if {![regexp {^[a-f0-9]{64}$} $generation]} {error "Invalid active release identity"}
        set selected_root [file join $selected_root generations $generation]
        if {![file isdirectory $selected_root]} {error "Selected release is missing; restore the previous release pointer"}
    }
    source [file join $selected_root product.tcl]
    unset selected_root pointer
}
