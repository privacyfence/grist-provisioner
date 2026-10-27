# Changelog

## [Unreleased]

## [0.1.0] - 2026-10-27

### Added

- **Grist provisioner.** Gives PrivacyFence each person's Grist API key on a self-hosted grist-core, behind a scoped provisioning key.
- **Provisioner service, HTTP API and CLI.** `grist-provisioner serve` answers PrivacyFence's provisioning routes and clears managed keys 90 days after they were last issued; `create-key`, `list-keys`, `revoke-key` and `check` manage provisioning keys and the set-up.
- **README and release workflow.** Documents the set-up, key handling, messages and design decisions; pushing a `vX.Y.Z` tag runs the tests, publishes a `linux/amd64` and `linux/arm64` image to `ghcr.io/privacyfence/grist-provisioner` as `X.Y.Z`, `X.Y` and `latest` (a prerelease only as its own version) and creates the GitHub Release from the changelog; `Run workflow` is a dry run that publishes nothing.
