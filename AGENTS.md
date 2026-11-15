# HaloDeck maintenance contract

Read README.md and docs/ before changes. Keep source separate from mutable runtime data. Never copy private runtime state, OAuth grants, recovery kits, user images, conversations or databases into this repository or CI artifacts.

Every new app must document and verify: upstream version/license, startup owner, loopback port, persistent data paths, private credential location (never values), backup/export method, health probe, stop/rollback procedure. Register with Revive before starting persistent services; do not create competing supervisors.

A new directory inside backup scope is captured automatically; a new service is not automatically recoverable. Verify its data scope and database consistency separately. Missing expected data must fail closed; never initialize an empty database or repository as a repair. Restore only to empty directories. Never auto-enable duplicate writers on old and new machines.

Use fixed official downloads and hashes. Do not enable paid services, publish credentials, delete backups, change public ingress or enable new provider accounts without explicit scope. Keep owner-facing documentation short: purpose, entry, current status, recovery location, next action. Put implementation detail in source/docs.

Verify desktop and phone layout, same-origin/auth boundaries and relevant restore behavior. Distinguish local fixture tests, real remote restore tests and actual new-machine deployment. Preserve existing owner edits. No unsolicited visual redesign or application sprawl.
