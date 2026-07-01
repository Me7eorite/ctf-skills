# Pwn Docker Example

This is a cleaned-up xinetd + chroot template for modern pwn challenges.

## Layout

- `deploy/Dockerfile`
- `deploy/docker-compose.yml`
- `deploy/_files/start.sh`
- `deploy/_files/ctf.xinetd`
- `deploy/src/Makefile`
- `deploy/src/pwn.c`

## Notes

- The Dockerfile is the only place that should do image-building work.
- `start.sh` only prepares runtime state and starts xinetd.
- `ctf.xinetd` runs the service through `/usr/sbin/chroot`.
- `Makefile` is included so the Dockerfile can build the challenge binary.

## Quick start

```bash
cd pwn_docker_example/deploy
docker compose up --build
```
