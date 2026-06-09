# CTF Challenge Factory Monorepo

This repository contains a runnable challenge-generation project and its local
CTF authoring skills.

## Project

`challenge-factory/` is the primary application. It provides:

- Hermes shard orchestration
- Web, Pwn, and Reverse challenge generation
- build and reference-exploit validation
- a Tailwind dashboard for queues, challenges, and logs

Start it with:

```bash
cd challenge-factory
uv run challenge-factory serve
```

Then open `http://127.0.0.1:4173`.

## Skills

All reusable skill content lives under `skills/`:

```text
skills/
  design-challenges/
  ctf-web/
  ctf-pwn/
  ctf-reverse/
  ctf-crypto/
  ...
```

`skills/design-challenges/` is the organizer-facing design skill used by the
factory. The `ctf-*` directories are technique catalogs that provide deeper
category material.

## Repository Layout

```text
challenge-factory/  runnable application and dashboard
skills/             standalone skills and technique catalogs
scripts/            repository maintenance tools
tests/              skill structure and security tests
```

The application reads skills by filesystem path. It does not require copying
them into the application directory.
