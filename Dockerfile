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

# Versienummer (footer): commit-hash + commit-count worden op build-tijd
# meegegeven (zie docker-compose.yml / build.sh), want de image bevat geen
# .git-geschiedenis en geen git-binary. Zonder build-args toont de app alleen
# de kale VERSION (bv. "1.0.0").
ARG GIT_COMMIT=unknown
ARG GIT_COMMIT_COUNT=0
ENV GIT_COMMIT=$GIT_COMMIT
ENV GIT_COMMIT_COUNT=$GIT_COMMIT_COUNT

EXPOSE 5001

# Productieserver (gunicorn). Ruime timeout: AI-opschoning van grote documenten
# kan enkele minuten duren. Pas workers/timeout aan naar wens.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--timeout", "600", "app:app"]
