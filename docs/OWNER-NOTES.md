# A small system record, not another manual

For each installed workspace, keep one **private** page with:

1. What this workspace is for, its entry URL and who owns it.
2. Source repository and installed version.
3. Backup destination, frequency, last successful restore check and independent recovery-kit location.
4. Application list, data locations, startup owner and stop/rollback instructions for the next Agent.
5. Known gaps and one next action, if any.

The owner should not need to memorize implementation details. After adding a service, the Agent checks registration, data/export coverage and health, then updates this page. Source details belong in Git; live status belongs in the panel; sensitive keys stay private and outside the server being protected.

Do not post a populated owner page or recovery attachment to this public repository. A dashboard link, a source link and a private recovery page are enough for everyday use; avoid creating many overlapping documentation databases before they solve a real problem.
