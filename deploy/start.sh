#!/bin/sh
# Starts as root so the Railway volume (mounted at /data, owned by root) can be handed to the app user,
# then runs everything else as that user.
set -eu
mkdir -p /data
chown -R agent:agent /data
exec runuser -u agent -- ./deploy/run.sh
