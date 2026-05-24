# Security Review — 2026-05-25

Scope: full review of `admin/app/`, `admin/Dockerfile`, `admin/entrypoint.sh`,
`pam/ocserv`, `scripts/entrypoint.sh`, `ocserv-ldap/nslcd.conf.tmpl`,
top-level `Dockerfile`, `docker-compose.yml`, `ldap/bootstrap/*.ldif`,
`tests/e2e/*.sh`, plus a `pip-audit` of `admin/requirements.txt`.

Reviewer: automated review under owner's autonomous-fix policy.

## Summary

| Severity | Count | Auto-fixed | Flagged |
|---|---|---|---|
| HIGH     | 0  | 0 | 0 |
| MEDIUM   | 5  | 2 | 3 |
| LOW      | 6  | 0 | 6 |

No high-severity exploitable issues found. Auth path (password + TOTP) is
sound: `__Host-` cookies, `Secure`/`HttpOnly`/`SameSite=Strict`, CSRF on
every state-changing route via double-submit, replay protection on the admin
TOTP via `last_used_totp_step`, bcrypt password storage, rate limiting on
both password and TOTP steps. SQLite calls are uniformly parametrised. API
tokens are hashed (bcrypt) and only shown once. The TOTP secret returned by
`/api/v1/users/<u>/enroll` is never logged or cached — `audit.sanitize_details`
also redacts a `secret` key by convention.

## Auto-fixed

| # | Category | File | Commit |
|---|---|---|---|
| 1 | dep vuln (jinja2 sandbox escape, 3 CVEs) | `admin/requirements.txt` | `1170940` |
| 2 | dep vuln (python-multipart DoS, CVE-2024-53981) | `admin/requirements.txt` | `1170940` |

After bump: `docker compose run --rm admin pytest -q` = 100 passed.

## Flagged for owner decision

### MEDIUM

**M1. LDAP injection in `admin/app/ldap_client.py:55` — defense in depth.**
`_fetch_one` interpolates `uid` into the filter `(&(objectClass=posixAccount)(uid={uid}))`
unescaped. Today `uid` only comes from `_member_uids`, which reads
`memberUid` from `cn=vpn-users` via the read-only bind — so attacker
control requires already-writeable LDAP. Still, future callers may pass
external input. Fix: use ldap3's `escape_filter_chars` or `ldap3.utils.conv`
when building the filter. Mechanical fix but a behaviour change worth
your eyeball — flagging.

**M2. `ocserv` container runs as root with `cap_add: NET_ADMIN` and a
shared `/home` volume with the admin container** (`docker-compose.yml:36,
50, 75`). If the admin container is compromised (root in container, as
it currently is), the attacker writes arbitrary files into the shared
`ocserv-home` volume which `ocserv` then reads as root. There is no path
traversal in `enroll_user` (good — `safe_home_path` resolves and checks
containment), but the trust boundary is still shared. Mitigation
options: (a) run admin container as non-root user (UID matched to LDAP
uidNumber range), (b) make `/home` mount read-only for admin and pipe
enroll writes through a small setuid helper in ocserv, (c) split
`.google_authenticator` storage out of `/home`. Architectural — flagging.

**M3. Cookie `__Host-csrf` is `httponly=False` by design (double-submit
needs JS read), but combined with `'unsafe-inline'` in the CSP
`style-src`, any future stored-XSS in an admin-rendered template could
both read the CSRF token and submit forms.** No XSS sink found today —
Jinja2 autoescape is on by default, and the templates I traced
(`audit.html`, `_users_table.html`, `qr_once.html`, `tokens.html`) pass
content through `{{ … }}`. But `'unsafe-inline'` in `style-src`
materially weakens CSP and should be replaced with hashed/nonce inline
styles or moved to `static/`. `security_headers.py:6`.

### LOW

**L1. `admin/Dockerfile` runs as root.** No `USER` directive. Combined
with M2, a code-exec bug in the admin app yields container root. Mitigate
by adding a non-root `USER` after `pip install`.

**L2. `scripts/entrypoint.sh:135` runs `ocserv --debug=3` always.** Lab
default; in any non-lab use this leaks PAM debug info to `docker logs`.
Acceptable for the repo's stated purpose; flagging for awareness.

**L3. `pam/ocserv:6` `pam_ldap.so` is `required` — failure delays response
without abort; that's intentional for fail-closed behaviour.** OK.
However the comment in line 2 (`onerr=succeed`) means *if the denylist
file is missing* the user is permitted; the `scripts/entrypoint.sh`
touches it so it always exists, but worth a `fail` semantics review if
you ever change the entrypoint.

**L4. Self-signed RSA-2048 cert (admin entrypoint, line 12) — fine for
a lab.** Consider bumping to 3072 or switching to ed25519/ecdsa-p256 for
modernity.

**L5. `python-multipart` left at 0.0.18.** Three further DoS CVEs
(2026-24486 path-traversal in non-default config, 2026-40347, 2026-42561)
have fix versions 0.0.22/0.0.26/0.0.27. They don't apply (we don't use
`UPLOAD_DIR`/`UPLOAD_KEEP_FILENAME`, and request-body size is bounded by
uvicorn defaults), but worth tracking when fastapi >0.115 ships compat.

**L6. `pytest 8.3.3` has CVE-2025-71176 (predictable `/tmp/pytest-of-{user}`
DoS / local privesc).** Dev dependency only — never on the production
container image at runtime since tests don't ship in the running container.
Bump on next routine update; not security-relevant for the deployed admin.

## Dependency vulnerabilities (full)

| Package | Current | CVE / GHSA | Severity | Fix available | Bumped? |
|---|---|---|---|---|---|
| jinja2 | 3.1.4 | CVE-2024-56326, CVE-2024-56201, CVE-2025-27516 | MEDIUM | 3.1.6 | yes (auto) |
| python-multipart | 0.0.12 | CVE-2024-53981 | MEDIUM | 0.0.18 | yes (auto) |
| python-multipart | 0.0.18 | CVE-2026-24486, CVE-2026-40347, CVE-2026-42561 | LOW (config not used) | 0.0.27 | no — see L5 |
| starlette | 0.41.3 (transitive via fastapi 0.115.4) | PYSEC-2026-161 (host header path-injection), CVE-2025-54121 (UploadFile rollover blocks loop), CVE-2025-62727 (FileResponse Range ReDoS) | MEDIUM | 0.47.2 / 0.49.1 / 1.0.1 | no — fastapi pin blocks |
| pytest | 8.3.3 | CVE-2025-71176 | LOW (dev-only) | 9.0.3 | no — see L6 |

Starlette is the highest-risk remaining transitive: the Range-header ReDoS
on `FileResponse` is theoretically reachable via `/static/...` mounted on
the admin app. Bumping starlette directly requires lifting fastapi to a
release that pins a fixed starlette range. Recommend a follow-up:
`fastapi>=0.115.6` ships with `starlette>=0.41.3,<0.42` — still vulnerable.
The cleanest path is bumping to `fastapi==0.118.x` + `starlette==0.49.x`,
which is a separately-tested upgrade outside this review's auto-fix scope.

## Infra / compose review

- `docker-compose.yml`: no plaintext secrets — all sourced from `.env`.
  `.env` is in `.gitignore`. Good.
- `ldap/bootstrap/*.ldif`: only `{SSHA}` hashes — fine to commit.
- `pam/ocserv`, `ocserv-ldap/nslcd.conf.tmpl`: only template variables;
  secrets injected at container start via envsubst from `.env`. Good.
- Top-level `Dockerfile` uses `--no-install-recommends` consistently.
  `apt-get` lists are cleaned (`rm -rf /var/lib/apt/lists/*`). Good.
- Base images:
  - `python:3.12-slim` — current major, slim; patch level pulled at build
    time. OK.
  - `debian:bookworm-slim` — current Debian stable. OK.
  - `osixia/openldap:1.5.0` — last release Sep 2022 (~3.7 yrs old). Image
    is unmaintained upstream. Consider migrating to `bitnami/openldap` or
    `lldap` for a maintained alternative. **Flagged.**

## Verified-clean (negatives worth recording)

- All `sqlite3` calls in `admin/app/` use `?`-parametrised execute. No
  string concatenation into SQL anywhere.
- `safe_home_path` (`admin/app/usernames.py:15`) resolves + checks
  `is_relative_to(base)` — defeats `../`/symlink path traversal in
  `ensure_home`/`enroll_user`/`revoke_user`.
- `users._write_denylist_atomic` uses `fcntl.flock(LOCK_EX)` on a real
  file handle held across read-modify-write. No TOCTOU.
- Admin TOTP replay window: `verify_admin_totp` records
  `last_used_totp_step` and rejects `<= last_used`. Good.
- `audit.sanitize_details` redacts keys in `SENSITIVE_KEYS`
  (`password`, `secret`, `token`, `plaintext_token`, `scratch_code`, …)
  before persisting to `audit_log.details`. The TOTP `secret` returned
  by `/api/v1/users/<u>/enroll` is **never written to `details`** — the
  enroll route only writes `action=enroll.ok` with no details payload.
  Confirmed safe.
- CSRF: `verify_csrf` uses `hmac.compare_digest` — constant-time.
- bcrypt: `_pwd.dummy_verify()` on user-not-found path mitigates
  username-enumeration timing oracle.
- Cookies: `__Host-` prefix + `Secure` + `HttpOnly` + `SameSite=strict`
  on `admin_session` and `admin_pending`. `__Host-csrf` necessarily
  `HttpOnly=false` (JS reads it) — by design.
- LDAP cache fallback (`ldap_client.stale_users`) only used for UX
  display when LDAP is down; never used as an authorisation source.
- Bearer token format check (`vpa_` prefix, 36 chars) plus bcrypt
  hash compare — no timing oracle on token id since prefix lookup
  returns *all* rows for that prefix and verifies each (rare collision
  case handled).

## Recommended follow-ups (owner decision)

1. Bump fastapi → ~0.118 to pull a fixed starlette (`FileResponse`
   ReDoS, host header path injection). Needs separate compat test.
2. Run admin container as non-root + tighten the `/home` trust boundary
   (M2 + L1).
3. Replace `osixia/openldap:1.5.0` with a maintained image.
4. Escape user input into LDAP filters with `escape_filter_chars` (M1).
5. Drop `'unsafe-inline'` from `style-src` CSP (M3).
