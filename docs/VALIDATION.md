# Release validation — 2026-11-16

This preview was tested in an isolated instance with its own config, data, credentials and ports. No production account or bot state was included.

- 51 Python tests pass: display editing/persistence, liveness, wallpaper bounds, terminal image uploads, private cache behavior, proxy behavior, authentication/origin checks, SQLite exports and backup failure boundaries.
- Desktop1440 and phone390: five default application cards, no horizontal overflow, no JavaScript page errors. Screenshots are from the isolated instance using the supplied default wallpaper.
- Browser terminal, FileBrowser, code-server and Magpie routes return200. FileBrowser, code-server and Magpie render real application screens without page errors; Magpie's authenticated providers API returns JSON200. No paid model inference was invoked.
- Anonymous gateway access requires authentication; unknown hostnames and invalid write origins are rejected. Independent private credentials were generated for this instance.
- An explicit local test restic repository produced an encrypted snapshot of148 files, approximately9.8MiB, including two SQLite online exports and a paused FileBrowser database copy. The exported recovery kit restored203 filesystem entries into a new empty directory, verified file data and passed SQLite validation. This is a **local repository test**, not proof of a new user's cloud authorization.
- The private owner deployment separately passed Google Drive restore checks before this source release. None of that repository's credentials, IDs, private documentation or data is distributed here.
- Gitleaks8.30.1 source scan reports no leaks; the initial Git history starts with this sanitized release tree, not the operational workspace's history.

Not tested: fresh-VPS OS installation, complete dependency build on a different distribution, ARM, iOS/Android installed-app behavior, new-user OAuth onboarding, long-term uptime. Runtime dependencies in this isolated test reused previously verified installations; the pinned Magpie CLI was downloaded and SHA-256 verified for the test.

The installer and recovery script are explicit owner operations. The package does not silently create cloud accounts, enable paid services, modify system boot files, activate old scheduled writers or expose backend ports.
