# Common for the Classic controller.
namespace eval ::plugins::mahlkonig_decent_sync {
    proc value {name {default ""}} {
        if {[uplevel #0 [list info exists $name]]} {return [uplevel #0 [list set $name]]}
        return $default
    }
    proc js {text} {
        set result "\""
        foreach char [split $text ""] {
            scan $char %c code
            if {$char eq "\""} {append result {\"}} \
            elseif {$char eq "\\"} {append result {\\}} \
            elseif {$code < 32} {append result [format {\u%04x} $code]} \
            else {append result $char}
        }
        append result "\""
        return $result
    }
    proc jo {args} {
        set pairs {}
        foreach {key val} $args {lappend pairs "[js $key]:$val"}
        return "\{[join $pairs ,]\}"
    }
    proc ja {items} {return [format {[%s]} [join $items ,]]}
    proc num {v} {
        if {![string is double -strict $v] || [regexp -nocase {nan|inf} $v]} {return null}
        return [format %.9g $v]
    }
    proc now {} {return [clock format [clock seconds] -gmt 1 -format {%Y-%m-%dT%H:%M:%SZ}]}
    proc atomic_write {path body} {
        set tmp "$path.tmp"
        set fd [open $tmp w];fconfigure $fd -encoding utf-8 -translation lf
        puts -nonewline $fd $body;flush $fd;close $fd
        file rename -force $tmp $path
    }
}
