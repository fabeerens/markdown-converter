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

import os

import requests

DEFAULT_MODEL = "~anthropic/claude-haiku-latest"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Chunk target: ~60000 tokens per request (≈ 4 characters per token). Most
# documents fit in a single call. Note: cleanup output ≈ input length, so a
# chunk near this size sits close to Haiku's ~64000-token output cap — lower
# _CHUNK_TOKENS if you see truncated output on very dense documents.
_CHUNK_TOKENS = 60000
_CHUNK_CHARS = _CHUNK_TOKENS * 4        # ≈ 240000 characters
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

_PROMPTS = {"generic": _SYSTEM_GENERIC, "caselaw": _SYSTEM_CASELAW}


def _system_for(profile: str) -> str:
    return _PROMPTS.get(profile, _SYSTEM_GENERIC)


def _api_key() -> str | None:
    # `or None` so an empty string (e.g. an unset Docker env var) counts as absent.
    return os.environ.get("OPENROUTER_API_KEY") or None


def is_available() -> bool:
    return bool(_api_key())


def _model() -> str:
    # `or DEFAULT` so an empty env var falls back to the default model.
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
    """Return {'prompt': $/token, 'completion': $/token} for a model, or None."""
    if model in _pricing_cache:
        return _pricing_cache[model]
    result = None
    try:
        base = _base_url().rstrip("/")
        resp = requests.get(f"{base}/models", timeout=20)
        if resp.status_code == 200:
            for m in resp.json().get("data", []):
                if m.get("id") == model:
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


def estimate(markdown: str, profile: str = "generic") -> dict:
    """Estimate chunks, tokens and cost for cleaning `markdown`.

    Token counts are approximate (≈ 4 characters per token) — enough to gauge
    cost, not exact billing.
    """
    text = markdown.strip()
    chunks = _split_chunks(text) if text else []
    n = len(chunks)
    sys_tokens = len(_system_for(profile)) // 4
    content_tokens = sum(len(c) for c in chunks) // 4
    # Each chunk resends the system prompt + a little message overhead.
    input_tokens = content_tokens + (sys_tokens + 20) * n
    output_tokens = content_tokens  # cleanup preserves length ≈ input content
    model = _model()
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


def _split_chunks(text: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Split on blank lines, packing paragraphs up to `limit` characters."""
    paras = text.split("\n\n")
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 > limit:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        chunks.append(buf)
    return chunks or [text]


def _clean_chunk(chunk: str, api_key: str, model: str, base_url: str, system: str) -> str:
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
                {"role": "user", "content": f"Clean up this Markdown fragment:\n\n{chunk}"},
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
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"AI-opschoning gaf een onverwacht antwoord: {str(data)[:200]}") from e


def clean_markdown(markdown: str, profile: str = "generic") -> str:
    """Run the LLM cleanup pass. Raises ValueError on auth/config/API problems.

    profile: "generic" (default) or "caselaw" (court decisions / judgments).
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

    model = _model()
    base_url = _base_url()
    system = _system_for(profile)

    try:
        cleaned = [_clean_chunk(c, api_key, model, base_url, system) for c in _split_chunks(text)]
    except requests.exceptions.RequestException as e:
        raise ValueError(f"AI-opschoning mislukt (verbindingsfout): {e}") from e

    return "\n\n".join(c for c in cleaned if c).strip() + "\n"
