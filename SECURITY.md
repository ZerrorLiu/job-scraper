# Security Policy

## Sensitive local data

Credentials belong in `.env` or ignored local configuration. Cookies, mailbox content, SQLite
databases, logs, exports, Notion backups, and personal documents must never be committed.

If a credential is committed accidentally, revoke or rotate it immediately and remove it from the
entire Git history before sharing the repository.

## Reporting a vulnerability

Do not open a public issue containing credentials, personal data, or exploit details. Contact the
repository owner privately through GitHub instead.
