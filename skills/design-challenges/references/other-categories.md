# Other Category Design Notes

Use this reference when a challenge pack needs categories beyond web, pwn, and reverse. Keep these lanes lightweight unless the user asks to expand them.

## Crypto

Good crypto challenges expose a broken construction, oracle, parameter choice, or implementation flaw with enough information for deterministic solving.

Design lanes:

- Classic ciphers and encodings for beginner tracks.
- RSA parameter mistakes, oracle protocols, or related-message setups.
- Stream cipher keystream reuse, weak PRNGs, and nonce misuse.
- Lattice, ECC, or ZKP-inspired tasks only when the intended math path is documented.

Avoid opaque "guess the cipher" puzzles and impossible parameter sizes.

## Forensics

Good forensics challenges provide artifacts with a recoverable story: packet capture, disk image, memory dump, image/audio stego, logs, or device traces.

Design lanes:

- PCAP reconstruction, DNS/HTTP exfiltration, TLS keylog use.
- Deleted-file recovery and timeline reconstruction.
- Image, audio, video, or archive steganography.
- USB HID, keyboard, serial, or peripheral traces.

Avoid massive noisy artifacts without clear triage clues.

## Misc

Good misc challenges teach a compact trick or nonstandard environment.

Design lanes:

- Python or Bash jails with explicit sandbox boundaries.
- Encoding, Unicode, QR/barcode, esolang, or constraint puzzles.
- Game, VM, or protocol mini-challenges.

Avoid arbitrary trivia and hidden magic constants.

## OSINT

Good OSINT challenges use fictional or organizer-owned assets with reproducible evidence.

Design lanes:

- Fictional social profiles and username trails.
- Geolocation from images, maps, or public records created for the event.
- DNS, archive, certificate, or repository trails on owned domains.

Never require investigating real private people.

## Malware

Good malware-themed challenges are inert and educational.

Design lanes:

- Simulated config extraction.
- Toy C2 traffic in PCAPs.
- Benign scripts with obfuscation and no live persistence.
- YARA or static-analysis exercises over harmless samples.

Do not ship live malware behavior.

## AI/ML

Good AI/ML challenges use toy models, synthetic data, or local prompt/application setups.

Design lanes:

- Prompt injection against a toy retrieval app.
- Model extraction or membership-inference demos with synthetic data.
- Adversarial examples against small models.
- Data poisoning or backdoor detection in controlled artifacts.

Avoid real user data and external model dependencies when reproducibility matters.
