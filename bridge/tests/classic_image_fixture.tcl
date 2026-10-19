# Shared Classic image/scale contract, verified against de1app 1.46.1.1.
set ::logo_images {}
set ::logo_scales {}
proc image {operation type name args} {
    if {$operation ne "create" || $type ne "photo"} {error {unsupported image operation}}
    array set options $args
    if {![file isfile $options(-file)]} {error {logo asset missing}}
    dict set ::logo_images $name $options(-file)
    return $name
}
# Classic 1.46 exports the global scaler; DUI's image ensemble does not export photoscale.
proc photoscale {name sx sy} {
    if {![dict exists $::logo_images $name]} {error {logo must load before scaling}}
    dict set ::logo_scales $name [list $sx $sy]
}
