"""Gives PrivacyFence each person's Grist API key on a self-hosted grist-core."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("grist-provisioner")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"
