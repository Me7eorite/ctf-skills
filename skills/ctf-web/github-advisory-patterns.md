# CTF Web - GitHub Advisory Inspired Patterns

Use this file when designing web challenges from GitHub Advisory Database, GHSA, or CVE patterns. Convert advisories into toy services; do not require attacking real software.

## Table of Contents

- [Workflow](#workflow)
- [npm Advisory Patterns](#npm-advisory-patterns)
- [pip Advisory Patterns](#pip-advisory-patterns)
- [Maven Advisory Patterns](#maven-advisory-patterns)
- [Go Archive and Parser Patterns](#go-archive-and-parser-patterns)
- [Challenge Seeds](#challenge-seeds)
- [Sources Reviewed](#sources-reviewed)

## Workflow

For each advisory:

1. Extract the bug class.
2. Identify the data boundary: request body, URL, archive entry, template, signed token, serialized blob.
3. Replace the real product with a tiny toy app.
4. Add source or dependency metadata as a clue.
5. Make the final primitive local: read `/flag.txt`, access fictional admin data, or change toy config.
6. Include a patch note: allowlist, version bump, safe parser, safe serializer, or ownership check.

## npm Advisory Patterns

### Prototype Pollution

Source shape: message processing, deep merge, config parsing, localization, or template options allow keys such as `__proto__` or `constructor.prototype`.

CTF design:

- Use a localization editor or theme config endpoint.
- Let players observe polluted behavior through a preview route.
- Chain to Pug/EJS/SSR option gadget, admin flag, or local template read.

Good clues:

- `package-lock.json`
- vulnerable merge helper source
- render options read from a plain object

### SSR URL Resolution

Source shape: SSR framework resolves attacker-controlled paths with `new URL(path, base)`, but `//host` or backslash-like forms override the intended base.

CTF design:

- SSR preview fetches internal assets.
- Internal service exposes `/flag` only to the SSR container.
- Logs show the resolved URL after each request.

## pip Advisory Patterns

### Flask Key Rotation and Signed Sessions

Source shape: stale fallback key is used for signing or accepted longer than expected.

CTF design:

- Backup config leaks a stale challenge key.
- Players forge a Flask session or signed action token.
- Admin export reveals flag.

### Unsafe Deserialization in Data and AI Services

Source shape: model cache, job queue, connector, or agent feature deserializes untrusted bytes.

CTF design:

- Toy worker deserializes a local job blob.
- Payload returns local flag content as job output.
- Source exposes the feature flag or unsafe load call.

## Maven Advisory Patterns

### Spring and Struts Binding/Upload

Source shape: data binding, multipart upload, file metadata, or path normalization writes or exposes unexpected files.

CTF design:

- Java toy app accepts upload metadata and stores a rendered template.
- Players write a harmless template/config file inside the container.
- Next preview request reads the local flag variable.

### Java Deserialization

Source shape: Java object deserialization, JDBC parameters, connector config, or admin-triggered import.

CTF design:

- Provide dependency metadata and a local validation endpoint.
- Use an inert file-read gadget or a fake gadget chain implemented in source.
- Avoid requiring real ysoserial chains unless the dependencies are pinned.

## Go Archive and Parser Patterns

Source shape: archive extraction trusts symlinks, traversal paths, or common-prefix checks.

CTF design:

- Go app unpacks theme archives.
- Crafted ZIP writes outside the extraction directory into a template or config path.
- Preview route renders the overwritten local file.

## Challenge Seeds

- `localizer`: npm translation runtime prototype pollution flips `escape` in a renderer.
- `double-slash`: SSR route resolves `//internal/flag` to a toy internal host.
- `old-key`: Flask key rotation bug lets stale key forge a session.
- `model-cache`: unsafe job cache deserialization returns flag in a job result.
- `theme-unzip`: Go archive traversal overwrites a preview template.
- `java-upload`: Spring/Struts-like upload metadata controls a destination path.

## Sources Reviewed

- GitHub Advisory Database. https://github.com/advisories
- GHSA-6xv4-9cqp-92rh / CVE-2025-57353: messageformat prototype pollution. https://github.com/advisories/GHSA-6xv4-9cqp-92rh
- GHSA-q63q-pgmf-mxhr / CVE-2025-62427: Angular SSR SSRF. https://github.com/advisories/GHSA-q63q-pgmf-mxhr
- GHSA-4grg-w6v8-c28g / CVE-2025-47278: Flask fallback signing key. https://github.com/advisories/GHSA-4grg-w6v8-c28g
- GHSA-5w3j-gwgh-4rfv / CVE-2025-6544: H2O deserialization. https://github.com/advisories/GHSA-5w3j-gwgh-4rfv
- GHSA-7xcv-9j6c-2fmc / CVE-2025-60455: Modular Max Serve unsafe deserialization. https://github.com/advisories/GHSA-7xcv-9j6c-2fmc
- GHSA-36p3-wjmg-h94x / CVE-2022-22965: Spring Framework RCE shape. https://github.com/advisories/GHSA-36p3-wjmg-h94x
- GHSA-7vpp-9cxj-q8gv / CVE-2025-3445: Go archiver traversal. https://github.com/advisories/GHSA-7vpp-9cxj-q8gv
