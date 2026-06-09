# CTF Reverse - WASM, Mobile, and Managed Runtime Patterns

Use this file when a reversing challenge is not a classic stripped ELF: WebAssembly, PyInstaller, Android/JNI, Go, Rust, .NET, or a custom VM/backdoor artifact.

## Table of Contents

- [WebAssembly](#webassembly)
- [PyInstaller and Python Bytecode](#pyinstaller-and-python-bytecode)
- [Android and JNI](#android-and-jni)
- [Go and Rust](#go-and-rust)
- [Custom VM and Backdoor Artifacts](#custom-vm-and-backdoor-artifacts)
- [Challenge Design Seeds](#challenge-design-seeds)
- [Sources Reviewed](#sources-reviewed)

## WebAssembly

Common shape:

1. Web page loads `.wasm` and a JS glue file.
2. The flag checker lives in exports, linear memory, or an imported callback.
3. Players recover logic via `wasm2wat`, decompilation, dynamic instrumentation, or direct memory inspection.
4. Some challenges compile C/C++/Rust to WASM, leaving recognizable runtime patterns.

Triage:

- Inspect imports/exports first.
- Search JS glue for memory offsets, exported function names, and input marshaling.
- Convert with `wasm2wat`; then identify loops, tables, and linear-memory comparisons.
- If logic is opaque, emulate or patch branches rather than fully decompile.

Authoring notes:

- Include the JS glue when the marshaling is part of the puzzle.
- Keep the checker deterministic; avoid browser-only timing assumptions unless the timing side channel is the lesson.

## PyInstaller and Python Bytecode

Common shape:

1. Binary is a PyInstaller bundle or compiled Python artifact.
2. Players extract `.pyc`, repair magic/version headers, and decompile or disassemble.
3. The logic uses Python bytecode quirks, marshaled constants, obfuscated strings, or runtime imports.

Triage:

- Look for `PYZ`, `pyimod`, Python version strings, and embedded archive markers.
- Match Python version before decompilation.
- If decompilers fail, use `dis`, constants, names, and control-flow reconstruction.

Design notes:

- Pin Python version or leak it in metadata.
- Prefer bytecode-level transformations that can be solved with `dis`, not only a fragile decompiler.

## Android and JNI

Common shape:

1. APK has Java/Kotlin UI with validation split into native `.so`.
2. JNI registration hides method names or dynamic loading chooses a native library.
3. Players combine jadx/apktool with Ghidra/Frida to recover the real check.

Triage:

- Read `AndroidManifest.xml`, resources, strings, and Java/Kotlin first.
- Search for `System.loadLibrary`, `RegisterNatives`, certificate checks, and debug/root checks.
- Hook Java methods or JNI functions when static names are obfuscated.

Design notes:

- Provide an emulator-friendly APK or native library.
- Keep anti-debug as a clue, not a wall; include a bypass route such as patching or Frida hooks.

## Go and Rust

Go:

- Use build info, `go version -m`, GoReSym, strings, and runtime type metadata.
- Remember Go strings are pointer+length pairs, and slices carry pointer, length, capacity.
- `embed.FS`, `-ldflags -X`, goroutines, channels, and panic messages often leak structure.

Rust:

- Demangle symbols when available.
- Look for panic strings, enum/Result/Option patterns, iterator-heavy code, and static byte arrays.
- Rust binaries may be large and noisy; start with strings, xrefs, and panic paths.

Design notes:

- For managed-runtime challenges, the lesson should be runtime-aware analysis, not just fighting binary size.
- Provide one stable validation function or comparison target.

## Custom VM and Backdoor Artifacts

Common shape:

1. Artifact embeds bytecode, script, shader, or custom instruction stream.
2. Players recover the instruction set, write a tracer/emulator, then solve constraints.
3. Backdoor-style challenges hide a privileged command, alternate key path, or patched dependency behavior.

Design notes:

- Keep the VM instruction set small enough to infer.
- Include a few visible test programs or outputs.
- For backdoored FOSS-style challenges, make diffing or symbol recovery possible.

## Challenge Design Seeds

- Browser game checker where `.wasm` exports `check`, linear memory holds an obfuscated target, and the JS glue reveals input layout.
- PyInstaller binary with one damaged `.pyc` header and a bytecode-level XOR/permutation checker.
- Android app where Java validates format but native JNI checks the secret transform.
- Go CLI with `embed.FS` containing encrypted levels and `-ldflags -X` build metadata as a key hint.
- Rust checker whose panic strings reveal enum states and guide a Z3 translation.

## Sources Reviewed

- osu!gaming CTF 2024 writeup collection: WebAssembly and game-themed reversing patterns. https://v0lk3n.github.io/writeup/Osu%21CTF2024/Osu%21CTF2024-WriteUp.html
- osu!gaming CTF 2024 writeup collection: alternate reversing and scripting notes. https://blog.maple3142.net/2024/03/03/osu-gaming-ctf-2024-writeups/
- HackTheBox Business CTF 2025 official writeups: PyInstaller reversing, ARM UART backdoor, C++ VM reversing, and backdoored software patterns. https://github.com/hackthebox/business-ctf-2025
- Real World CTF 2023 Dark Portal writeup: Java web/reverse hybrid via WAR recovery and static analysis. https://gist.github.com/stong/5236143fdb6a3b656ac295e534988902
