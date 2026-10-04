# Security Policy

## Scope

This repository is a **public interview / reference implementation**. It exists to
demonstrate engineering design and to be reviewed and read by others.

It is not a production service operated on behalf of users, and it is not offered
as a hardened, audited, or certified product. Everything written below describes
what is present in this repository's code and tests — nothing more.

## Supported Branches

Security fixes are accepted against **`main` only**. It is the single supported
branch. Other branches are not maintained and receive no backports.

## Reporting a Vulnerability

**Do not open a public issue containing any of the following:**

- credentials of any kind (tokens, keys, passwords, certificates)
- real production data
- private corpus content
- an exploitable secret

Including those in a public issue publishes them. Even if you later redact the
issue, the content has already been exposed.

**Preferred route: GitHub private vulnerability reporting.** If private
vulnerability reporting / Security Advisories are enabled on this repository, use
GitHub's *Report a vulnerability* control (the *Security* tab →
*Report a vulnerability*), which opens a private advisory visible only to the
maintainer. This is the correct channel for anything that could help an attacker.

If private reporting is not enabled, open a normal issue that contains **only** a
redacted description of the issue and no sensitive material, and ask for a private
channel.

There is no guaranteed response time and no paid support SLA. Reports are handled
best-effort by the maintainer.

## Current Implemented Security Controls

These are the security controls that exist in this repository today and are
exercised by code or tests in the tree. They are described as implemented
mechanisms, not as guarantees.

| Control | Where |
|---|---|
| RS256 authentication (JWT signing/verification, fail-closed claim validation) | `auth/jwt_auth.py`, `common/auth.py` |
| Document-level RBAC (bitmask role/dept authorization) | `auth/bitmask_rbac.py`, `common/auth.py` |
| Role/dept cache partitioning (physical cache key separation) | `cache/redis_cache.py` |
| Trusted proxy handling (`X-Forwarded-For` client-IP resolution) | `api/routes_auth.py`, `app.py` |
| Login rate limiting | `api-gateway/middleware/rate_limiter.py`, `api/routes_auth.py` |
| Authenticated metrics endpoint | `api/routes.py` (`/api/metrics`) |
| Structured audit logging (hashed queries, security-relevant fields) | `auth/audit_log.py`, `common/audit.py` |
| Security CI (dependency vulnerability scan + secret scan) | `.github/workflows/security.yml` |
| Retrieval prompt trust boundary (untrusted evidence confined to reserved delimiters) | `models/llm_client.py` |

Per-control evidence, the threat each control addresses, and the remaining gap for
each one are documented in
[docs/security-regression-coverage.md](docs/security-regression-coverage.md).

## Explicit Limitations

These are stated plainly so nothing here is read as a stronger claim than it is.

- **No security certification.** This project has not undergone SOC 2, ISO 27001,
  or any other formal security certification or audit. No such compliance is
  claimed or implied.
- **No production penetration testing.** No third-party or professional
  penetration test has been performed against this system. Do not treat it as
  penetration-tested.
- **The retrieval prompt trust boundary is not proof against prompt injection.**
  It confines untrusted evidence between reserved delimiters and states a
  data-not-instructions policy in the system message. That is a message-structure
  property only. It does not demonstrate, and cannot demonstrate, that a model
  will refuse instructions found inside retrieved content. There is also no
  ingestion-side content inspection, sanitization, or quarantine.
- **No ABAC.** Authorization is role/dept bitmask RBAC. Attribute-based policies
  are not implemented.
- **No enterprise SSO.** There is no SAML, OIDC, or enterprise identity-provider
  integration. Local JWT issuance is the only authentication path.
- **No KMS.** Keys are not held in a managed key-management service.
- **No comprehensive PII masking.** There is no systematic, comprehensive
  personally-identifiable-information detection or redaction across the system.

Each item above is a statement about this repository as it stands, not a roadmap
commitment.

## Further Reading

- [docs/security-regression-coverage.md](docs/security-regression-coverage.md) —
  what is deterministically covered, what is not, and each control's boundary
- [docs/repository-truth-audit.md](docs/repository-truth-audit.md) — claim-level
  verification status and known unverified gaps across the repository