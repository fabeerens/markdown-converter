#!/usr/bin/env bash
# Start de EUR-Lex → Markdown web-interface.
# - Maakt de eerste keer een virtuele omgeving aan.
# - Installeert de dependencies opnieuw zodra requirements.txt is gewijzigd.
# - Opent de browser en start de server.
set -e
cd "$(dirname "$0")"

PORT="${PORT:-5001}"
STAMP=".venv/.deps-installed"

# Python 3 is vereist (bv. via https://www.python.org/downloads/ of 'xcode-select --install').
if ! command -v python3 >/dev/null 2>&1; then
  echo "FOUT: Python 3 is niet gevonden op deze Mac."
  echo "Installeer Python 3 (https://www.python.org/downloads/) en probeer opnieuw."
  read -r -p "Druk op Enter om te sluiten… " _
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Eenmalige installatie: virtuele omgeving aanmaken… (dit kan even duren)"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
fi

# (Her)installeer dependencies als de stamp ontbreekt of ouder is dan requirements.txt.
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Dependencies installeren / bijwerken…"
  ./.venv/bin/pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

echo ""
echo "  EUR-Lex → Markdown draait op:  http://127.0.0.1:${PORT}"
echo "  Stoppen met Ctrl+C (of sluit dit venster)"
echo ""

# Open de browser zodra de server er is.
( sleep 2; open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true ) &

# serve.sh laadt .env veilig en start de server.
exec bash serve.sh
