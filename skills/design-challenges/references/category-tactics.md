# Category Tactics

Category-specific design seeds, technique lanes, anti-patterns, and container conventions. The Easy/Medium/Hard/Expert columns map to the difficulty rubric defined elsewhere; Phase 2 will add a dedicated `difficulty-rubric.md`. Until then:

- **easy** = 1 考点, basic knowledge, single-step solve.
- **medium** = 2–3 考点 chained inside a believable business scenario.
- **hard** = 3–4 考点 chained, multi-step, requires cross-domain reasoning.
- **expert** = 0day-style trick / novel chain / unusual constraints. MUST be summarized in a `novelty` field.

---

## Web

### Good Properties
- App has a believable feature: notes, reports, exports, uploads, admin review, webhooks, search, login, API keys.
- Trust boundary is visible through normal use.
- Vulnerability creates a clear primitive: read, write, forge, bypass, trigger bot, or call internal service.
- Isolated HTTP services with seeded data and reset behavior.

### Technique Lanes
| Lane | Easy | Medium | Hard | Expert |
| --- | --- | --- | --- | --- |
| Auth | IDOR, weak role check | JWT confusion, OAuth callback bug | SAML/OIDC chain, login state machine | Custom signing scheme bypass / forgery via algorithm confusion in in-house JWS |
| Injection | SQLi login bypass | blind SQLi, NoSQL operator injection | second-order SQLi, parser differential | Differential between two parsers in the same request chained with cache poisoning |
| Server-side | Path traversal, LFI | SSTI, SSRF to internal app | Chained SSRF, deserialization, archive parser | Request smuggling + chained SSRF + custom gadget chain |
| Client-side | DOM XSS | CSP bypass, postMessage bug | Browser bot chain, XS-leak, cache poisoning | Novel browser side-channel + bot chain + state oracle |
| Upload/export | Extension bypass | Polyglot upload, PDF renderer file read | Multi-parser upload to RCE | Exotic format chain (archive → template → RCE) requiring stage-wise unwrap |
| Node/API | Prototype pollution | Sandbox escape in toy evaluator | Pollution to SSRF/RCE chain | Nested pollution through unusual library + delayed gadget |

### Design Seeds
- Customer-support portal where admin review turns stored HTML into a browser-bot challenge.
- Report exporter where server-side rendering can read local files through controlled assets.
- Webhook tester where URL parsing differs between validation and fetch.
- Team invite API with hidden authorization assumptions.
- Source-map leak that reveals signing logic for a forged session.

### Anti-Patterns
- Route named `/flag` with no clue path.
- Real external callback targets controlled by non-organizers.
- Excessive WAF bypass trivia without a learning objective.
- SQLi that requires guessing table names without schema clues.
- XSS challenges where bot behavior is undocumented or flaky.

### Container Notes
- Reuse base image's non-root service account when available (`www-data:/var/www/html`, Tomcat `tomcat`).
- Bind unprivileged container port (e.g. `8080`); map host port `80` if needed.
- No Compose `volumes`; copy in source/seed data during build.
- Treat root, capabilities, privileged mode, and host mounts as exceptional; document why.
- Include a health-check route; reset DB state on every restart.

---

## Pwn

### Good Properties
- Vulnerable primitive is intentional, inspectable, reachable.
- Mitigations match the intended technique.
- Exploit is reliable under the deployed libc/kernel/container.
- Service has bounded input, stable prompts, reference exploit.

### Technique Lanes
| Lane | Easy | Medium | Hard | Expert |
| --- | --- | --- | --- | --- |
| Stack | ret2win, no canary | canary leak, ret2libc | constrained ROP, SROP, seccomp | seccomp + custom syscall scheme + minimal gadget set |
| Format string | leak + win overwrite | GOT overwrite, partial writes | blind format string, constrained charset | partial-byte chain across multiple unaligned writes with custom calc |
| Heap | simple UAF | tcache poisoning | FSOP, allocator internals, custom heap | Exotic mitigation bypass (SafeUnlinking-like variant) |
| Integer/OOB | signedness check | OOB read/write primitive | parser-driven object corruption | parser corruption + race-stabilized exploit |
| Sandbox | restricted shell | syscall filter bypass | VM escape, multi-stage sandbox | nested sandbox + side-channel exfil |
| Kernel | simple module bug | KASLR leak + kROP | race, cross-cache, namespace constraints | unpatched kernel-style bug with custom mitigations |

### Design Seeds
- Menu service with a clear UAF and deliberate libc leak path.
- Note service where format strings leak canary and redirect control flow.
- File parser with a bounded but wrong integer conversion.
- Toy bytecode VM whose memory model creates an OOB primitive.
- Seccomp service where the intended solve uses `open/read/write` or SROP.

### Anti-Patterns
- Exploits relying on luck or unbounded brute force.
- Hidden menu commands with no discoverable clue.
- Remote libc mismatch between provided files and deployment.
- Race conditions without stabilization guidance.
- Heap challenges where unintended one-gadget shortcuts bypass the lesson.

### Container Notes
- Run as unprivileged `ctf` from `/home/ctf` by default.
- Provide exact binary, libc, loader, Dockerfile.
- Reference exploit must run against the container, not just locally.
- Generous service timeouts for iterative exploit development.
- Document mitigations and expected `checksec` output.
- Privileged / cap-NET_ADMIN / setuid only with documented technical justification.

---

## Reverse

### Good Properties
- Artifact has a deterministic validation target.
- Core transformation can be reasoned about, emulated, patched, or solved.
- Obfuscation adds learning value, not random noise.
- Players make progress with common tooling before advanced methods.

### Technique Lanes
| Lane | Easy | Medium | Hard | Expert |
| --- | --- | --- | --- | --- |
| Crackme | strings, simple XOR | layered transforms, anti-debug | symbolic constraints, side-channel oracle | obfuscated VM + symbolic + side-channel chain |
| VM/bytecode | tiny instruction set | custom VM with encoded program | VM lifting, self-modifying bytecode | nested VM with reflective opcodes |
| Runtime | ltrace/strace win | hooks, memory dump | anti-debug, signal handlers, timing tricks | custom syscall hook + JIT validator |
| Language | Python bytecode, .NET | Go/Rust/Swift patterns | packed, obfuscated, mixed runtime | hand-rolled packer + custom loader stub |
| Platform | WASM, APK basics | JNI, firmware config | embedded, drivers, unusual architecture | undocumented architecture or custom bus |
| Visual/game | asset extraction | shader/game-state logic | custom format plus solver | game-state machine requiring constraint solver |

### Design Seeds
- Validator that transforms input through reversible position-dependent operations.
- Small VM whose bytecode checks the flag one block at a time.
- Mobile-style artifact with native validation in a shared library.
- WASM module with hidden state and exported helper functions.
- Packed binary where unpacking reveals a simple but nontrivial checker.

### Anti-Patterns
- Huge opaque binaries with no meaningful clues.
- Anti-debugging that blocks all normal analysis without teaching a bypass.
- Random brute-force search spaces without pruning.
- Fake flags indistinguishable from real progress.
- Required commercial tooling when open alternatives would work.

---

## Crypto

Good crypto challenges expose a broken construction, oracle, parameter choice, or implementation flaw with enough information for deterministic solving.

| Lane | Easy | Medium | Hard | Expert |
| --- | --- | --- | --- | --- |
| Classical | substitution, simple XOR | Vigenère, key reuse | unknown rotor variant | hybrid scheme requiring frequency + structural analysis |
| RSA | small e | Wiener, related messages | Coppersmith, partial-d leak | novel parameter-choice attack across multiple keys |
| Symmetric | ECB cut-paste | CBC bit-flip | length-extension chain | nonce-reuse exploitation in AEAD with custom mode |
| Asymmetric / ECC | weak curve params | small subgroup | invalid curve attack | pairing-based oracle requiring multi-step reduction |
| Protocol | replay | nonce reuse | oracle protocol | lattice/ZKP-style requiring careful math path |

Avoid opaque "guess the cipher" puzzles and impossible parameter sizes.

---

## Forensics

Good forensics challenges provide artifacts with a recoverable story: packet capture, disk image, memory dump, stego, logs, device traces.

| Lane | Easy | Medium | Hard | Expert |
| --- | --- | --- | --- | --- |
| Network | DNS exfil, plain HTTP | TLS keylog use | layered protocol carve | malformed/proprietary protocol carve |
| Disk | deleted-file recovery | timeline reconstruction | journal/EXT4 carving | custom FS carve with metadata reconstruction |
| Memory | strings & process list | volatility plugin chain | kernel structure walk | custom kernel-mode artifact requiring own plugin |
| Stego | LSB image/audio | polyglot files | custom carrier (video, archive) | multi-carrier chain with novel encoding |
| Peripheral | USB HID | keyboard timing | serial protocol parse | undocumented bus / firmware logic analysis |

Avoid massive noisy artifacts without clear triage clues.

---

## Misc / OSINT / Malware / AI

| Track | Easy | Medium | Hard | Expert |
| --- | --- | --- | --- | --- |
| Misc | encoding, QR | Python jail, esolang | constraint puzzle, custom protocol | multi-stage protocol with novel state machine |
| OSINT | username trail, single-photo geoloc | DNS/cert history | multi-source pivot on fictional profiles | adversarial-author pivot across owned domains |
| Malware (inert) | string deobfuscation | toy C2 PCAP | static unpack of benign sample | YARA-resistant variant requiring custom signature |
| AI/ML | prompt injection toy app | model extraction (toy data) | adversarial sample crafting | backdoor + data poisoning in synthetic pipeline |

Safety: all assets fictional or organizer-owned. No real malware behavior, no real users.

---

## Cross-Category Rules

- Every challenge MUST have one and only one primary_technique. Secondary techniques go in `secondary_technique` or in `techniques[]`.
- For medium and harder, the scenario MUST sit inside a believable business or product context — not "a toy service that has bug X".
- Hard challenges must chain at least two primitives or two reasoning steps. Reusing the same technique across multiple steps does not count as chaining.
- Expert challenges MUST include a `novelty` field summarizing what makes this design non-trivial (0day-style mechanic, novel chain, custom protocol, parser differential, etc.). Without `novelty`, an expert design is rejected.
