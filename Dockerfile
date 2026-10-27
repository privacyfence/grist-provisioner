FROM python:3.12-slim

ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev0

# No fixed uid: deployments override it with compose `user:` to match Grist's `grist` user.
RUN useradd --create-home provisioner

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION}" pip install --no-cache-dir .

USER provisioner
VOLUME /data
EXPOSE 8485
ENTRYPOINT ["grist-provisioner"]
CMD ["serve"]
