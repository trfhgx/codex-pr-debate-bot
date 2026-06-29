# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a Vulnerability

If you discover a security issue, please **do not** open a public GitHub issue.

Instead, report it privately by opening a GitHub Security Advisory on this
repository, or by emailing the maintainer with:

- A description of the vulnerability
- Steps to reproduce
- Impact assessment
- Suggested fix (if you have one)

We aim to acknowledge reports within 72 hours.

## Secrets and Local Configuration

Never commit `.env`, GitHub tokens, webhook secrets, or private keys. The
repository `.gitignore` excludes these files by default.

If you accidentally expose credentials:

1. Revoke the token or rotate the webhook secret immediately in GitHub.
2. Remove the secret from git history before publishing (use `git filter-repo`
   or GitHub secret scanning remediation guidance).
3. Generate fresh credentials in `.env`.

## Webhook Verification

The bot verifies GitHub webhook signatures when `GITHUB_WEBHOOK_SECRET` is set.
Leave this enabled in production. An empty secret disables verification and is
only appropriate for isolated local testing.