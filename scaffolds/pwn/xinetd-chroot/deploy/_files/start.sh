#!/bin/sh
set -eu

if [ "${DASFLAG:-}" ]; then
    INSERT_FLAG="$DASFLAG"
    export DASFLAG=no_FLAG
elif [ "${FLAG:-}" ]; then
    INSERT_FLAG="$FLAG"
    export FLAG=no_FLAG
elif [ "${GZCTF_FLAG:-}" ]; then
    INSERT_FLAG="$GZCTF_FLAG"
    export GZCTF_FLAG=no_FLAG
else
    INSERT_FLAG="flag{TEST_Dynamic_FLAG}"
fi

printf '%s\n' "$INSERT_FLAG" > /home/ctf/flag
chmod 711 /home/ctf/{{BINARY_NAME}}

/etc/init.d/xinetd start
sleep infinity
