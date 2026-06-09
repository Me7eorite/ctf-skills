# Quality Gate

Apply this checklist before finalizing a challenge or challenge pack.

## Event-Level Checks

- Category mix matches the requested event plan.
- Difficulty progression is plausible for the audience.
- Each major category has at least one easy on-ramp.
- Names and prompts fit the event theme without leaking the trick.
- No category is dominated by one repeated technique.
- Hard and expert challenges have reliable reference solves.

## Challenge-Level Checks

- The learning objective is specific and observable.
- The intended path has no unexplained leaps.
- The flag location follows naturally from the solve path.
- The player prompt is spoiler-free but actionable.
- Hints are staged from gentle to direct.
- Artifacts are small enough and documented enough for distribution.
- Validation can be run by another author.
- Reset and health-check behavior is defined for services.

## Safety Checks

- Targets are synthetic and organizer-owned.
- Credentials, identities, logs, and domains are fictional.
- No design requires attacking real third-party systems.
- No real malware behavior is required.
- Remote services are containerized or otherwise isolated.
- Destructive-looking actions are confined to disposable artifacts.

## Fairness Checks

- Avoid guessing-heavy hidden endpoints.
- Avoid dependency on one obscure tool unless the challenge teaches that tool.
- Avoid brittle race windows unless bounded and stabilized.
- Avoid excessive artifact noise that does not support the intended path.
- Avoid flags in metadata unless the challenge is explicitly about metadata.

## Revision Actions

When a challenge fails the gate:

- Add an observable clue.
- Narrow the artifact.
- Replace a duplicate technique.
- Add a validation script.
- Pin a dependency version.
- Split a multi-trick design into separate challenges.
- Downgrade or upgrade difficulty based on actual solver burden.
