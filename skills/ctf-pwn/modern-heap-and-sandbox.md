# CTF Pwn - Modern Heap and Sandbox Patterns

Use this file for recent userland pwn challenge patterns that show up repeatedly in public writeups and official challenge packs.

## Table of Contents

- [Tcache and UAF Chains](#tcache-and-uaf-chains)
- [Seccomp and ORW](#seccomp-and-orw)
- [Parent-Child IPC Sandbox Escapes](#parent-child-ipc-sandbox-escapes)
- [Static ROP and Minimal Primitives](#static-rop-and-minimal-primitives)
- [Hybrid Web-to-Pwn Services](#hybrid-web-to-pwn-services)
- [Challenge Design Seeds](#challenge-design-seeds)
- [Sources Reviewed](#sources-reviewed)

## Tcache and UAF Chains

Common modern shape:

1. Menu object contains two heap pointers or a struct plus controlled string.
2. A stale reference after `free()` leaks heap or libc through a show/list action.
3. The exploit poisons tcache or creates an overlapping chunk.
4. Final write targets a hook, function pointer, vtable, exit handler, FILE structure, or return address depending on glibc and mitigations.

Authoring notes:

- Provide the exact libc and loader for heap challenges.
- Make at least one leak intentional and stable.
- If safe-linking or double-free checks are part of the lesson, include enough observable heap state to derive the needed value.
- Avoid unbounded brute force; bound attempts and test against the deployed container.

## Seccomp and ORW

Common shape:

1. `execve` is blocked, so shell is not the intended goal.
2. Allowed syscalls permit `open`/`openat`, `read`, and `write`, or a constrained equivalent.
3. The exploit builds an ORW chain or shellcode to read the flag directly.

Authoring notes:

- Include `seccomp-tools dump` output or a clear way to extract the BPF filter.
- Put the flag path in a discoverable location or leak it through strings/config.
- If `open` is blocked but `openat` works, make that distinction part of the clue.

## Parent-Child IPC Sandbox Escapes

Recent sandbox challenges often separate an untrusted child from a more privileged parent:

1. Child process runs code under strict seccomp.
2. Parent exposes pipe/socket/shared-memory commands for logging, file access, or challenge service logic.
3. Bug lives in the parent command parser, shared buffer, or message lifecycle.
4. Exploit pivots from child shellcode to parent-controlled file read or command execution.

Design notes:

- Document the IPC protocol enough for solvers to model it.
- Keep the parent bug reachable with allowed child syscalls.
- Add logging or source so the challenge is not just blind protocol guessing.

## Static ROP and Minimal Primitives

Useful lanes:

- Static binary with abundant gadgets and a small stack overflow.
- PIE disabled but NX enabled: direct ROP to `execve` or ORW.
- Full RELRO/no hooks: target stack pivot, `.bss`, `ret2csu`, SROP, or exit handlers.
- Tiny input where solvers chain reads to stage a larger payload.

Design notes:

- Static ROP is fair when gadget search is the lesson; provide enough input length or a staged read path.
- If a one-gadget would bypass the intended chain, use a libc or environment where constraints make the intended path preferable.

## Hybrid Web-to-Pwn Services

Pattern:

1. Web app exposes a native service through an API, image parser, game engine, or local admin endpoint.
2. Web bug creates reachability: XSS to localhost, SSRF to internal TCP, upload to parser, or auth bypass to admin API.
3. Native bug provides memory corruption and final flag access.

Authoring notes:

- Split the intended path into visible milestones: web primitive, native primitive, leak, control-flow hijack.
- Provide both web source and binary when the challenge is multi-domain.
- Add health checks for both layers.

## Challenge Design Seeds

- Notes service with UAF in user profiles: list leaks heap, edit-after-free corrupts tcache key, final overlap writes a controlled function pointer.
- Seccomp shellcode runner where only ORW is possible and the flag path is reconstructed from a parent-provided hint.
- Child sandbox can only read/write pipes; parent parser has signed length truncation into a fixed buffer.
- Web canvas API wraps a native pixel engine; coordinate arithmetic overflow creates negative OOB writes.

## Sources Reviewed

- HITCON CTF 2024 Setjmp writeup: tcache/UAF chain with heap and libc leak, tcache-key manipulation, and final hook overwrite. https://ctftime.org/writeup/39355
- HITCON CTF 2024 Seccomp Hell writeup: multi-stage Linux pwn under seccomp constraints. https://ctftime.org/writeup/39321
- M*CTF 2025 Ipsecs writeup: seccomp child plus parent IPC sandbox escape. https://ctftime.org/writeup/40503
- HackTheBox Business CTF 2025 official writeups: pwn mix including static ROP, format string, tcache double-free, off-by-null, and larger chained services. https://github.com/hackthebox/business-ctf-2025
