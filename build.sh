#!/usr/bin/env bash
# Bouwt en (her)start de Docker-container. Het versienummer/build-nummer in
# de footer wordt automatisch door de app bijgehouden (zie app.py en
# .deploy-state/, gemount als volume) — geen actie hier nodig.
set -e
cd "$(dirname "$0")"

docker compose build
docker compose up -d
docker compose logs --tail 20
