# CTF Web - Java and Spring Challenge Patterns

Use this file when a web challenge is written in Java, Spring Boot, Thymeleaf, Tomcat, JSP, or a JVM framework. These notes are for authorized CTF targets and challenge design.

## Table of Contents

- [Triage](#triage)
- [Spring Boot and Thymeleaf SSTI](#spring-boot-and-thymeleaf-ssti)
- [Tomcat WAR and Java Source Recovery](#tomcat-war-and-java-source-recovery)
- [Java Deserialization](#java-deserialization)
- [Servlet and Filter Auth Mistakes](#servlet-and-filter-auth-mistakes)
- [Parser and Dependency Bugs](#parser-and-dependency-bugs)
- [Challenge Design Seeds](#challenge-design-seeds)
- [Sources Reviewed](#sources-reviewed)

## Triage

Fast indicators:

- Headers, errors, or paths mention `Spring`, `Whitelabel Error Page`, `Tomcat`, `JSESSIONID`, `.war`, `.jsp`, `Thymeleaf`, `SpEL`, or `Actuator`.
- Templates include `th:*` attributes or `[[...]]` / `${...}` expression syntax.
- Serialized Java blobs often start with `aced0005` in hex or `rO0AB` in base64.
- Java stack traces leak package names, controller methods, dependency versions, and template names.

Useful authoring clues:

- Java challenges become fairer when the source, stack trace, leaked WAR, or dependency manifest points at the intended sink.
- Avoid requiring players to guess a long gadget chain from nothing; include a detectable serialization format, dependency list, or DNS-only verification path.

## Spring Boot and Thymeleaf SSTI

Pattern:

1. A controller returns a view name or renders user-controlled text through Thymeleaf.
2. The attacker controls part of an expression context, view name, error page, or preview template.
3. SpEL or Thymeleaf expression evaluation reaches Java classes or Spring utility methods.
4. The final primitive is usually file read, directory listing, or command execution if the container has a shell.

Design notes:

- In modern Java containers, command execution may fail because distroless images lack `/bin/sh`; file-read payloads through Java/Spring utilities are often more reliable.
- WAF-style string filters are best used as a teaching device only when bypass clues exist: string concatenation, alternate utility classes, or error differences.
- A safe CTF version should put the flag in a local file inside the container and expose the vulnerable template preview only in the toy app.

Detection ideas:

- Probe harmless math or property access in the suspected template expression.
- Look for error messages naming `org.thymeleaf`, `org.springframework.expression`, or template parsing.
- Inspect whether the endpoint returns a literal string, a view name, or a parsed template.

## Tomcat WAR and Java Source Recovery

Pattern:

1. A web bug such as LFI, static file exposure, or backup download reveals `ROOT.war` or compiled `.class` files.
2. Players unpack the WAR and recover controllers, filters, templates, and dependency metadata.
3. The second stage is often Java reverse engineering: obfuscated handlers, hidden routes, custom auth, or crypto in server-side code.

Authoring notes:

- Put the intended clue in `WEB-INF/web.xml`, controller annotations, templates, or package names.
- If the code is obfuscated, make the web vulnerability lead naturally into reverse engineering; do not hide both stages behind guesswork.
- Keep the downloadable WAR small enough to inspect with `jar`, `javap`, CFR, FernFlower, or jadx.

## Java Deserialization

Pattern:

1. Cookie, POST body, cache entry, message queue, or uploaded file contains Java serialized data.
2. The application calls `ObjectInputStream.readObject()` or a framework deserializer on untrusted bytes.
3. Gadget availability depends on bundled libraries; blind detection can use URLDNS-style callbacks, while local CTFs can use visible file reads or command output.

Design notes:

- Include `pom.xml`, `build.gradle`, `WEB-INF/lib/`, or source so gadget selection is fair.
- Prefer a benign local side effect for validation, such as reading `/flag.txt` or writing a marker in a temp directory.
- If using ysoserial-shaped chains, pin Java and library versions.

## Servlet and Filter Auth Mistakes

Common CTF bugs:

- Filter protects `/admin` but not encoded or normalized equivalents.
- Controller trusts `X-Forwarded-*` or proxy headers without a trusted reverse proxy.
- Spring Security matcher protects one path style while the dispatcher routes another.
- Role checks are applied to UI routes but missing on JSON or export endpoints.
- Actuator or debug endpoints are exposed in the challenge container.

Design notes:

- Make the mismatch observable through route behavior, source, or logs.
- Include at least one normal request that shows the intended authorization boundary.

## Parser and Dependency Bugs

Useful lanes:

- XML/XXE through Java XML parsers, SAML, SOAP, or DOCX upload.
- Jackson polymorphic deserialization when `$type` or default typing is exposed.
- Spring Cloud/Gateway/Actuator/CVE-shaped bugs when version banners or manifests are provided.
- File upload parser differences between servlet container, framework, and storage layer.

## Challenge Design Seeds

- Spring Boot template preview where a restricted Thymeleaf expression can list `/app` and read a split flag filename.
- LFI reveals `ROOT.war`; decompilation shows a custom filter that trusts a normalized path differently than Tomcat.
- Java serialized remember-me cookie with a visible dependency list and a DNS-free validation path.
- Actuator-like debug endpoint leaks environment keys needed to forge a signed admin token.

## Sources Reviewed

- Real World CTF 2023 Dark Portal writeup: Java web bug leading to WAR recovery and Java handler reversing. https://gist.github.com/stong/5236143fdb6a3b656ac295e534988902
- Aero CTF 2021 Localization is hard writeup: Java/Thymeleaf SSTI challenge. https://ctftime.org/writeup/26230
- Aero CTF 2021 task index for category tags and alternate writeups. https://ctftime.org/task/14817
