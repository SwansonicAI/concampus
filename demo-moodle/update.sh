#!/bin/sh
# Weekly, from /etc/cron.d/moodle: Moodle's and Boost Union's fixes from their 4.5 branches, PHP's and Postgres's from
# their images, then Moodle's upgrade.
set -e
cd "$(dirname "$0")"
git -C /opt/moodle pull --ff-only --quiet
git -C /opt/moodle/theme/boost_union pull --ff-only --quiet
docker compose pull --quiet
docker compose up -d
docker compose exec -T --user www-data moodle php admin/cli/upgrade.php --non-interactive
