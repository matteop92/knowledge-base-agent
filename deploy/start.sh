#!/bin/sh
# Starts as root so the Railway volume (mounted at /data, owned by root) can be handed to the app user,
# then runs everything else as that user.
set -eu
mkdir -p /data/home
chown -R agent:agent /data
chmod 700 /data/home
# setpriv replaces this process (runuser would stay as PID 1 and may cut shutdown short).
exec setpriv --reuid=agent --regid=agent --init-groups ./deploy/run.sh
