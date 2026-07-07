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

EXPOSE 5001

# Productieserver (gunicorn). Ruime timeout: AI-opschoning van grote documenten
# kan enkele minuten duren. Pas workers/timeout aan naar wens.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--timeout", "600", "app:app"]
