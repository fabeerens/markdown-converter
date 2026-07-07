#!/usr/bin/env bash
# Bouwt en (her)start de Docker-container, met het versienummer automatisch
# gekoppeld aan de huidige git-commit. Gebruik dit i.p.v. losse `docker compose`
# commando's, zodat de footer altijd de juiste commit-hash toont.
set -e
cd "$(dirname "$0")"

export GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export GIT_COMMIT_COUNT="$(git rev-list --count HEAD 2>/dev/null || echo 0)"

echo "Bouwen met versie-info: commit ${GIT_COMMIT} (build ${GIT_COMMIT_COUNT})"
docker compose build
docker compose up -d
docker compose logs --tail 20
