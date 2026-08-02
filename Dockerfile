# Markdown converter — productie-image voor een VPS.
FROM python:3.13-slim

# Geen .pyc-bestanden, ongebufferde logging.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies eerst (betere build-cache).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Applicatiecode.
COPY . .

# Versienummer (footer): het build-nummer + installatiedatum worden door de
# app zelf bijgehouden in .deploy-state/ (zie mdconv/version.py), niet hier.
# Zorg dat die map in docker-compose.yml als volume is gemount, anders reset de
# teller bij elke rebuild.

EXPOSE 5001

# Productieserver (gunicorn) met **threads**. Een AI-opschoning duurt minuten en
# zit die tijd te wachten op OpenRouter; met alleen processen bezet zo'n verzoek
# een hele worker en staat de tool stil voor al het andere. Met de gthread-worker
# kunnen meerdere documenten tegelijk worden opgehaald en opgeschoond.
# De ruime timeout is nodig omdat één opschoonverzoek zo lang open blijft staan.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", \
     "--worker-class", "gthread", "--workers", "2", "--threads", "8", \
     "--timeout", "600", "app:app"]
