#!/usr/bin/env bash
# Laadt .env veilig (alleen KEY=VALUE-regels; commentaar en rommel worden
# genegeerd) en start de server. Wordt gebruikt door run.sh én de preview.
cd "$(dirname "$0")"

if [ -f ".env" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}                       # verwijder eventuele Windows CR
    case "$line" in
      ''|[[:space:]]*\#*|\#*) continue ;;     # lege regels en commentaar
    esac
    case "$line" in
      [A-Za-z_]*=*) export "$line" ;;         # alleen geldige toewijzingen
    esac
  done < .env
fi

exec .venv/bin/python app.py
