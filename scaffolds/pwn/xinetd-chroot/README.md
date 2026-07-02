# Pwn xinetd/chroot Scaffold

This scaffold is the default deployment skeleton for ordinary Pwn TCP services.
Copy the `deploy/` tree into the generated challenge, then replace only the
documented placeholders such as `{{BINARY_NAME}}` and `{{SERVICE_PORT}}`.
The image creates a fixed `ctf` user/group with uid/gid `1000:1000`, and
xinetd runs the challenge with `--userspec=1000:1000`.
It follows the normalized `ctf-docker-template/pwn-ubuntu_20.04` xinetd/chroot
layout while preserving this factory's `deploy/` directory contract.

Host boundary:

- Host commands may run `docker build`, `docker-compose`, `file`, checksum
  tools, and the reference exploit.
- Host commands must not run the chroot setup commands directly.
- Commands such as `cp -R /lib* /home/ctf`, `mknod /home/ctf/dev/null ...`,
  and `cp /bin/ls /home/ctf/bin` are intentionally inside
  `deploy/Dockerfile` `RUN` steps. They execute inside the Docker build
  container and copy the image/container filesystem, not the host filesystem.

Expected generated tree:

```text
deploy/Dockerfile
deploy/docker-compose.yml
deploy/_files/start.sh
deploy/_files/ctf.xinetd
deploy/src/<source files and Makefile>
```

The generated `docker-compose.yml` must inject the literal `FLAG=flag{...}`
environment entry. The startup script accepts `DASFLAG`, `FLAG`, or
`GZCTF_FLAG`, writes the selected value to `/home/ctf/flag`, clears the
environment variable, and then starts xinetd.
