# grist-provisioner

A small service that runs next to a self-hosted, free grist-core and gives [PrivacyFence](https://github.com/privacyfence) each person's Grist API key, behind a scoped provisioning key. grist-core has no API that creates a key for another user, so the provisioner reads and writes `users.api_key` in Grist's home database (SQLite) directly.

It only answers requests. It never calls PrivacyFence. It creates Grist accounts only through Grist's own API, when the team site is configured (see below); it never writes `users` or `logins` rows itself.

- **A person with no key** gets a new *managed* key. It expires 90 days after it was last issued, and a release deletes it, but only while it is still that person's current key.
- **A person who already has a key** has it *adopted*: handed over as it is, and never deleted by the provisioner.
- **The install admin** is never provisioned.
- **People without a Grist account** get 404 until Grist creates the account itself, unless the team site is configured: then their first provisioning creates the account through Grist's API and adds them to the team site.

## Requirements

- grist-core 1.7 with the default SQLite home database (`home.sqlite3`). Other versions are refused at start-up if the `users`/`logins` columns differ.
- The provisioner runs on the **same host** as Grist and mounts Grist's `/persist` volume read-write. Never put it on a network file system: SQLite locking across containers needs a local volume.
- Grist must be in service for users' API keys to work: set `GRIST_IN_SERVICE=true`, or finish Grist's first-run setup at `/boot`. Otherwise API calls with a valid key get 503 "Grist is not yet configured".
- With the team site configured, Grist must set `GRIST_BOOT_KEY` (the same value as the boot key file) and `GRIST_SINGLE_ORG` (the team site's domain), and the boot user (`GRIST_ADMIN_EMAIL`, else `GRIST_DEFAULT_EMAIL`) must own that site. Grist creates the site for that user at start.
- The provisioner's port must be reachable only from the PrivacyFence server, behind your TLS reverse proxy. PrivacyFence requires https.

## Deployment

Add the provisioner to Grist's compose file:

```yaml
services:
  grist:
    image: gristlabs/grist:1.7.21
    environment:
      GRIST_ADMIN_EMAIL: admin@example.com
      GRIST_IN_SERVICE: "true"
      # GRIST_BOOT_KEY: ...          # only with the team site; the same value as the provisioner's boot key file
      # GRIST_SINGLE_ORG: team       # only with the team site
    volumes:
      - persist:/persist

  grist-provisioner:
    image: ghcr.io/privacyfence/grist-provisioner:latest
    user: "1001:1001"            # the uid:gid of Grist's `grist` user, see below
    environment:
      GRIST_ADMIN_EMAIL: admin@example.com   # copy Grist's value
      # GRIST_DEFAULT_EMAIL: ...               # copy Grist's value if you set it
      # PROVISIONER_ALLOWED_DOMAINS: example.com
      # PROVISIONER_KEY_FILE: /run/secrets/provisioning_key
      # PROVISIONER_GRIST_URL: http://grist:8484
      # PROVISIONER_GRIST_TEAM_SITE: team
      # PROVISIONER_GRIST_BOOT_KEY_FILE: /run/secrets/grist_boot_key
    volumes:
      - persist:/persist
      - provisioner-data:/data
    ports:
      - "127.0.0.1:8485:8485"    # publish only through your TLS reverse proxy

volumes:
  persist:
  provisioner-data:
```

- **uid and gid.** Grist's entrypoint makes `/persist` owned by its `grist` user. Run the provisioner as the same user, with the values from `docker compose exec grist id grist`, or it cannot write `home.sqlite3` or its journal.
- **Admin addresses.** `GRIST_ADMIN_EMAIL` and `GRIST_DEFAULT_EMAIL` must match Grist's. Both addresses (and `GRIST_SUPPORT_EMAIL`, default `support@getgrist.com`, plus Grist's `anon@`, `everyone@` and `thumbnail@getgrist.com` special users) are never provisioned. At least one of the two must be set.
- **TLS.** Put the port behind your reverse proxy with a certificate; PrivacyFence only talks https.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GRIST_HOME_DB` | `/persist/home.sqlite3` | Grist's home database |
| `PROVISIONER_STATE_DB` | `/data/provisioner.sqlite3` | The provisioner's own database |
| `PROVISIONER_HOST` / `PROVISIONER_PORT` | `0.0.0.0` / `8485` | Listen address |
| `GRIST_ADMIN_EMAIL`, `GRIST_DEFAULT_EMAIL` | empty | Same values as Grist's; both are refused |
| `GRIST_SUPPORT_EMAIL` | `support@getgrist.com` | Refused, like the three special users |
| `PROVISIONER_ALLOWED_DOMAINS` | empty (any) | Comma-separated email domains, compared lower-case. Required when `PROVISIONER_GRIST_URL` is set |
| `PROVISIONER_KEY_FILE` | empty | A file holding a provisioning key, registered at every `serve` start |
| `PROVISIONER_KEY_NAME` | `key-file` | The name that key is stored under (1 to 100 characters). Reserved for the file's key |
| `PROVISIONER_GRIST_URL` | empty | Grist's address for API calls, such as `http://grist:8484` (http or https, no path) |
| `PROVISIONER_GRIST_TEAM_SITE` | empty | The team site's domain (Grist's `GRIST_SINGLE_ORG`), `[a-z0-9-]{1,100}` |
| `PROVISIONER_GRIST_BOOT_KEY_FILE` | empty | A file holding Grist's `GRIST_BOOT_KEY`. Set it, the URL and the site together, or none |
| `PROVISIONER_GRIST_TEAM_ROLE` | `editors` | The role a new team member gets: `editors`, `viewers` or `members` |

## Provisioning keys

PrivacyFence authenticates with a provisioning key (`Authorization: Bearer <key>`). Create one on the host:

```
docker compose exec grist-provisioner grist-provisioner create-key --name privacyfence
```

The key is printed once, on its own line. Put it in PrivacyFence's environment as `PRIVACYFENCE_GRIST_PROVISIONING_KEY`; see PrivacyFence's `docs/grist-setup.md` for the rest of the PrivacyFence side. Only the key's SHA-256 is stored.

**From a file.** Instead of `create-key`, set `PROVISIONER_KEY_FILE` to a file holding a key that starts with `gristprov_` and is at least 42 characters. Every `serve` start registers it (only its SHA-256 is stored), so one generated key can be given to both PrivacyFence and the provisioner. To rotate, replace the file's content and restart: the key previously registered from the file is revoked. The name (`PROVISIONER_KEY_NAME`, default `key-file`) is reserved for the file's key, so keys made with `create-key` should use another name. To revoke the file's key, replace the file; revoking it with `revoke-key` while it is still in the file makes the next start fail.

Other commands (run the same way):

| Command | What it does |
|---|---|
| `serve` | Starts the service and the hourly expiry pass (the image default) |
| `create-key --name NAME` | Creates a provisioning key and prints it once |
| `list-keys` | Lists id, name, creation time and revocation time |
| `revoke-key ID` | Revokes a provisioning key |
| `check` | Checks the Grist schema and prints `ok: N Grist users, M linked` |

## Managed and adopted keys

- A person whose Grist account has no API key gets a new random 40-hex-character key (the same shape Grist makes). It is *managed*: it expires 90 days after it was last issued (each issue to PrivacyFence renews it), and releasing the person deletes it, but only while it is still their current key. If they replaced it themselves, it is left alone.
- A person who already has a key has it *adopted*: it is handed over as it is, never expires and is never deleted by the provisioner, not even on release. Grist has one key per person, and replacing it would break their scripts.
- Expiry runs inside `serve` once an hour. Grist checks the database on every API request, so a written key works at once and a cleared one fails at once.
- Only SHA-256 hashes are kept in `/data`; no email and no Grist key is stored.
- Each link, issue, adopt, release and expiry logs one JSON line to stdout (never an email or a key).

## Backup

Back up `/data` together with `/persist`. If `/data` is lost, the provisioner no longer knows which keys it made: existing keys are then treated as adopted and never expire, and provisioning keys must be created again.

## Messages and fixes

### Start-up and CLI

| Message | Fix |
|---|---|
| `PROVISIONER_PORT must be a number from 1 to 65535` (exit 2) | Fix `PROVISIONER_PORT`. |
| `Set GRIST_ADMIN_EMAIL or GRIST_DEFAULT_EMAIL to the same value as Grist's, so the install admin is never provisioned.` (exit 2) | Set at least one of them, copied from Grist's own configuration. |
| `Grist's home database does not have the users and logins columns this provisioner expects (it supports grist-core 1.7). Check GRIST_HOME_DB, or update the provisioner.` (exit 2) | Point `GRIST_HOME_DB` at Grist's `home.sqlite3` (the `/persist` volume), or use a provisioner release that supports your Grist version. |
| `Store this key in PrivacyFence's environment; it is not shown again.` (stderr of `create-key`) | Copy the printed key into `PRIVACYFENCE_GRIST_PROVISIONING_KEY`; if lost, create another and revoke the old one. |
| `PROVISIONER_KEY_NAME must be 1 to 100 characters` (exit 2) | Fix `PROVISIONER_KEY_NAME`. |
| `Set PROVISIONER_GRIST_URL, PROVISIONER_GRIST_TEAM_SITE and PROVISIONER_GRIST_BOOT_KEY_FILE together, or none of them.` (exit 2) | Set all three, or remove them. |
| `PROVISIONER_GRIST_URL must be an http:// or https:// address with no user name, path, query or fragment` (exit 2) | Use an address such as `http://grist:8484`. |
| `PROVISIONER_GRIST_TEAM_SITE must match [a-z0-9-]{1,100}` (exit 2) | Use the domain of Grist's `GRIST_SINGLE_ORG`. |
| `PROVISIONER_GRIST_BOOT_KEY_FILE cannot be read: ...` or `... is empty` (exit 2) | Mount a readable file holding Grist's `GRIST_BOOT_KEY`. |
| `PROVISIONER_GRIST_TEAM_ROLE must be editors, viewers or members` (exit 2) | Fix the role. |
| `Set PROVISIONER_ALLOWED_DOMAINS when PROVISIONER_GRIST_URL is set: the provisioner then creates Grist accounts.` (exit 2) | List the email domains that may get accounts. |
| `PROVISIONER_KEY_FILE cannot be read: ...` (exit 2) | Mount a readable key file. |
| `PROVISIONER_KEY_FILE must hold a key that starts with gristprov_ and is at least 42 characters` (exit 2) | Put a key from `create-key`, or one made the same way, in the file. |
| `PROVISIONER_KEY_FILE holds a revoked key; put a new key in the file` (exit 2) | Replace the file's content with a new key. |
| `Grist answered N` or `Grist is not reachable` (`check`, exit 2) | `check` could not read the team site. Check the URL, `GRIST_BOOT_KEY`, and that the boot user owns the site. |
| `no active key with id N` (exit 2) | Check the id with `list-keys`; it may already be revoked. |
| `expiry pass failed (will retry): ...` (stderr) | Usually a locked or unreadable database; check the `/persist` volume and the `user:` setting. The pass retries in an hour. |
| `sqlite3.OperationalError: unable to open database file` or `attempt to write a readonly database` | The provisioner's user cannot write `/persist` or `/data`. Set `user:` to Grist's uid:gid and make sure the volumes are writable. |

### HTTP API errors

Every `/api/provisioning/*` route returns `{"error": "..."}`.

| Status and message | Meaning and fix |
|---|---|
| 401 `unauthorized` | Missing, wrong or revoked provisioning key. Create a new one and update `PRIVACYFENCE_GRIST_PROVISIONING_KEY`. |
| 400 `bad request` | The body is not a JSON object. |
| 400 `bad external id` | The id must match `[A-Za-z0-9._@-]{1,200}`. |
| 400 `bad email` | The email must be 3-320 characters with exactly one `@`. |
| 403 `account email domain is not allowed` | The domain is not in `PROVISIONER_ALLOWED_DOMAINS`. Add it, or use another account. |
| 404 `no Grist account for this email` | The person has not signed in to Grist yet (or the account is a service user). Let them sign in once, then retry, or configure the team site so the provisioner creates the account. |
| 404 `not found` | The external id is not linked (or its Grist account is gone). Link it with the PUT call first. |
| 409 `account is a Grist admin; connect it by hand` | The install admin is never provisioned. Create the admin's key in Grist and connect it by hand. |
| 409 `account is linked to another external id or email` | The Grist account or the external id is already linked to something else. Release the old link first. |
| 403 `account disabled` | The Grist account is disabled; re-enable it in Grist. |
| 502 `could not add the account to the Grist team site` | Grist's API refused or did not answer. Run `check`, and see that `GRIST_BOOT_KEY` matches and the boot user owns the site. Later requests for people already linked are not affected. |
| 503 `Grist is not yet configured` (from Grist, not from the provisioner) | Grist is not in service: set `GRIST_IN_SERVICE=true` or finish the first-run setup. |

Routes: `GET /api/provisioning/self`, `PUT /api/provisioning/accounts/{external_id}` (body `{"email": ...}`), `POST /api/provisioning/accounts/{external_id}/key` (201), `DELETE /api/provisioning/accounts/{external_id}`, and `GET /healthz` (no authentication). An unknown route answers a plain-text 404.

## Design decisions

- **Direct SQLite access.** grist-core has no API that creates a key for another user, so the provisioner writes `users.api_key` itself, only with `UPDATE ... WHERE api_key IS NULL` (set) and `WHERE api_key = ?` (clear), and no other column or table.
- **One lock.** Upsert, issue, release and expiry run one at a time, so an expiry can never clear a key that was just renewed. A person's first upsert also holds the lock across two Grist API calls (5 s timeout each), so the worst case is about 10 s.
- **Rejected: TypeScript or a grist-core fork.** grist-core has no server plug-in hook for routes, and a fork must follow every Grist release.
- **Rejected: writing through Grist's own API with forward auth.** It replaces Grist's Google sign-in.
- **Grist accounts are created through Grist's API with the boot key.** With the team configured, a person's first upsert asks Grist (`PATCH /api/orgs/{site}/access`, `x-boot-key`) to add them to the team site, which creates the Grist user if needed. The provisioner still never writes `users` or `logins` rows. An existing member's role is never changed, and linked people never trigger a call, so a Grist outage blocks only first-time provisioning. The boot key goes only to the configured address, through no proxy and no redirect.
  - **Rejected: inserting rows into `users` and `logins`.** `users.ref` is generated by Grist's ORM, and personal orgs and groups would be skipped.
  - **Rejected: SCIM.** The maintainer declined it; it also needs `GRIST_ENABLE_SCIM` and an admin API key, and cannot add team-site membership.
  - **Rejected: the admin's own API key instead of the boot key.** That key would have to be created and stored; the boot key is already a server setting the operator controls.
- **A workspace or document guest becomes a team member.** If a person only has access to a workspace or document, the first provisioning adds them to the team site with the configured role.
- **Rejected: rotating or replacing a key a person already has.** Grist has one key per person, and replacing it would break their scripts; it is adopted instead.
- **Rejected: keeping Grist keys or emails in `/data`.** Only SHA-256 hashes are kept.

## Development

```
pip install -e ".[test,dev]"
python3 -m pytest tests -q -m "not real_grist"
```

`tests/real_grist` runs against a real grist-core container (`.github/workflows/real-grist.yml`).

### Releases

1. In a pull request, rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD` and add a new empty `## [Unreleased]` above it.
2. Merge it.
3. Tag the merge commit: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.

The tag runs `.github/workflows/release.yml`: it runs the tests, builds `linux/amd64` and `linux/arm64`, publishes `ghcr.io/privacyfence/grist-provisioner:X.Y.Z` (and `X.Y` and `latest` for a non-prerelease) and creates the GitHub Release from the changelog section. A tag with a suffix such as `v0.2.0-rc.1` is a prerelease and gets only its full-version tag. The tag must be on `main`.

`Actions → release → Run workflow` is a dry run: it builds the image and publishes nothing.

A new package is private until its visibility is set to Public in the package settings.

Licensed under Apache-2.0.
