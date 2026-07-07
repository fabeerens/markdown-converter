# Markdown converter

Een lokale web-tool om jurisprudentie, wetgeving en documenten om te zetten naar nette markdown.

## Wat kan het?

De interface heeft drie tabbladen:

### 1. Jurisprudentie
Plak een ECLI of link; de tool herkent de bron automatisch:
- **Hof van Justitie EU** — EU-ECLI (bv. `ECLI:EU:C:2025:645`). Via het **Cellar**-archief van de Publicatiebureau.
- **EHRM (ECtHR)** — een HUDOC-link, item-id (bv. `001-210077`) of een EHRM-ECLI (bv. `ECLI:CE:ECHR:2021:0525JUD005817013`). Via de HUDOC-zoek-API en document-export. De taalkeuze bepaalt de versie/vertaling (Engels/Frans origineel, of een vertaling indien als HTML beschikbaar — anders terugval op het origineel).
- **Nederlandse rechtspraak** — een ECLI (bv. `ECLI:NL:HR:2012:BQ9251`) of een rechtspraak.nl-link. Via de officiële Open Data API van de Rechtspraak.

### 2. Wetgeving
- **EU-wetgeving** — CELEX-nummer (bv. `32016R0679`, de AVG), een link, of een ELI-link (bv. `https://eur-lex.europa.eu/eli/reg/2016/679/oj`). Officiële tekst uit het Cellar-archief (met terugval op de EUR-Lex portal).
- **Nederlandse wetgeving** — een wetten.overheid.nl-link of een BWB-nummer (bv. `BWBR0040940`, optioneel met versiedatum `/2021-07-01`). Staat er een **hoofdstuk-anker** in de link (bv. `…/2026-07-01#Hoofdstuk16`), dan wordt alléén dat onderdeel opgehaald en omgezet.

### 3. Documentupload
- Sleep een bestand in het venster (of klik om te bladeren), **of plak een link naar een bestand** (bv. een directe PDF-link).
- **Formex-XML** (`.xml`) van EUR-Lex → eigen structuur-parser (nette koppen, recitals, artikelen, lijsten, voetnoten).
- **Alle andere formaten** (PDF, Word, Excel, PowerPoint, HTML, CSV, JSON, EPUB…) → via [Microsoft MarkItDown](https://github.com/microsoft/markitdown). Bij PDF's worden de "zachte" regeleindes binnen een alinea automatisch samengevoegd.

Uitvoer kun je kopiëren of downloaden als `.md`.

### Optioneel: opschonen met AI (Haiku)

Na elke conversie verschijnt een knop **"Opschonen met AI"** met een **kostenraming** (aantal delen, geschatte tokens en prijs, opgehaald bij OpenRouter). Klik erop om de markdown door een klein taalmodel te halen (standaard `~anthropic/claude-haiku-latest` via [OpenRouter](https://openrouter.ai/)). Dat zet koppen om naar echte markdown-koppen, voegt losse regeleindes binnen alinea's samen en verwijdert herhalende kop-/voetteksten en paginanummers — zonder de tekst inhoudelijk te wijzigen.

Er zijn twee opmaak-profielen die automatisch worden gekozen:
- **Uitspraak-opmaak** (voor rechtspraak, HUDOC en EU-rechtspraak): koppen vanaf `##`, genummerde rechtsoverwegingen blijven behouden, citaten als blockquotes (`>`), lijsten als markdownlijsten, voetnoten als markdownvoetnoten.
- **Algemeen** (voor overige documenten, bv. PDF-rapporten): sectietitels als koppen, alinea's samenvoegen, kop-/voetteksten verwijderen.

Hiervoor heb je een OpenRouter API-sleutel nodig. Maak een bestand `.env` naast de app met:

```bash
OPENROUTER_API_KEY=sk-or-...
```

(sleutel aanmaken op <https://openrouter.ai/keys>). Een ander model of endpoint kies je optioneel met `LLM_MODEL` / `OPENROUTER_BASE_URL` in `.env`. Zonder sleutel blijft het vinkje uitgeschakeld; de rest van de tool werkt gewoon.

## Starten

**Dubbelklik in Finder op `Markdown converter.command`.**

Dat opent een Terminal-venster, installeert zo nodig de dependencies, start de server en opent je browser op **http://127.0.0.1:5001**. Stoppen: sluit het Terminal-venster (of Ctrl+C).

> Dependencies worden alleen (opnieuw) geïnstalleerd bij de eerste keer of nadat `requirements.txt` is gewijzigd — normaal start het dus meteen.

Vanaf de terminal kan ook:

```bash
./run.sh
```

Handmatig starten kan ook:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

## Voorbeelden

| Invoer | Resultaat |
|---|---|
| `32016R0679` | Algemene verordening gegevensbescherming (AVG) |
| `32011L0083` | Richtlijn consumentenrechten |
| `https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32016R0679` | idem, via link |

Kies rechtsboven de taal (standaard NL).

## Structuur

```
app.py                 Flask-server + API
converters/
  formex.py            Formex-XML → markdown (eigen parser)
  eurlex.py            ophalen bij EUR-Lex (CELEX/ELI/URL) + HTML → markdown
  caselaw.py           link-dispatcher: rechtspraak.nl (ECLI), HUDOC (EHRM), wetten.overheid.nl
  wetten.py            Nederlandse wetgeving (wetten.overheid.nl / BWB) → markdown
  generic.py           overige formaten → markdown via MarkItDown
  llm_cleanup.py       optionele AI-opschoning + kostenraming (OpenRouter)
templates/index.html   web-interface
```

## Draaien met Docker (bv. op een VPS)

Er is een `Dockerfile` en `docker-compose.yml`. De container draait de app met **gunicorn**.

```bash
# 1. (optioneel) API-sleutel voor de AI-opschoning: maak een .env met
#    OPENROUTER_API_KEY=sk-or-...

# 2. bouwen en starten
docker compose up -d --build
```

De app draait dan op **http://127.0.0.1:5001** (op de VPS zelf). Compose bindt bewust
op `127.0.0.1` — de tool heeft **geen ingebouwde authenticatie**.

> ⚠️ **Beveiliging.** Zet de tool niet zonder meer open op internet: iedereen zou 'm
> dan kunnen gebruiken, en het endpoint dat een bestand via een link ophaalt kan
> arbitraire URL's benaderen (SSRF). Ontsluit 'm publiek alleen achter een **reverse
> proxy** (nginx/Caddy/Traefik) met HTTPS **en** authenticatie (bv. Basic Auth), of
> houd 'm achter een VPN/firewall. Wil je 'm tóch direct op alle interfaces, pas dan
> de `ports` in `docker-compose.yml` aan naar `"5001:5001"`.

Logs bekijken / stoppen:

```bash
docker compose logs -f
docker compose down
```

## Op GitHub zetten

Het project is een git-repository met een `.gitignore` die `.venv`, `.env` en secrets
uitsluit. Maak een lege repo aan op GitHub en push:

```bash
git remote add origin git@github.com:<gebruiker>/markdown-converter.git
git push -u origin main
```

De AI-sleutel staat **niet** in de repo (`.env` is genegeerd). Op een nieuwe machine
maak je een `.env` met `OPENROUTER_API_KEY=...`, of je zet de env-variabele.
