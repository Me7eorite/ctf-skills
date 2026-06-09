# Reverse Engineering Challenge Design

Use this reference for synthetic reverse engineering challenge ideas. It is distilled from the former reverse solve skill, but is written for authors designing deterministic artifacts.

## Good Reverse Challenge Properties

- The artifact has a deterministic validation target.
- The core transformation can be reasoned about, emulated, patched, or solved.
- Obfuscation adds learning value rather than random noise.
- Players can make progress with common tooling before advanced methods are needed.

## Technique Lanes

| Lane | Easy | Medium | Hard |
| --- | --- | --- | --- |
| Crackme | strings, simple XOR | layered transforms, anti-debug | symbolic constraints, side-channel oracle |
| VM/bytecode | tiny instruction set | custom VM with encoded program | VM lifting, self-modifying bytecode |
| Runtime | ltrace/strace win | hooks, memory dump | anti-debug, signal handlers, timing tricks |
| Language | Python bytecode, .NET | Go/Rust/Swift patterns | packed, obfuscated, mixed runtime |
| Platform | WASM, APK basics | JNI, firmware config | embedded, drivers, unusual architecture |
| Visual/game | asset extraction | shader/game-state logic | custom format plus solver |

## Design Seeds

- A validator that transforms input through reversible position-dependent operations.
- A small VM whose bytecode checks the flag one block at a time.
- A mobile-style artifact with native validation in a shared library.
- A WASM module with hidden state and exported helper functions.
- A packed binary where unpacking reveals a simple but nontrivial checker.

## Anti-Patterns

- Huge opaque binaries with no meaningful clues.
- Anti-debugging that blocks all normal analysis without teaching a bypass.
- Random brute-force search spaces without pruning.
- Fake flags that are indistinguishable from real progress.
- Required commercial tooling when open alternatives would work.

## Validation Notes

- Provide expected file type, architecture, and run command.
- Include a reference solve script or patching/emulation notes.
- Ensure the flag checker accepts exactly the intended flag.
- Test on a clean environment with documented dependencies.
- Keep decoys educational and distinguishable after analysis.
