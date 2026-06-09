# Web Challenge Design

Use this reference for synthetic web CTF challenge ideas. It is distilled from the former web solve skill, but is written for authors designing fair toy targets.

## Good Web Challenge Properties

- The app has a believable feature: notes, reports, exports, uploads, admin review, webhooks, search, login, or API keys.
- The trust boundary is visible through normal use.
- The vulnerability creates a clear primitive: read, write, forge, bypass, trigger bot, or call internal service.
- Deployment uses isolated HTTP services with seeded data and reset behavior.

## Technique Lanes

| Lane | Easy | Medium | Hard |
| --- | --- | --- | --- |
| Auth | IDOR, weak role check | JWT confusion, OAuth callback bug | SAML/OIDC chain, login state machine |
| Injection | SQLi login bypass | blind SQLi, NoSQL operator injection | second-order SQLi, parser differential |
| Server-side | path traversal, LFI | SSTI, SSRF to internal app | chained SSRF, deserialization, archive parser |
| Client-side | DOM XSS | CSP bypass, postMessage bug | browser bot chain, XS-leak, cache poisoning |
| Upload/export | extension bypass | polyglot upload, PDF renderer file read | multi-parser upload to RCE |
| Node/API | prototype pollution | sandbox escape in toy evaluator | pollution to SSRF/RCE chain |

## Design Seeds

- A customer-support portal where admin review turns stored HTML into a browser-bot challenge.
- A report exporter where server-side rendering can read local files through controlled assets.
- A webhook tester where URL parsing differs between validation and fetch.
- A team invite API with hidden authorization assumptions.
- A source-map leak that reveals signing logic for a forged session.

## Anti-Patterns

- A route named `/flag` with no clue path.
- Real external callback targets controlled by non-organizers.
- Excessive WAF bypass trivia without a learning objective.
- SQLi challenges that require guessing table names without schema clues.
- XSS challenges where bot behavior is undocumented or flaky.

## Validation Notes

- Include a health check route.
- Seed the same database state on every reset.
- Document bot cookies and visit behavior for authors.
- Pin framework and dependency versions when the bug depends on parser behavior.
