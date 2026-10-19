# Complete-package updates from the paired server. Downloads are never evaluated.
namespace eval ::plugins::mahlkonig_decent_sync {
    proc update_base {} {
        variable settings
        if {![info exists settings(server_id)] || $settings(server_id) eq ""} {error "Pair the server first"}
        return "[normalize_server $settings(bridge_url)]/api/v1/updates/classic"
    }
    proc update_problem {label} {
        variable update_phase;variable update_status;variable update_payload
        set update_phase error;set update_status $label;set update_payload {}
    }
    proc update_progress {limit token total current} {
        if {$total > $limit || $current > $limit} {catch {::http::reset $token {size limit}}}
    }
    proc update_fetch {url limit callback} {
        variable update_job
        if {[catch {::http::geturl $url -binary 1 -timeout 8000 \
            -progress [list [namespace current]::update_progress $limit] \
            -command [list [namespace current]::update_reply $update_job $limit $callback]}]} {update_problem "Download failed"}
    }
    proc update_reply {job limit callback token} {
        variable update_job;variable update_server;variable settings
        set ok 0;set data ""
        catch {
            set data [::http::data $token]
            set ok [expr {[::http::status $token] eq "ok" && [::http::ncode $token] == 200 && [string length $data] <= $limit}]
        }
        ::http::cleanup $token
        if {$job != $update_job} {return}
        if {$update_server ne "$settings(bridge_url)|$settings(server_id)"} {update_problem "Server changed";return}
        if {!$ok} {update_problem "Download failed";return}
        if {[catch {{*}$callback $data}]} {update_problem "Update rejected"}
    }
    proc update_version {text} {
        if {![regexp {^([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})(-(alpha|beta|rc)\.([0-9]{1,6}))?$} $text _ major minor patch extra phase serial]} {error "Invalid release version"}
        set rank [lsearch -exact {alpha beta rc {}} $phase]
        if {$serial eq ""} {set serial 0}
        return [list $major $minor $patch $rank $serial]
    }
    proc update_newer {candidate current} {
        foreach a [update_version $candidate] b [update_version $current] {
            if {$a != $b} {return [expr {$a > $b}]}
        }
        return 0
    }
    proc check_updates {} {
        variable update_phase;variable update_status;variable update_manifest;variable update_server;variable update_job;variable settings
        if {$update_phase in {checking downloading installed}} {return}
        if {[catch {update_base}]} {update_problem "Pair the server first";return}
        incr update_job
        set update_server "$settings(bridge_url)|$settings(server_id)";set update_manifest {}
        set update_phase checking;set update_status "Checking…"
        update_fetch "$settings(bridge_url)/api/v1/identity" 16384 [namespace code update_health]
    }
    proc update_health {bytes} {
        variable update_server_version;variable settings
        set body [::json::json2dict [encoding convertfrom utf-8 $bytes]]
        if {[dict get $body product] ne "mahlkonig-decent-sync" || [dict get $body server_id] ne $settings(server_id)} {error "Wrong server"}
        set update_server_version [dict get $body version]
        update_fetch [update_base] 16384 [namespace code update_catalogue]
    }
    proc update_catalogue {bytes} {
        variable version;variable update_manifest;variable update_phase;variable update_status;variable update_server_version
        variable package_files;variable plugin_file;variable settings;variable update_generation
        set manifest [::json::json2dict [encoding convertfrom utf-8 $bytes]]
        set candidate [dict get $manifest version]
        update_version $candidate
        if {[dict get $manifest schema] != 2 || [dict get $manifest plugin] ne "mahlkonig_decent_sync" ||
            [dict get $manifest server_id] ne $settings(server_id) || [dict get $manifest server_release] ne $update_server_version ||
            [dict get $manifest adapter_version] ne "classic-0.5" || [dict get $manifest controller_contract] != 2 ||
            [dict get $manifest bootstrap_api] != 1 || [lsort [dict keys [dict get $manifest files]]] ne [lsort $package_files]} {error "Incompatible package"}
        set total 0
        dict for {name spec} [dict get $manifest files] {
            if {![regexp {^[a-f0-9]{64}$} [dict get $spec sha256]] ||
                ![string is integer -strict [dict get $spec bytes]] || [dict get $spec bytes] < 1 || [dict get $spec bytes] > 2097152} {error "Invalid file"}
            incr total [dict get $spec bytes]
        }
        if {$total > 4194304} {error "Package too large"}
        if {[dict get $manifest files plugin.tcl sha256] ne [::sha2::sha256 -hex [update_read_binary $plugin_file]]} {
            update_problem "Loader update requires assisted installation";return
        }
        if {![update_newer $candidate $version]} {set update_phase current;set update_status "Up to date";return}
        set update_generation [::sha2::sha256 -hex $bytes]
        set update_manifest $manifest;set update_phase available;set update_status "Update available"
    }
    proc update_idle_reason {} {
        variable shot_id;variable pending_scale;variable active_cycle;variable last_server_ack;variable busy;variable outbox;variable server_testing
        if {$shot_id ne "" || $pending_scale ne "" || $active_cycle ne "" || [::de1::state::current_state] ni {Idle Sleep}} {return "Finish current cycle"}
        if {$busy || $server_testing || ![file isdirectory $outbox] || [llength [glob -nocomplain -directory $outbox *.json]]} {return "Sending saved events"}
        if {[clock seconds]-$last_server_ack > 10} {return "Server offline"}
        return ""
    }
    proc update_remote_idle {bytes} {
        set status [::json::json2dict [encoding convertfrom utf-8 $bytes]]
        return [expr {[dict get $status update_idle_reason] eq "null"}]
    }
    proc update_read_binary {path} {
        if {[file type $path] ne "file"} {error "Expected a regular file"}
        set fd [open $path rb];set bytes [read $fd];close $fd;return $bytes
    }
    proc update_write_binary {path bytes} {
        set fd [open $path {WRONLY CREAT EXCL}]
        fconfigure $fd -translation binary -encoding binary
        puts -nonewline $fd $bytes;flush $fd;close $fd
    }
    proc update_signature {} {
        variable module_root;variable package_files;variable plugin_file
        set signature {}
        foreach name $package_files {
            set path [file join $module_root $name]
            if {$name eq "plugin.tcl"} {set path $plugin_file}
            dict set signature $name [::sha2::sha256 -hex [update_read_binary $path]]
        }
        set pointer [file join [file dirname $plugin_file] active-generation]
        dict set signature pointer [expr {[file exists $pointer] ? [update_read_binary $pointer] : "root"}]
        return $signature
    }
    proc install_update {} {
        variable update_phase;variable update_status;variable settings;variable update_original;variable update_payload
        if {$update_phase ne "available"} {return}
        set reason [update_idle_reason]
        if {$reason ne ""} {set update_status $reason;return}
        if {[catch {set update_original [update_signature]}]} {update_problem "Installed files changed";return}
        set update_payload {}
        set update_phase downloading;set update_status "Checking idle…"
        update_fetch "$settings(bridge_url)/api/v1/status" 2097152 [namespace code update_preflight]
    }
    proc update_preflight {bytes} {
        variable update_phase;variable update_status
        if {![update_remote_idle $bytes]} {set update_phase available;set update_status "Finish current cycle";return}
        set update_status "Downloading…";update_next
    }
    proc update_next {} {
        variable update_manifest;variable update_payload;variable settings
        dict for {name spec} [dict get $update_manifest files] {
            if {![dict exists $update_payload $name]} {
                update_fetch "[update_base]/files/$name" [dict get $spec bytes] [list [namespace current]::update_downloaded $name]
                return
            }
        }
        update_fetch "$settings(bridge_url)/api/v1/status" 2097152 [namespace code update_commit]
    }
    proc update_downloaded {name bytes} {
        variable update_manifest;variable update_payload
        set spec [dict get $update_manifest files $name]
        if {[string length $bytes] != [dict get $spec bytes] || [::sha2::sha256 -hex $bytes] ne [dict get $spec sha256]} {error "Hash mismatch"}
        if {[file extension $name] eq ".tcl" && ![info complete [encoding convertfrom utf-8 $bytes]]} {error "Incomplete Tcl file"}
        if {$name eq "product.tcl" && (![regexp -line {^    variable version ([^\r\n ]+)$} [encoding convertfrom utf-8 $bytes] _ candidate] || $candidate ne [dict get $update_manifest version])} {error "Version mismatch"}
        dict set update_payload $name $bytes
        update_next
    }
    proc update_commit {bytes} {
        variable update_phase;variable update_status;variable update_payload;variable update_original;variable plugin_file;variable update_generation
        set reason [update_idle_reason]
        if {$reason ne "" || ![update_remote_idle $bytes]} {
            set update_phase available;set update_status [expr {$reason ne "" ? $reason : "Finish current cycle"}];set update_payload {};return
        }
        if {[update_signature] ne $update_original} {update_problem "Installed files changed";return}
        set root [file dirname $plugin_file]
        set generations [file join $root generations]
        set staged [file join $generations ".$update_generation.pending"]
        set target [file join $generations $update_generation]
        if {[catch {
            file mkdir $generations
            # Incomplete generations are never selected by the loader.
            if {[file exists $staged]} {file delete -force $staged}
            file mkdir $staged
            dict for {name body} $update_payload {
                set path [file join $staged $name]
                update_write_binary $path $body
                if {[update_read_binary $path] ne $body} {error "Write verification failed"}
            }
            if {[file exists $target]} {file delete -force $staged} else {file rename $staged $target}
            dict for {name body} $update_payload {
                if {[update_read_binary [file join $target $name]] ne $body} {error "Staged package changed"}
            }
            atomic_write [file join $root previous-generation] [dict get $update_original pointer]
            atomic_write [file join $root active-generation] $update_generation
        }]} {
            catch {file delete -force $staged};update_problem "Install failed";return
        }
        # Keep the active selection and its previous generation. The original
        # package remains the loader's explicit first-install rollback target.
        set previous [dict get $update_original pointer]
        foreach directory [glob -nocomplain -types d -directory $generations *] {
            set name [file tail $directory]
            if {[regexp {^[a-f0-9]{64}$} $name] && $name ne $update_generation && $name ne $previous} {
                catch {file delete -force $directory}
            }
        }
        set update_payload {};set update_original {}
        set update_phase installed;set update_status "Reopen de1app"
    }
    proc update_action {} {
        variable update_phase
        if {$update_phase eq "available"} {install_update} else {check_updates}
    }
    proc update_button_label {} {
        variable update_phase
        switch -- $update_phase {
            available {return Install}
            checking {return "Checking…"}
            downloading {return "Installing…"}
            installed {return Installed}
            default {return "Check updates"}
        }
    }
    proc update_version_label {} {
        variable update_manifest;variable update_phase
        if {$update_phase in {available downloading installed} && $update_manifest ne ""} {return [dict get $update_manifest version]}
        return "—"
    }
}
