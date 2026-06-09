# Pwn Challenge Design

Use this reference for synthetic binary exploitation challenge ideas. It is distilled from the former pwn solve skill, but is written for authors designing reliable local services.

## Good Pwn Challenge Properties

- The vulnerable primitive is intentional, inspectable, and reachable.
- Mitigations match the intended technique.
- The exploit is reliable under the deployed libc/kernel/container.
- The service has bounded input, stable prompts, and a reference exploit.

## Technique Lanes

| Lane | Easy | Medium | Hard |
| --- | --- | --- | --- |
| Stack | ret2win, no canary | canary leak, ret2libc | constrained ROP, SROP, seccomp |
| Format string | leak + win overwrite | GOT overwrite, partial writes | blind format string, constrained charset |
| Heap | simple UAF | tcache poisoning | FSOP, allocator internals, custom heap |
| Integer/OOB | signedness check | OOB read/write primitive | parser-driven object corruption |
| Sandbox | restricted shell | syscall filter bypass | VM escape, multi-stage sandbox |
| Kernel | simple module bug | KASLR leak + kROP | race, cross-cache, namespace constraints |

## Design Seeds

- A menu service with a clear UAF and a deliberate libc leak path.
- A note service where format strings leak canary and redirect control flow.
- A file parser with a bounded but wrong integer conversion.
- A toy bytecode VM whose memory model creates an OOB primitive.
- A seccomp service where the intended solve uses open/read/write or SROP.

## Anti-Patterns

- Exploits that work only by luck or unbounded brute force.
- Hidden menu commands with no discoverable clue.
- Remote libc mismatch between provided files and deployment.
- Race conditions without stabilization guidance.
- Heap challenges where unintended one-gadget shortcuts bypass the lesson.

## Validation Notes

- Provide the exact binary, libc, loader, and Dockerfile when relevant.
- Run the reference exploit against the container, not only locally.
- Check exploit reliability across repeated runs.
- Document mitigations and expected `checksec` output for authors.
- Add service timeouts that are generous enough for intended exploitation.
- Run the final container as unprivileged user `ctf` from `/home/ctf` by
  default, with the challenge binary and runtime files owned intentionally.
- Do not use root, privileged mode, broad capabilities, host devices, or host
  networking for ordinary userland pwn services.
- When a kernel, namespace, device, setuid, or capability-focused challenge
  genuinely needs elevated behavior, grant only the exact requirement and
  document the exception in metadata, validation notes, and the writeup.
