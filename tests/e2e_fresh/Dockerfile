# Tier 3 — fresh-install dry-run harness.
#
# Starts from an empty Ubuntu image, installs the bare minimum to exercise
# the deterministic parts of install.txt (python, git, jq, curl, openssl),
# and runs the install harness as an unprivileged user.
#
# What this image canNOT do: drive a real Claude Code session. The slash
# commands /softwaresoftware:install and /mindframe:setup require an
# authenticated claude binary, which we don't bring into the container.
# Tier 3 tests the underlying mechanisms — dispatcher + dashboard + frame
# pipeline — against a known-clean filesystem. The Claude-driven phases
# are documented as manual checks in README.md.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python-is-python3 \
        git curl jq openssl ca-certificates \
        && rm -rf /var/lib/apt/lists/*

# Unprivileged user that mirrors a real install — no sudo, no system
# packages added at runtime.
RUN useradd -m -s /bin/bash mf
USER mf
WORKDIR /home/mf

# The harness clones the repos itself so the image is reusable across
# branches/commits — pass MINDFRAME_REF / DISPATCHER_REF to docker run
# to test a specific commit. Defaults to main.
ENV MINDFRAME_REPO=https://github.com/softwaresoftware-dev/mindframe.git
ENV DISPATCHER_REPO=https://github.com/softwaresoftware-dev/dispatcher.git
ENV MINDFRAME_REF=main
ENV DISPATCHER_REF=main

COPY --chown=mf:mf harness.py /home/mf/harness.py

CMD ["python3", "/home/mf/harness.py"]
