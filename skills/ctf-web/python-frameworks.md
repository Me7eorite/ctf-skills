# CTF Web - Python Framework Challenge Patterns

Use this file when a web challenge is built with Flask, Django, FastAPI, Starlette, Jinja2, Mako, Werkzeug, pickle, or Python-specific object behavior.

## Table of Contents

- [Triage](#triage)
- [Flask and Jinja2 SSTI](#flask-and-jinja2-ssti)
- [Pickle and Signed Cookie Chains](#pickle-and-signed-cookie-chains)
- [Django and FastAPI Mistakes](#django-and-fastapi-mistakes)
- [Python Object Pollution](#python-object-pollution)
- [Parser and Normalization Quirks](#parser-and-normalization-quirks)
- [Challenge Design Seeds](#challenge-design-seeds)
- [Sources Reviewed](#sources-reviewed)

## Triage

Fast indicators:

- Headers, errors, or templates mention Flask, Werkzeug, Jinja2, Django, FastAPI, Starlette, Uvicorn, Gunicorn, Mako, or `itsdangerous`.
- Cookies look like Flask signed sessions: dot-separated base64-ish segments.
- Source leaks show `render_template_string`, user-controlled `.format()`, f-strings, `pickle.loads`, `yaml.load`, `eval`, or unsafe template globals.
- Debug pages leak config, route maps, secret keys, or object reprs.

## Flask and Jinja2 SSTI

Pattern:

1. User input reaches `render_template_string` or a template context in an unsafe way.
2. Simple probes confirm expression evaluation.
3. Filters block obvious names such as `config`, `self`, parentheses, underscores, quotes, or `class`.
4. The solve walks through available Jinja globals, Python object attributes, or already-bound variables.

Design notes:

- Make the entry point clear: route parameter, profile name, task name, email template, or admin preview.
- If filters exist, include a reasoned bypass path rather than random payload golf.
- For challenge design, a config leak or controlled file read is often cleaner than full shell.

Common intended primitives:

- Read `app.config`, environment values, or local files.
- Reach Python builtins through function globals.
- Use object attribute traversal to mutate app state.
- Abuse custom template globals added only in a debug or challenge mode.

## Pickle and Signed Cookie Chains

Pattern:

1. A secret key leaks through XSS, source disclosure, debug output, or LFI.
2. The app trusts a signed cookie or backup blob.
3. A pickle payload executes during load, or a signed session flips a role and unlocks a second-stage pickle sink.

Design notes:

- Keep the chain fair by exposing the signing algorithm, framework, or source.
- Obfuscating pickle with base64, ROT13, compression, XOR, or database storage does not change the core risk; provide enough bytes for players to recognize the wrapper.
- Avoid real system compromise: make the payload read a local flag file or hit a local-only verification function.

## Django and FastAPI Mistakes

Useful lanes:

- Django `DEBUG=True` info leaks plus weak `SECRET_KEY` handling.
- Template autoescape bypass or custom template filters that call Python functions.
- Misconfigured `ALLOWED_HOSTS`, password reset links, or signed values.
- FastAPI/Pydantic mass assignment, hidden JSON fields, dependency injection mistakes, or path operation auth gaps.
- Starlette/FastAPI file upload and static-file path handling quirks.

Design notes:

- Provide OpenAPI docs, route names, or source snippets when hidden fields are required.
- Make auth differences visible between UI and API routes.

## Python Object Pollution

Pattern:

1. JSON body controls object attributes, task names, dict keys, or class-like structures.
2. The app merges untrusted data into objects or globals.
3. Players alter behavior indirectly: template globals, task manager fields, class attributes, or function dispatch tables.

This is analogous to prototype pollution in JavaScript, but it usually abuses Python dictionaries, object attributes, class variables, or module globals.

Design notes:

- Provide a readback endpoint so players can observe that a write changed server-side state.
- Keep mutation targets deterministic and resettable.

## Parser and Normalization Quirks

Useful lanes:

- `urllib.parse` vs framework routing differences.
- Path normalization differences between reverse proxy and Flask/Django.
- Python base64 decoders ignoring non-base64 bytes.
- YAML unsafe load, TOML/JSON type confusion, or CSV formula-like parsing.
- Unicode normalization in usernames, filenames, or authorization checks.

## Challenge Design Seeds

- Flask note app with stored XSS that leaks `SECRET_KEY`, then a signed pickle backup import reads the flag.
- Jinja2 task manager where a JSON API mutates an object later used as a template global.
- FastAPI admin route with dependency-based auth on UI routes but missing checks on export endpoints.
- Django debug-style challenge where a leaked signing key lets players forge a password reset token.

## Sources Reviewed

- TokyoWesterns CTF 2018 Shrine writeup: Flask/Jinja2 SSTI with blacklisted globals and stripped parentheses. https://ctftime.org/writeup/11036
- CSAW Finals 2018 NekoCat writeup: XSS to secret-key theft to Python pickle RCE. https://ctftime.org/writeup/12144
- idekCTF 2022 task manager writeup: Python object-pollution-like state mutation feeding Jinja behavior. https://kdxcxs.github.io/posts/wp/idekctf-2022-task-manager-wp/
