#!/bin/sh
set -eu

# Runs inside the challenge container. It does not create the chroot layout;
# that layout is built only by deploy/Dockerfile RUN steps during docker build.

if [ -z "${FLAG:-}" ]; then
  echo "FLAG environment variable is required" >&2
  exit 1
fi

printf '%s\n' "$FLAG" > /home/ctf/flag
chown root:ctf /home/ctf/flag
chmod 740 /home/ctf/flag
unset FLAG

/etc/init.d/xinetd start
sleep infinity
