# Changelog

## [Unreleased]

### Added

- **Grist provisioner.** Gives PrivacyFence each person's Grist API key on a self-hosted grist-core, behind a scoped provisioning key.
- **Provisioner service, HTTP API and CLI.** `grist-provisioner serve` answers PrivacyFence's provisioning routes and clears managed keys 90 days after they were last issued; `create-key`, `list-keys`, `revoke-key` and `check` manage provisioning keys and the set-up.
