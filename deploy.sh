#!/bin/sh
# On the server: bring it to the latest main, migrate, restart.
set -e
cd "$(dirname "$0")"
git pull --ff-only
docker compose build
docker compose run --rm app python manage.py migrate
docker compose up -d
