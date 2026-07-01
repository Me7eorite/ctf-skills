#!/bin/sh
# Add your startup script
set -eu
echo "$FLAG" > /home/ctf/flag

# DO NOT DELETE
/etc/init.d/xinetd start
sleep infinity
