# syntax=docker/dockerfile:1
# Markdown converter — productie-image voor een VPS.
FROM python:3.13-slim

# Geen .pyc-bestanden, ongebufferde logging.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies eerst (betere laagcache: verandert er alleen appcode, dan slaat
# Docker deze stap over). De --mount=type=cache bewaart pip's downloadcache
# tussen builds in een apart BuildKit-cachevolume — dus ook op een VPS/CI die
# elke keer "schoon" bouwt (geen laagcache tussen builds) worden pakketten
# niet telkens opnieuw van PyPI gedownload. Die cache belandt niet in de
# uiteindelijke image, dus geen --no-cache-dir meer nodig.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

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
