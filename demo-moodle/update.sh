#!/bin/sh
# Weekly, from /etc/cron.d/moodle: Moodle's fixes from its stable branch, PHP's and Postgres's from their images, then
# Moodle's upgrade.
set -e
cd "$(dirname "$0")"
git -C /opt/moodle pull --ff-only --quiet
docker compose pull --quiet
docker compose up -d
docker compose exec -T --user www-data moodle php admin/cli/upgrade.php --non-interactive
