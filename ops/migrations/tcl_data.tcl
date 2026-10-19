# Read and write Tcl dictionaries as data. Never source/evaluate settings files.
fconfigure stdin -encoding utf-8
fconfigure stdout -encoding utf-8
set mode [lindex $argv 0]
if {$mode eq "read"} {
    set value [read stdin]
    dict size $value
    dict for {key item} $value {
        binary scan [encoding convertto utf-8 $key] H* key_hex
        binary scan [encoding convertto utf-8 $item] H* item_hex
        puts "$key_hex\t$item_hex"
    }
} elseif {$mode eq "write"} {
    set result [dict create]
    foreach line [split [string trimright [read stdin] \n] \n] {
        if {$line eq ""} {continue}
        set parts [split $line \t]
        if {[llength $parts] != 2} {error "Invalid encoded dictionary"}
        dict set result [encoding convertfrom utf-8 [binary format H* [lindex $parts 0]]] \
            [encoding convertfrom utf-8 [binary format H* [lindex $parts 1]]]
    }
    puts -nonewline $result
} elseif {$mode eq "plugins"} {
    set result {}
    foreach item [read stdin] {
        if {$item eq "decent_sync"} {set item "mahlkonig_decent_sync"}
        if {[lsearch -exact $result $item] < 0} {lappend result $item}
    }
    puts -nonewline $result
} elseif {$mode eq "disable"} {
    set result {}
    foreach item [read stdin] {
        if {$item ni {decent_sync mahlkonig_decent_sync}} {lappend result $item}
    }
    puts -nonewline $result
} else {error "Unknown data operation"}
