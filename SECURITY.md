# Security model

HaloDeck is a single-owner workspace. Its terminal and editor run as the installing Linux user and can access that user's files. It is not a sandbox for untrusted users or a multi-tenant hosting panel.

The gateway requires a generated Basic login; all apps listen only on loopback. Use SSH forwarding or an authenticated HTTPS reverse proxy. Never expose ttyd, code-server, Magpie or FileBrowser backend ports directly. Untrusted local processes under the same OS account are outside this protection boundary.

FileBrowser v2.63.23 is pinned; its upstream is archived. Its command execution and public sharing are disabled in this integration. Reassess or replace it before broad public deployment. Other applications retain their own upstream security and license obligations.

The private recovery kit can decrypt backups and access the configured storage. Keep it outside Git, outside public Notion pages and outside release assets. A leaked kit requires both credential revocation and a backup-key response; changing only the dashboard password is insufficient.

Do not submit secrets in public issues. Describe vulnerabilities without live credentials or private data, or use GitHub private vulnerability reporting if enabled on the repository.
