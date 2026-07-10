"""Optional LLM cleanup pass via OpenRouter (OpenAI-compatible API).

Takes the mechanically-converted markdown and asks a small model
(default: ``~anthropic/claude-haiku-latest``) to:
  - convert headings to real Markdown headings (#, ##, ###);
  - join lines that were split mid-sentence back into paragraphs;
  - drop repeating page headers/footers and page numbers;
  - tidy up extraction artefacts,

while preserving the original wording and language faithfully (no
summarising, translating, or adding content).

Configuration (environment variables):
  OPENROUTER_API_KEY   required — your OpenRouter key
  LLM_MODEL            model slug (default "~anthropic/claude-haiku-latest")
  OPENROUTER_BASE_URL  API base (default "https://openrouter.ai/api/v1")

If no key is set, `clean_markdown` raises a ValueError with a clear message
and the caller falls back to the raw output.
"""

from __future__ import annotations

import json
import os
import re

import requests

try:
    import fcntl  # POSIX file locking (macOS/Linux); absent on Windows.
except ImportError:  # pragma: no cover
    fcntl = None

DEFAULT_MODEL = "~anthropic/claude-haiku-latest"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Chunk target: ~55000 tokens per request (≈ 4 characters per token) — the
# built-in default; the user can override this and everything else below via
# the settings gear icon in the UI (see get_settings_payload/update_settings).
# Most documents fit in a single call. Note: cleanup output ≈ input length, so
# a chunk near this size sits comfortably under Haiku's ~64000-token output
# cap — lower it further if you see truncated output on very dense documents.
_DEFAULT_CHUNK_TOKENS = 55000
_MIN_CHUNK_TOKENS = 5000
_MAX_CHUNK_TOKENS = 190000              # leaves headroom under Haiku's 200k context
_MAX_OUTPUT_TOKENS = 64000              # Haiku's output ceiling

_SYSTEM_GENERIC = """\
You clean up Markdown that was mechanically extracted from a document (often a
PDF or a court/legislative text). Your ONLY job is to reformat and tidy — never
to change the meaning.

Do all of the following:
- Turn only GENUINE section titles into Markdown headings (#, ##, ###) that
  reflect their hierarchy: the document title (#), and named structural
  divisions such as parts, chapters, titles, sections, or headings like
  "PROCEDURE", "THE FACTS", "THE LAW", "Considerandi", "Conclusie", "Artikel 5",
  "HOOFDSTUK II". Use the level to reflect nesting.
- Join lines that were split mid-sentence back into flowing paragraphs. A hard
  line break inside a sentence is an extraction artefact — remove it. Keep real
  paragraph breaks (blank lines between paragraphs).
- Remove repeating page headers and footers, running titles, and standalone page
  numbers that appear between or inside the text.
- Fix words that were split across lines with a hyphen.
- Keep genuine lists, tables, quotes and numbered items intact as Markdown.

Headings — critical:
- Do NOT turn numbered legal paragraphs into headings. Recitals, considerations
  ("overwegingen"), margin/paragraph numbers ("randnummers"), points, and list
  items — anything that is essentially a running text unit introduced by a number
  or letter such as "1.", "12.", "(1)", "(a)", "§ 3", "45." — MUST stay as normal
  paragraphs (or list items), even when the number sits alone on its own line.
  Keep the number together with its text; never promote it to #, ##, or ###.
- When unsure whether a line is a section title or just a numbered paragraph,
  keep it as a paragraph. Only a handful of real section titles per document
  should become headings.

Strict rules:
- Preserve the original wording exactly. Do NOT summarise, paraphrase, translate,
  correct, or add anything. Every sentence of real content must remain.
- Keep the document's original language.
- Output ONLY the cleaned Markdown. No preamble, no explanation, no code fences.\
"""

# Profile tuned for court decisions and judgments (uitspraken/arresten).
_SYSTEM_CASELAW = """\
You reformat a court decision or judgment (uitspraak/arrest) into clean Markdown.
Your ONLY job is to reformat — never change the meaning.

Rules:
- Headings start at `##`. Use `##` for the main sections (e.g. "PROCEDURE",
  "THE FACTS", "THE LAW", "Feiten", "Beoordeling", "Beslissing", "Conclusie"),
  and `###`/`####` for subsections. Do NOT use a single `#`.
- Keep numbered legal considerations ("rechtsoverwegingen"/"randnummers", e.g.
  "1.", "12.", "(1)", "45.") exactly as running numbered paragraphs — never turn
  a paragraph number into a heading. Keep the number together with its text.
- Render quoted passages (cited legal provisions, quotations from other
  decisions, quoted submissions) as Markdown blockquotes with `>`.
- Render enumerations as Markdown lists (keep the original letter/number labels
  such as (a), (b), i, ii within the list items).
- Render footnotes as Markdown footnotes: a reference `[^1]` at the place in the
  text, and the note text as `[^1]: …` collected at the end.
- Join lines split mid-sentence back into flowing paragraphs; remove repeating
  page headers/footers and standalone page numbers; fix hyphenated line-break
  word splits.

Strict rules:
- Preserve the original wording exactly. Do NOT summarise, paraphrase, translate,
  correct, or add anything. Every sentence of real content must remain.
- Keep the document's original language.
- Output ONLY the cleaned Markdown. No preamble, no explanation, no code fences.\
"""

# "Opmaken voor Obsidian" — turns a judgment into a complete Obsidian note
# (YAML frontmatter, a table-of-contents callout, a structured legal analysis,
# and the full verbatim text). Based on the user's own skill file
# (~/Downloads/SKILL jurisprudentie.md), copied verbatim minus its skill
# frontmatter (that part is Claude Code skill-invocation metadata, not model
# instructions). Unlike the other profiles, this one must see — and
# reproduce — the ENTIRE document in a single response: chunking it would
# stitch together multiple duplicate frontmatters/analyses, which makes no
# sense for one note. See _NO_CHUNK_PROFILES below.
_SYSTEM_OBSIDIAN = """\
Je bent een juridische jurisprudentie-assistent voor Nederlandse rechtspraak, het EHRM en
het HvJ EU. Je levert één Obsidian-ready Markdownbestand op basis van (1) een link naar een
uitspraak of (2) de volledige tekst die de gebruiker plakt.

## Outputcontract (lees dit eerst)

De gebruiker wil het resultaat **plakken** in Obsidian. Lever daarom **uitsluitend één
Markdown-codeblok** terug — de volledige notitie, van de openende `---` tot en met de laatste
voetnoot. Geen inleiding, geen toelichting, geen voorbehoud, geen tekst buiten het codeblok.

Het codeblok begint met ```` ```markdown ```` en eindigt met ```` ``` ````.

## Waarom verbatim reproduceren mag

Onder `## Volledige uitspraak` komt de **integrale** uitspraaktekst, letterlijk en onverkort.
Dat mag: officiële teksten van rechterlijke aard zijn uitgezonderd van auteursrecht
(art. 11 Auteurswet; art. 2 lid 4 Berner Conventie). Uitspraken van de Nederlandse rechter
(rechtspraak.nl), het EHRM (HUDOC) en het HvJ EU (curia.europa.eu) zijn openbare stukken.
Vat deze sectie dus nooit samen, vertaal niet en laat niets weg — zet alleen om naar Markdown.

## De vaste outputtemplate

Vul exact deze structuur; de volgorde en de kopniveaus liggen vast. Placeholders tussen
`{ }` vervang je, lege YAML-velden laat je leeg als de informatie ontbreekt.

```
---
Onderdeel van:
  - "[[Atlas/Jurisprudentie/Jurisprudentie|Jurisprudentie]]"
Naam: {korte roepnaam van de zaak}
Datum: {YYYY-MM-DD}
ECLI: {ECLI of leeg}
Zaaknummer: {zaak-/applicatienummer of leeg}
Link: {officiële bronlink of leeg}
Instantie: {exact één waarde uit de toegestane lijst}
Tags:
  - jurisprudentie
  - {tag 2}
  - {tag 3}
---
> [!toc]- Inhoudsopgave
> - [[#### {inhoudelijke hoofdkop 1}]]
> - [[#### {inhoudelijke hoofdkop 2}]]

## Samenvatting
### In het kort
> [!samenvatting] {naam uitspraak}
> {korte rechtsregel-samenvatting van enkele zinnen}

# Feiten
{chronologisch overzicht van relevante feiten}

# Rechtsvragen
{genummerde centrale juridische vragen}

## Argumenten
{standpunten per partij}

## Conclusie
{beslissing en dragende overwegingen}

# Impact
{precedentwerking en praktische gevolgen}

## Volledige uitspraak

{integrale uitspraak, omgezet naar Markdown}
```

## De analyse-instructies (feiten → impact)

Gebruik exact de kopniveaus hierboven (`# Feiten`, `# Rechtsvragen`, `## Argumenten`,
`## Conclusie`, `# Impact`) en volg per onderdeel:

- **Feiten** — chronologisch overzicht van de relevante feiten; focus op wat direct relevant
  is voor de rechtsvragen; vermijd interpretaties of juridische kwalificaties; objectieve,
  neutrale taal.
- **Rechtsvragen** — identificeer de centrale juridische vragen; formuleer elke vraag helder
  en beknopt; nummer ze; groepeer gerelateerde subvragen.
- **Argumenten** — beschrijf de standpunten van alle partijen (label vetgedrukt, bv.
  `**Klager:**` / `**Regering:**` / `**Verweerder:**`); geef de belangrijkste argumenten;
  verwijs naar relevante wetgeving en jurisprudentie; onderscheid feitelijke van juridische
  argumenten.
- **Conclusie** — vat de beslissing samen; beschrijf de dragende overwegingen; citeer
  kernachtige overwegingen kort en letterlijk; leg uit hoe de conclusie uit de argumentatie
  volgt; benoem een concurring/dissenting opinion apart als die er is.
- **Impact** — analyseer de precedentwerking; beschrijf praktische gevolgen voor vergelijkbare
  gevallen; benoem relevante sectoren en eventuele maatschappelijke impact; geef aan of
  vervolgprocedures waarschijnlijk zijn.

**Bronvermelding is verplicht.** Verwijs in de analyse waar mogelijk naar de specifieke
rechtsoverweging/paragraaf (bv. "r.o. 3.2", "§ 86") en naar de concrete wetsartikelen die de
rechter toepast (bv. "art. 8 EVRM", "art. 32 Grondwet").

## Workflow

### 1. Input bepalen
- Kreeg je een **link**? Gebruik die als `Link:` en haal daaruit de volledige tekst, metadata
  en inhoud (fetch de pagina).
- Kreeg je **losse tekst**? Gebruik die als bron. Zoek zo nodig het ECLI-nummer en de
  officiële bronlink op (rechtspraak.nl, HUDOC, curia) en verifieer die voordat je hem opneemt.
- Maak géén juridische aannames als metadata ontbreekt. Laat het betreffende YAML-veld dan leeg.

### 2. Metadata (YAML-frontmatter)
- `Onderdeel van:` altijd exact: `"[[Atlas/Jurisprudentie/Jurisprudentie|Jurisprudentie]]"`
- `Naam:` de gangbare (korte) roepnaam van de zaak.
- `Datum:` de uitspraakdatum in `YYYY-MM-DD`.
- `ECLI:` volledige ECLI; leeg laten als onbekend.
- `Zaaknummer:` zaak-/applicatienummer (bij EHRM het application no.).
- `Link:` de officiële bronlink.
- `Instantie:` exact één waarde uit de toegestane lijst hieronder.
- `Tags:` begin altijd met `jurisprudentie`; voeg maximaal vier inhoudelijke tags toe
  (max. vijf totaal). Meerwoordige tags met koppeltekens, bv. `artikel-8-EVRM`,
  `gegevensbescherming`.

**Toegestane instanties (kies exact één):**
EHRM, HvJ EU, Hoge Raad (HR), Raad van State (RvS), Gerechtshof Amsterdam,
Gerechtshof Arnhem-Leeuwarden, Gerechtshof Den Haag, Gerechtshof 's-Hertogenbosch,
College van Beroep voor het bedrijfsleven (CBb), Centrale Raad van Beroep (CrvB),
Rechtbank Amsterdam, Rechtbank Den Haag, Rechtbank Gelderland, Rechtbank Limburg,
Rechtbank Midden-Nederland, Rechtbank Noord-Holland, Rechtbank Noord-Nederland,
Rechtbank Oost-Brabant, Rechtbank Overijssel, Rechtbank Rotterdam,
Rechtbank Zeeland-West-Brabant.

### 3. Inhoudsopgave-callout
Direct na de frontmatter, vóór `## Samenvatting`. Gebruik wikilinks naar de **inhoudelijke
hoofdkoppen van de volledige uitspraak** (de `####`-koppen uit stap 6), in dezelfde volgorde.
Twee harde eisen, omdat een Obsidian-wikilink anders niet resolvet:
- **Sla het titel-/kopblok over** (bv. `FIFTH SECTION`, `CASE OF ...`, `JUDGMENT`/`ARREST`,
  vindplaatsregels). De TOC begint bij de eerste procesinhoudelijke sectie
  (bv. `PROCEDURE`/`Procesverloop`).
- Het label ná `####` in de wikilink moet **teken voor teken identiek** zijn aan de
  bijbehorende kop, inclusief nummering, hoofdletters, apostrofs en leestekens (ook een
  afsluitende komma). Kort de tekst niet in en laat geen leestekens weg.

### 4. Uitgebreide analyse
Maak de analyse (`# Feiten` t/m `# Impact`) volgens "De analyse-instructies" hierboven. Deze
uitgebreide analyse staat **onder** de `[!samenvatting]`-callout en **boven**
`## Volledige uitspraak`.

### 5. Korte rechtsregel-samenvatting (callout)
Distilleer uit de analyse een bondige samenvatting van enkele zinnen (de rechtsregel + kern) en
plaats die in de `[!samenvatting]`-callout onder `## Samenvatting` → `### In het kort`, met
achter `[!samenvatting]` de naam van de uitspraak.

### 6. Volledige uitspraak in Markdown
Onder `## Volledige uitspraak` de integrale tekst, omgezet naar nette Markdown:
- **Hoofdkoppen** van de uitspraak (bv. PROCEDURE, THE FACTS, THE LAW, Procesverloop,
  Overwegingen, Beslissing) → `####`.
- **Subkoppen** (bv. "A. History of ...", "I. ...") → `#####`.
- **Genummerde rechtsoverwegingen** behouden hun nummer (`12. ...`).
- **Citaten** als blockquotes (`>`).
- **Opsommingen** als Markdownlijsten.
- **Voetnoten** als Markdown-voetnoten: verwijzing `[^1]` in de tekst, definities
  (`[^1]: ...`) onderaan het bestand. Zorg dat elke verwijzing een definitie heeft en omgekeerd.
- **Tabellen** als Markdowntabellen, indien aanwezig.

### 7. Eindcontrole vóór uitvoer
- Sectievolgorde: frontmatter → `[!toc]`-callout → `## Samenvatting`/`### In het kort`/callout
  → `# Feiten` … `# Impact` → `## Volledige uitspraak`.
- TOC-links: labels zijn teken-voor-teken gelijk aan de `####`-koppen, en het titelblok is
  overgeslagen (TOC begint bij de eerste inhoudelijke sectie).
- Volledige uitspraak is onverkort en verbatim.
- Alle voetnoten hebben zowel een verwijzing als een definitie.
- Output is **één** `markdown`-codeblok, verder niets.\
"""

_DEFAULT_PROMPTS = {
    "generic": _SYSTEM_GENERIC, "caselaw": _SYSTEM_CASELAW, "obsidian": _SYSTEM_OBSIDIAN,
}

# Profiles that must run as a single, unsplit request instead of the normal
# chunked flow (see _SYSTEM_OBSIDIAN's docstring for why).
_NO_CHUNK_PROFILES = {"obsidian"}

# Rough output/input length ratio per profile, for the cost estimate. The
# reformat-only profiles roughly preserve length (~1.0); "obsidian" adds
# frontmatter, a table of contents and a substantial legal analysis on top
# of the full verbatim text, so its output runs noticeably longer.
_OUTPUT_RATIO = {"obsidian": 1.35}

# Custom user-message framing per profile; anything not listed uses the
# default "clean up this fragment" framing (see _clean_chunk).
_USER_PROMPTS = {
    "obsidian": (
        "Hieronder staat de volledige, al naar Markdown omgezette tekst van de "
        "uitspraak. Verwerk die exact volgens de systeeminstructies en lever de "
        "complete Obsidian-notitie op.\n\n{chunk}"
    ),
}
_DEFAULT_USER_PROMPT = "Clean up this Markdown fragment:\n\n{chunk}"

# The obsidian profile's output contract wraps its answer in a ```markdown
# fence; strip that so the app's textarea shows plain Markdown, not a
# fenced code block containing Markdown.
_FENCE_RE = re.compile(r"^```(?:markdown)?\s*\n(.*?)\n```\s*$", re.S)


def _strip_markdown_fence(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text

# Selectable models (all via the same OpenRouter key). ":nitro" routes to the
# fastest provider for that model. Prices below are indicative — the UI
# fetches live pricing via get_pricing() rather than trusting this comment.
# This is the built-in default list; the user can add/edit/remove entries via
# the settings gear icon (see get_model_choices/update_settings below).
_DEFAULT_MODEL_CHOICES = [
    {"id": "~anthropic/claude-haiku-latest", "label": "Claude Haiku (latest) — standaard"},
    {"id": "z-ai/glm-5.2:nitro", "label": "GLM 5.2 (nitro) — $0,93 / $3 per 1M"},
    {"id": "openai/gpt-5.6-luna:nitro", "label": "GPT-5.6 Luna (nitro) — $1 / $6 per 1M"},
    {"id": "deepseek/deepseek-v4-flash:nitro", "label": "DeepSeek V4 Flash (nitro) — $0,09 / $0,18 per 1M"},
    {"id": "anthropic/claude-haiku-4.5:nitro", "label": "Claude Haiku 4.5 (nitro) — $1 / $5 per 1M"},
    {"id": "anthropic/claude-sonnet-5:nitro", "label": "Claude Sonnet 5 (nitro) — $2 / $10 per 1M"},
    {"id": "openai/gpt-oss-120b:nitro", "label": "GPT-OSS 120B (nitro) — $0,036 / $0,18 per 1M"},
]

# --- User-configurable settings (gear icon in the UI) -----------------------
#
# Persisted as JSON in .deploy-state/settings.json — the same gitignored,
# Docker-volume-mounted state directory already used for the build counter
# (see app.py:_STATE_DIR). Only keys the user actually changed are stored;
# anything missing/empty falls back to the _DEFAULT_* constant above, which
# is also how the UI's "reset to default" works without a separate endpoint.

_STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".deploy-state")
_SETTINGS_PATH = os.path.join(_STATE_DIR, "settings.json")
_SETTINGS_LOCK_PATH = os.path.join(_STATE_DIR, ".settings.lock")


def _read_settings_raw() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_settings_raw(data: dict) -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    with open(_SETTINGS_LOCK_PATH, "w") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def get_model_choices() -> list[dict]:
    """The model dropdown's entries — user-configured list, or the default."""
    models = _read_settings_raw().get("models")
    if isinstance(models, list) and models:
        cleaned = [
            {"id": str(m.get("id", "")).strip(), "label": str(m.get("label", "")).strip()}
            for m in models if isinstance(m, dict) and str(m.get("id", "")).strip()
        ]
        if cleaned:
            return cleaned
    return _DEFAULT_MODEL_CHOICES


def get_chunk_tokens() -> int:
    """Chunk size target in tokens — user-configured, or the default."""
    value = _read_settings_raw().get("chunk_tokens")
    try:
        value = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_CHUNK_TOKENS
    return value if _MIN_CHUNK_TOKENS <= value <= _MAX_CHUNK_TOKENS else _DEFAULT_CHUNK_TOKENS


def get_prompt(profile: str) -> str:
    """System prompt for `profile` — user-configured override, or the default."""
    prompts = _read_settings_raw().get("prompts")
    if isinstance(prompts, dict):
        override = prompts.get(profile)
        if isinstance(override, str) and override.strip():
            return override
    return _DEFAULT_PROMPTS.get(profile, _SYSTEM_GENERIC)


def get_settings_payload() -> dict:
    """Everything the settings UI needs: current values + defaults to reset to."""
    return {
        "models": get_model_choices(),
        "chunk_tokens": get_chunk_tokens(),
        "prompts": {p: get_prompt(p) for p in _DEFAULT_PROMPTS},
        "defaults": {
            "models": _DEFAULT_MODEL_CHOICES,
            "chunk_tokens": _DEFAULT_CHUNK_TOKENS,
            "prompts": _DEFAULT_PROMPTS,
            "min_chunk_tokens": _MIN_CHUNK_TOKENS,
            "max_chunk_tokens": _MAX_CHUNK_TOKENS,
        },
    }


def update_settings(payload: dict) -> dict:
    """Merge `payload` over the stored settings and persist. Returns the new payload.

    Any key omitted from `payload` keeps its current stored value. Passing an
    empty/invalid value for a key (empty list, empty string, out-of-range
    number) clears that key back to "use the default" rather than storing
    the invalid value.
    """
    current = _read_settings_raw()

    if "models" in payload:
        models = payload["models"]
        cleaned = [
            {"id": str(m.get("id", "")).strip(), "label": str(m.get("label", "")).strip()}
            for m in models if isinstance(m, dict) and str(m.get("id", "")).strip()
        ] if isinstance(models, list) else []
        if cleaned:
            current["models"] = cleaned
        else:
            current.pop("models", None)

    if "chunk_tokens" in payload:
        try:
            value = int(payload["chunk_tokens"])
        except (TypeError, ValueError):
            value = None
        if value is not None and _MIN_CHUNK_TOKENS <= value <= _MAX_CHUNK_TOKENS:
            current["chunk_tokens"] = value
        else:
            current.pop("chunk_tokens", None)

    if "prompts" in payload and isinstance(payload["prompts"], dict):
        prompts = dict(current.get("prompts") or {})
        for profile in _DEFAULT_PROMPTS:
            if profile not in payload["prompts"]:
                continue
            text = payload["prompts"][profile]
            if isinstance(text, str) and text.strip():
                prompts[profile] = text
            else:
                prompts.pop(profile, None)
        if prompts:
            current["prompts"] = prompts
        else:
            current.pop("prompts", None)

    _write_settings_raw(current)
    return get_settings_payload()


def _system_for(profile: str) -> str:
    return get_prompt(profile)


def _api_key() -> str | None:
    # `or None` so an empty string (e.g. an unset Docker env var) counts as absent.
    return os.environ.get("OPENROUTER_API_KEY") or None


def is_available() -> bool:
    return bool(_api_key())


def _model(override: str | None = None) -> str:
    # Explicit per-request choice (from the UI) wins; otherwise fall back to
    # the env var; `or DEFAULT` so an empty env var falls back to default.
    valid_ids = {m["id"] for m in get_model_choices()}
    if override and override in valid_ids:
        return override
    return os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def _base_url() -> str:
    # Normalise so the caller can append /chat/completions or /models safely,
    # even if the user configured a URL that already includes the path.
    url = (os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


# Cache the pricing lookup so the estimate doesn't hit OpenRouter every time.
_pricing_cache: dict[str, dict | None] = {}


def get_pricing(model: str) -> dict | None:
    """Return {'prompt': $/token, 'completion': $/token} for a model, or None.

    OpenRouter's /models catalogue lists bare model ids only — a routing
    suffix like ":nitro" or ":floor" doesn't appear as a separate entry (it
    picks a provider, not a differently-priced model), so we also match on
    the id with any such suffix stripped.
    """
    if model in _pricing_cache:
        return _pricing_cache[model]
    base_id = model.split(":", 1)[0]
    result = None
    try:
        base = _base_url().rstrip("/")
        resp = requests.get(f"{base}/models", timeout=20)
        if resp.status_code == 200:
            for m in resp.json().get("data", []):
                if m.get("id") in (model, base_id):
                    p = m.get("pricing") or {}
                    result = {
                        "prompt": float(p.get("prompt", 0) or 0),
                        "completion": float(p.get("completion", 0) or 0),
                    }
                    break
    except Exception:  # noqa: BLE001
        result = None
    _pricing_cache[model] = result
    return result


def estimate(markdown: str, profile: str = "generic", model: str | None = None) -> dict:
    """Estimate chunks, tokens and cost for cleaning `markdown`.

    Token counts are approximate (≈ 4 characters per token) — enough to gauge
    cost, not exact billing.
    """
    text = markdown.strip()
    if profile in _NO_CHUNK_PROFILES:
        chunks = [text] if text else []
    else:
        chunks = _split_chunks(text) if text else []
    n = len(chunks)
    sys_tokens = len(_system_for(profile)) // 4
    content_tokens = sum(len(c) for c in chunks) // 4
    # Each chunk resends the system prompt + a little message overhead.
    input_tokens = content_tokens + (sys_tokens + 20) * n
    output_tokens = int(content_tokens * _OUTPUT_RATIO.get(profile, 1.0))
    model = _model(model)
    pricing = get_pricing(model) if is_available() else None
    cost = None
    if pricing:
        cost = input_tokens * pricing["prompt"] + output_tokens * pricing["completion"]
    return {
        "available": is_available(),
        "model": model,
        "chunks": n,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "pricing": pricing,
        "cost_usd": cost,
    }


def _pack(pieces: list[str], sep: str, limit: int) -> list[str]:
    """Greedily pack pieces (joined by `sep`) into chunks up to `limit` chars.

    A single piece larger than `limit` is passed through as its own
    (oversized) chunk — the caller is responsible for splitting it further.
    """
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = f"{buf}{sep}{piece}" if buf else piece
        if buf and len(candidate) > limit:
            chunks.append(buf)
            buf = piece
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def _split_chunks(text: str, limit: int | None = None) -> list[str]:
    """Split text into chunks of at most `limit` characters.

    Prefers splitting on blank lines (paragraphs), then single newlines, then
    whitespace, falling back to a hard character slice. This guarantees every
    chunk respects `limit` even for text with no paragraph breaks at all —
    e.g. a PDF/Word conversion that reflows into one continuous block — which
    a paragraph-only split would otherwise pass through as a single oversized
    chunk that never gets split.
    """
    if limit is None:
        limit = get_chunk_tokens() * 4  # ≈ 4 characters per token
    chunks: list[str] = []
    for chunk in _pack(text.split("\n\n"), "\n\n", limit) or [text]:
        if len(chunk) <= limit:
            chunks.append(chunk)
            continue
        # This "paragraph" alone exceeds the limit — split it further.
        sub = _pack(chunk.split("\n"), "\n", limit)
        for piece in sub:
            if len(piece) <= limit:
                chunks.append(piece)
                continue
            words = _pack(piece.split(" "), " ", limit)
            for word_chunk in words:
                if len(word_chunk) <= limit:
                    chunks.append(word_chunk)
                else:
                    # A single "word" longer than the limit (e.g. no spaces
                    # at all) — hard-slice as a last resort.
                    chunks.extend(
                        word_chunk[i:i + limit] for i in range(0, len(word_chunk), limit)
                    )
    return chunks or [text]


def _clean_chunk(
    chunk: str, api_key: str, model: str, base_url: str, system: str, profile: str = "generic",
) -> str:
    user_prompt = _USER_PROMPTS.get(profile, _DEFAULT_USER_PROMPT).format(chunk=chunk)
    resp = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers used by OpenRouter for ranking.
            "X-Title": "EUR-Lex naar Markdown",
        },
        json={
            "model": model,
            "temperature": 0,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=600,
    )
    if resp.status_code == 401:
        raise ValueError(
            "AI-opschoning niet beschikbaar: ongeldige of ontbrekende OpenRouter API-sleutel. "
            "Controleer OPENROUTER_API_KEY en herstart de tool."
        )
    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "") or resp.text[:200]
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise ValueError(f"AI-opschoning mislukt (OpenRouter {resp.status_code}): {detail}")

    data = resp.json()
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"AI-opschoning gaf een onverwacht antwoord: {str(data)[:200]}") from e

    if profile == "obsidian":
        content = _strip_markdown_fence(content)
    return content


def clean_markdown(markdown: str, profile: str = "generic", model: str | None = None) -> str:
    """Run the LLM cleanup pass. Raises ValueError on auth/config/API problems.

    profile: "generic" (default), "caselaw" (court decisions / judgments), or
    "obsidian" (full Obsidian note — YAML frontmatter, ToC, legal analysis,
    and the full verbatim text; runs as a single unsplit request, see
    _NO_CHUNK_PROFILES).
    model: optional OpenRouter model id override (see MODEL_CHOICES); falls
    back to LLM_MODEL / the default when not given or not recognised.
    """
    text = markdown.strip()
    if not text:
        return markdown

    api_key = _api_key()
    if not api_key:
        raise ValueError(
            "AI-opschoning niet beschikbaar: geen OpenRouter API-sleutel. "
            "Zet de omgevingsvariabele OPENROUTER_API_KEY en herstart de tool."
        )

    model = _model(model)
    base_url = _base_url()
    system = _system_for(profile)
    chunks = [text] if profile in _NO_CHUNK_PROFILES else _split_chunks(text)

    try:
        cleaned = [_clean_chunk(c, api_key, model, base_url, system, profile) for c in chunks]
    except requests.exceptions.RequestException as e:
        raise ValueError(f"AI-opschoning mislukt (verbindingsfout): {e}") from e

    return "\n\n".join(c for c in cleaned if c).strip() + "\n"
