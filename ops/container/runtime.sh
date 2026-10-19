#!/bin/sh
# Only util-linux runs as root. Drop identity and all capabilities except binding
# the grinder's required port before any application/configuration code is read.
exec setpriv --reuid=10001 --regid=10001 --clear-groups \
    --bounding-set=-all,+net_bind_service --inh-caps=-all,+net_bind_service \
    --ambient-caps=-all,+net_bind_service --no-new-privs -- "$@"
