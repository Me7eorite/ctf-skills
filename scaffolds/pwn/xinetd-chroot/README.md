# Pwn xinetd/chroot Scaffold

This scaffold is the default deployment skeleton for ordinary Pwn TCP services.
Copy the `deploy/` tree into the generated challenge, then replace only the
documented placeholder `{{SERVICE_PORT}}`.
The runtime identity is fixed to `ctf:ctf` with uid/gid `1000:1000`.

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
deploy/src/pwn
```

The generated `docker-compose.yml` must inject the literal `FLAG=flag{...}`
environment entry. The startup script writes that value to `/home/ctf/flag`
inside the container before starting xinetd.
