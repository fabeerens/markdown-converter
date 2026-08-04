# Markdown converter

Een lokale web-tool om jurisprudentie, wetgeving en documenten om te zetten naar nette markdown.

## Wat kan het?

De interface heeft drie tabbladen:

### 1. Jurisprudentie
Plak een ECLI of link; de tool herkent de bron automatisch:
- **Hof van Justitie EU** — EU-ECLI (bv. `ECLI:EU:C:2025:645`). Via het **Cellar**-archief van de Publicatiebureau.
- **EHRM (ECtHR)** — een HUDOC-link, item-id (bv. `001-210077`) of een EHRM-ECLI (bv. `ECLI:CE:ECHR:2021:0525JUD005817013`). Via de HUDOC-zoek-API en document-export. De taalkeuze bepaalt de versie/vertaling (Engels/Frans origineel, of een vertaling indien als HTML beschikbaar — anders terugval op het origineel).
- **Nederlandse rechtspraak** — een ECLI (bv. `ECLI:NL:HR:2012:BQ9251`) of een rechtspraak.nl-link. Via de officiële Open Data API van de Rechtspraak.
- **Duitse rechtspraak** — een Duitse ECLI (bv. `ECLI:DE:BGH:2019:240919BVIZB39.18.0`). Via rechtsprechung-im-internet.de (BGH, BVerfG, BVerwG, BFH, BAG, BSG, BPatG).
- **Belgische rechtspraak** — een Belgische ECLI (bv. `ECLI:BE:CASS:2021:ARR.20211019.2N.25`). Via Juportal.
- **Frans Conseil constitutionnel** — een ECLI van dat hof (bv. `ECLI:FR:CC:2021:2021.931.QPC`). Andere Franse gerechten (Cour de cassation, Conseil d'État) zitten achter een bot-blokkade en worden (nog) niet ondersteund — de tool legt dat uit in de foutmelding.
- Spaanse en Oostenrijkse rechtspraak zijn onderzocht maar (nog) niet haalbaar zonder CAPTCHA-omzeiling resp. een onbetrouwbare zoekopdracht — zie `CLAUDE.md` voor de details.

### 2. Wetgeving
- **EU-wetgeving** — CELEX-nummer (bv. `32016R0679`, de AVG), een link, of een ELI-link (bv. `https://eur-lex.europa.eu/eli/reg/2016/679/oj`). Officiële tekst uit het Cellar-archief (met terugval op de EUR-Lex portal).
- **Nederlandse wetgeving** — een wetten.overheid.nl-link of een BWB-nummer (bv. `BWBR0040940`, optioneel met versiedatum `/2021-07-01`). Staat er een **hoofdstuk-anker** in de link (bv. `…/2026-07-01#Hoofdstuk16`), dan wordt alléén dat onderdeel opgehaald en omgezet.

### 3. Documentupload
- Sleep een of meer bestanden in het venster (of klik om te bladeren), **of plak een of meer links naar bestanden** (bv. directe PDF-links).
- **Formex-XML** (`.xml`) van EUR-Lex → eigen structuur-parser (nette koppen, recitals, artikelen, lijsten, voetnoten).
- **PDF** → via [pdf-inspector](https://github.com/firecrawl/pdf-inspector), met nette, layout-bewuste markdown (koppen, lijsten, tabellen). Bij een gescande/foto-PDF zonder tekstlaag valt de tool terug op MarkItDown.
- **Alle andere formaten** (Word, Excel, PowerPoint, HTML, CSV, JSON, EPUB…) → via [Microsoft MarkItDown](https://github.com/microsoft/markitdown). Bij tekstbestanden worden de "zachte" regeleindes binnen een alinea automatisch samengevoegd.

### Meerdere documenten tegelijk

Bij Jurisprudentie en Wetgeving kun je met **"+ Document toevoegen"** meerdere ECLI's/CELEX-
nummers/links tegelijk invoeren; **Ophalen** haalt ze allemaal parallel op. Bij Documentupload
kun je meerdere bestanden tegelijk slepen/kiezen en/of meerdere links toevoegen. Elk opgehaald
document krijgt een eigen **tabblad** boven de uitvoer — je schakelt ertussen om te bekijken,
bewerken, opschonen met AI (elk document met zijn eigen profiel/model) en los te downloaden.
Mislukt een van de documenten (bv. een ongeldige ECLI), dan blijft de rest gewoon beschikbaar;
de status onder de knop toont wat wel en niet is gelukt.

De tool volgt automatisch de **licht/donker-instelling van je systeem** — er is geen knop,
je hoeft niets te kiezen.

Uitvoer kun je kopiëren of downloaden als `.md`. Links naast de tekst staan **regelnummers**
(altijd zichtbaar) — handig om een bepaalde regel terug te vinden of ernaar te verwijzen.
Eén nummer staat voor één "enter" in de brontekst: een lange zin die over meerdere
schermregels wordt afgebroken (word-wrap), telt dus toch als één regel.

Onderaan de pagina staat een footer,
bv. `v1.0.0 (build 3) · geïnstalleerd op 07-07-2026 17:37`:
- `1.0.0` komt uit het `VERSION`-bestand (major.minor.patch; handmatig aanpassen bij een echte release).
- **Build-nummer en installatiedatum lopen automatisch op.** De app herkent zelf wanneer
  de broncode is gewijzigd (een checksum van `app.py`, `mdconv/`, `templates/`, `static/` en
  `VERSION`) en hoogt dan het build-nummer op met de datum/tijd van dat moment. Geen
  git nodig, geen handmatige stap. De staat wordt bijgehouden in `.deploy-state/`
  (genegeerd door git; in Docker gemount als volume zodat 'ie een rebuild overleeft).

### Optioneel: opschonen met AI

Na elke conversie verschijnt een knop **"Opschonen met AI"** met een **model-keuze** en
een **kostenraming** (aantal delen, tokens en prijs — de prijs wordt live bij OpenRouter
opgehaald). Klik op **Opschonen** om de markdown door het gekozen taalmodel te halen (via
[OpenRouter](https://openrouter.ai/)). Dat zet koppen om naar echte markdown-koppen, voegt
losse regeleindes binnen alinea's samen en verwijdert herhalende kop-/voetteksten en
paginanummers — zonder de tekst inhoudelijk te wijzigen.

**Model kiezen** — de dropdown biedt (allemaal via dezelfde OpenRouter-sleutel):

| Model | Prijs in/uit per 1M tokens |
|---|---|
| Claude Haiku (latest) — standaard | zie OpenRouter |
| GLM 5.2 (nitro) | $0,93 / $3 |
| GPT-5.6 Luna (nitro) | $1 / $6 |
| DeepSeek V4 Flash (nitro) | $0,09 / $0,18 |
| Claude Haiku 4.5 (nitro) | $1 / $5 |
| Claude Sonnet 5 (nitro) | $2 / $10 |
| GPT-OSS 120B (nitro) | $0,036 / $0,18 |

Je keuze wordt onthouden (browser-lokaal) voor de volgende keer. `:nitro` kiest automatisch
de snelste provider voor dat model.

Er zijn twee opmaak-profielen die automatisch worden gekozen (los van het model):
- **Uitspraak-opmaak** (voor rechtspraak, HUDOC en EU-rechtspraak): koppen vanaf `##`, genummerde rechtsoverwegingen blijven behouden, citaten als blockquotes (`>`), lijsten als markdownlijsten, voetnoten als markdownvoetnoten.
- **Algemeen** (voor overige documenten, bv. PDF-rapporten): sectietitels als koppen, alinea's samenvoegen, kop-/voetteksten verwijderen.

Bij **Jurisprudentie** verschijnt daarnaast een vinkje **"Opmaken voor Obsidian"**.
Die levert één complete Obsidian-notitie op — YAML-frontmatter, een inhoudsopgave-callout,
een gestructureerde juridische analyse (feiten, rechtsvragen, argumenten, conclusie, impact)
én de volledige uitspraak verbatim — volgens een vast sjabloon. Bij een **anderstalige**
uitspraak (bv. Duits) wordt de volledige uitspraak als tweekolomstabel opgeleverd: links het
origineel per rechtsoverweging, rechts de Nederlandse vertaling. Dit profiel verwerkt de hele
uitspraak in **één** verzoek (niet in delen, want frontmatter/analyse mag maar één keer
voorkomen); bij een zeer lang arrest kan de output daardoor tegen het model-uitvoerplafond
aanlopen — de tool geeft dan een duidelijke foutmelding (gebruik in dat geval het
standaardprofiel) in plaats van een stilletjes afgekapt resultaat.

**Let op bij de kostenraming:** het getoonde aantal tokens is de **invoergrootte** van het
document (niet invoer+uitvoer opgeteld). Het aantal delen (chunks) hangt af van die
invoergrootte gedeeld door ~55.000 tokens per deel.

### Instellingen (rechtsboven, ⚙)

Rechtsboven in de header staat een instellingenknop. Daarin kun je zonder de code aan
te passen configureren:
- **AI-endpoints** — endpoints toevoegen, aanpassen of verwijderen (model-id + label
  in de dropdown), of terug naar de standaardlijst.
- **Deelgrootte** — het aantal tokens per deel bij chunking (standaard 55.000).
- **Prompts** — de systeemprompt voor "algemeen" opschonen, voor "jurisprudentie"
  opschonen, en voor "Opmaken voor Obsidian", elk met een eigen reset-naar-standaard.

Wijzigingen gelden meteen voor alle volgende conversies en blijven bewaard in
`.deploy-state/settings.json` (buiten git, overleeft herstarts en — in Docker — ook
image-rebuilds dankzij dezelfde volume-mount als het versienummer). Een leeg
gelaten veld valt terug op de ingebouwde standaardwaarde.

Hiervoor heb je een OpenRouter API-sleutel nodig. Maak een bestand `.env` naast de app met:

```bash
OPENROUTER_API_KEY=sk-or-...
```

(sleutel aanmaken op <https://openrouter.ai/keys>). Een ander standaardmodel of endpoint kies je optioneel met `LLM_MODEL` / `OPENROUTER_BASE_URL` in `.env` (de dropdown in de UI overschrijft dit per keer). Zonder sleutel blijft het opschoon-paneel uitgeschakeld; de rest van de tool werkt gewoon.

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
app.py                 startpunt (ook het gunicorn-doel)
mdconv/                de applicatie
  api.py               alle API-routes
  net.py               gedeelde HTTP-client met connection pooling
  state.py             instellingen/versie op schijf, met cache
  render.py            HTML → markdown
  sources/             per bron: eurlex, rechtspraak, hudoc, wetten, formex, files
  cleanup/             AI-opschoning: prompts, chunking, OpenRouter, instellingen
templates/index.html   de pagina
static/app.css         opmaak (Radix-stijl, dark/light)
static/app.js          front-end
tests/                 tests (pytest)
```

## Draaien met Docker (bv. op een VPS)

Er is een `Dockerfile` en `docker-compose.yml`. De container draait de app met **gunicorn**.

```bash
# 1. (optioneel) API-sleutel voor de AI-opschoning: maak een .env met
#    OPENROUTER_API_KEY=sk-or-...

# 2. bouwen en starten
./build.sh
```

`build.sh` bouwt en start de container; `docker compose up -d --build` werkt net zo goed.
Het build-nummer/de installatiedatum in de footer worden door de app zelf bijgehouden
(zie hierboven) — daar hoeft de build/deploy-stap niets voor te doen.

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
