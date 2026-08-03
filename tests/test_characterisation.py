"""Karakteriseringstests: pinnen het gedrag van de tool vast.

Deze tests beschrijven geen wenselijk gedrag maar het *bestaande* gedrag van vóór
de herstructurering, zodat de herbouw aantoonbaar niets verandert. Ze raken geen
netwerk: alleen de pure functies (detectie, afleiding, chunking, reflow,
Formex-parsing, instellingen) en de HTTP-validatie.

De assertions zijn ongewijzigd overgenomen van de vorige structuur; alleen de
imports verwijzen nu naar de nieuwe modules. Slagen ze nog, dan is de conversie-
en opschoonlogica functioneel identiek.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Bronherkenning: detect_source precedentie
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    # EHRM-ECLI en HUDOC-links winnen van alles.
    ("ECLI:CE:ECHR:2021:0525JUD005817013", "hudoc"),
    ("https://hudoc.echr.coe.int/eng?i=001-210077", "hudoc"),
    ("001-210077", "hudoc"),
    # wetten.overheid.nl en BWB-nummers.
    ("https://wetten.overheid.nl/BWBR0040940/2021-07-01", "wetten"),
    ("BWBR0040940", "wetten"),
    # Nederlandse ECLI's en rechtspraak.nl-links.
    ("ECLI:NL:HR:2012:BQ9251", "rechtspraak"),
    ("https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2012:BQ9251", "rechtspraak"),
    # EU-ECLI, CELEX en overige input vallen door naar EUR-Lex (None).
    ("ECLI:EU:C:2025:645", None),
    ("32016R0679", None),
    ("https://eur-lex.europa.eu/eli/reg/2016/679/oj", None),
])
def test_detect_source(query, expected):
    from mdconv.sources import detect_source
    assert detect_source(query) == expected


def test_detect_source_hudoc_id_does_not_hijack_dutch_ecli():
    """Een ECLI met cijfergroepen mag niet als HUDOC-item-id worden gelezen."""
    from mdconv.sources import detect_source
    assert detect_source("ECLI:NL:RBAMS:2021:001-2345") == "rechtspraak"


# ---------------------------------------------------------------------------
# ELI -> CELEX afleiding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("eli,expected", [
    ("https://eur-lex.europa.eu/eli/reg/2016/679/oj", "32016R0679"),
    ("/eli/dir/2011/83", "32011L0083"),
    ("/eli/dec/2020/1", "32020D0001"),
    ("/eli/reco/2019/12", "32019H0012"),
])
def test_eli_to_celex(eli, expected):
    from mdconv.sources.eurlex import eli_to_celex
    assert eli_to_celex(eli) == expected


def test_eli_to_celex_unknown_type_is_none():
    from mdconv.sources.eurlex import eli_to_celex
    assert eli_to_celex("/eli/onbekend/2016/679") is None


def test_eli_to_celex_pads_number_to_four_digits():
    from mdconv.sources.eurlex import eli_to_celex
    assert eli_to_celex("/eli/reg/2016/7").endswith("R0007")


@pytest.mark.parametrize("text,expected", [
    ("32016R0679", "32016R0679"),
    ("https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32016R0679", "32016R0679"),
    ("62019CJ0311", "62019CJ0311"),
    ("geen celex hier", None),
])
def test_extract_celex(text, expected):
    from mdconv.sources.eurlex import extract_celex
    assert extract_celex(text) == expected


def test_fetch_multipart_concatenates_parts_in_document_order(monkeypatch):
    """Cellar geeft 300 voor documenten die uit meerdere HTML-onderdelen bestaan
    (bv. een wetgevingsvoorstel met een losse bijlage, elk als eigen
    manifestatie) — niet alleen bij een taalprobleem, zoals eerder aangenomen.
    Elk onderdeel moet dan met Accept: text/html opgehaald worden (de
    manifestatie-URL heeft text/html als resource-mimetype; xhtml+xml geeft
    daar 406) en in documentvolgorde samengevoegd.
    """
    from mdconv.sources import eurlex

    choices_html = (
        '<html><body><ul><li title="manifestation">cellar:abc'
        '<ul><li title="item"><a href="http://publications.europa.eu/resource/cellar/abc/DOC_1">1</a></li>'
        '<li title="item"><a href="http://publications.europa.eu/resource/cellar/abc/DOC_2">2</a></li>'
        '</ul></li></ul></body></html>'
    )

    fetched_urls = []

    class FakeResp:
        def __init__(self, url):
            self.status_code = 200
            self.apparent_encoding = "utf-8"
            self.text = f"<html><body><p>Inhoud van {url.rsplit('/', 1)[-1]}</p></body></html>"

    def fake_get(url, headers=None, timeout=None):
        fetched_urls.append((url, headers["Accept"]))
        return FakeResp(url)

    monkeypatch.setattr(eurlex.net, "documents", lambda: type("S", (), {"get": staticmethod(fake_get)})())

    markdown = eurlex._fetch_multipart(choices_html, "NL", "CELEX:test")
    unescaped = markdown.replace("\\_", "_")  # markdownify escaapt underscores

    assert [u for u, _ in fetched_urls] == [
        "http://publications.europa.eu/resource/cellar/abc/DOC_1",
        "http://publications.europa.eu/resource/cellar/abc/DOC_2",
    ]
    assert all(accept == "text/html" for _, accept in fetched_urls)
    assert unescaped.index("DOC_1") < unescaped.index("DOC_2")
    assert "\n\n---\n\n" in markdown


def test_fetch_multipart_without_doc_links_reports_language_problem():
    from mdconv.sources.eurlex import _fetch_multipart
    from mdconv.errors import ConversionError
    with pytest.raises(ConversionError, match="Probeer een andere taal"):
        _fetch_multipart("<html><body>geen manifestaties hier</body></html>", "NL", "CELEX:x")


# ---------------------------------------------------------------------------
# Chunking: elke chunk moet onder de limiet blijven, ook zonder witregels
# ---------------------------------------------------------------------------

def test_split_chunks_respects_limit_with_paragraphs():
    from mdconv.cleanup.chunking import split
    text = "\n\n".join(["alinea " * 50] * 40)
    chunks = split(text, limit=1000)
    assert chunks
    assert all(len(c) <= 1000 for c in chunks)


def test_split_chunks_respects_limit_without_any_blank_lines():
    """Een PDF-conversie kan één doorlopend blok zijn; dat moet alsnog splitsen."""
    from mdconv.cleanup.chunking import split
    text = "woord " * 5000  # geen enkele witregel
    chunks = split(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_split_chunks_hard_slices_a_single_oversized_word():
    from mdconv.cleanup.chunking import split
    chunks = split("x" * 2500, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == "x" * 2500


def test_split_chunks_preserves_all_content():
    from mdconv.cleanup.chunking import split
    text = "\n\n".join(f"alinea {i} " + "tekst " * 30 for i in range(30))
    chunks = split(text, limit=800)
    assert "".join(text.split()) == "".join("".join(c.split()) for c in chunks)


def test_split_chunks_default_limit_follows_configured_chunk_tokens():
    from mdconv.cleanup import config
    assert config.get_chunk_tokens() == config.DEFAULT_CHUNK_TOKENS


def test_obsidian_profile_is_never_chunked():
    from mdconv.cleanup import chunking
    long_text = "\n\n".join(["alinea " * 200] * 200)
    assert len(chunking.chunks_for(long_text, "obsidian")) == 1
    assert len(chunking.chunks_for(long_text, "caselaw")) > 1


# ---------------------------------------------------------------------------
# PDF-reflow heuristiek
# ---------------------------------------------------------------------------

def test_reflow_joins_soft_wrapped_sentence():
    from mdconv.sources.files import reflow
    text = ("Dit is een lange regel die tegen de rechtermarge aanloopt en daarom\n"
            "doorloopt op de volgende regel binnen dezelfde alinea.")
    assert "\n" not in reflow(text).strip()


def test_reflow_keeps_paragraph_breaks():
    from mdconv.sources.files import reflow
    text = "Eerste alinea met voldoende lengte om als volle regel te gelden.\n\nTweede alinea."
    assert "\n\n" in reflow(text)


def test_reflow_never_merges_structural_lines():
    from mdconv.sources.files import reflow
    text = ("## Een kop die lang genoeg is om boven de drempel uit te komen ja\n"
            "- lijstitem dat lang genoeg is om boven de drempel uit te komen ja\n"
            "- tweede lijstitem dat ook lang genoeg is om mee te tellen hierin")
    assert reflow(text).count("\n") == 2


def test_reflow_glues_hyphenated_word_split():
    from mdconv.sources.files import reflow
    text = ("Dit is een regel die eindigt met een afgebroken woord, namelijk voor-\n"
            "beeld, en gaat daarna verder.")
    assert "voorbeeld" in reflow(text)


def test_reflow_strips_cid_artefacts():
    from mdconv.sources.files import reflow
    assert "(cid:" not in reflow("tekst (cid:123) meer tekst")


# ---------------------------------------------------------------------------
# Formex-parser
# ---------------------------------------------------------------------------

FORMEX_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<ACT>
  <TITLE><TI>VERORDENING (EU) 2016/679</TI><STI>inzake gegevensbescherming</STI></TITLE>
  <PREAMBLE>
    <VISA>Gezien het Verdrag betreffende de werking van de Europese Unie,</VISA>
    <GR.CONSID>
      <CONSID><NP><NO.P>(1)</NO.P><TXT>De bescherming van natuurlijke personen is een grondrecht.</TXT></NP></CONSID>
      <CONSID><NP><NO.P>(2)</NO.P><TXT>Dit heeft gevolgen voor de interne markt.</TXT></NP></CONSID>
    </GR.CONSID>
  </PREAMBLE>
  <ENACTING.TERMS>
    <DIVISION>
      <TITLE><TI>HOOFDSTUK I</TI><STI>Algemene bepalingen</STI></TITLE>
      <ARTICLE>
        <TI.ART>Artikel 1</TI.ART><STI.ART>Voorwerp</STI.ART>
        <PARAG><NO.PARAG>1.</NO.PARAG><ALINEA><P>Deze verordening stelt regels vast.</P></ALINEA></PARAG>
        <PARAG><NO.PARAG>2.</NO.PARAG>
          <ALINEA>
            <LIST>
              <ITEM><NP><NO.P>a)</NO.P><TXT>eerste onderdeel</TXT></NP></ITEM>
              <ITEM><NP><NO.P>b)</NO.P><TXT>tweede onderdeel</TXT></NP></ITEM>
            </LIST>
          </ALINEA>
        </PARAG>
      </ARTICLE>
    </DIVISION>
  </ENACTING.TERMS>
</ACT>
"""


def test_formex_produces_expected_structure():
    from mdconv.sources.formex import convert_formex
    md = convert_formex(FORMEX_SAMPLE)
    assert "# VERORDENING (EU) 2016/679" in md
    assert "## inzake gegevensbescherming" in md
    assert "(1) De bescherming van natuurlijke personen is een grondrecht." in md
    assert "### Artikel 1 — Voorwerp" in md
    assert "1.  Deze verordening stelt regels vast." in md
    assert "- a) eerste onderdeel" in md
    assert "- b) tweede onderdeel" in md


def test_formex_recitals_are_not_headings():
    """Genummerde overwegingen blijven alinea's (uitdrukkelijke wens)."""
    from mdconv.sources.formex import convert_formex
    for line in convert_formex(FORMEX_SAMPLE).splitlines():
        if line.startswith("#"):
            assert "(1)" not in line and "(2)" not in line


def test_formex_inline_formatting_and_footnotes():
    from mdconv.sources.formex import convert_formex
    xml = (b'<ACT><ENACTING.TERMS><P>Gewoon <HT TYPE="BOLD">vet</HT> en '
           b'<HT TYPE="ITALIC">cursief</HT>.<NOTE NOTE.ID="1">De voetnoot.</NOTE></P>'
           b'</ENACTING.TERMS></ACT>')
    md = convert_formex(xml)
    assert "**vet**" in md
    assert "*cursief*" in md
    assert "[^1]" in md
    assert "[^1]: De voetnoot." in md


def test_formex_table_becomes_markdown_table():
    from mdconv.sources.formex import convert_formex
    xml = (b"<ACT><ENACTING.TERMS><TBL><CORPUS>"
           b"<ROW><CELL>Kop A</CELL><CELL>Kop B</CELL></ROW>"
           b"<ROW><CELL>a1</CELL><CELL>b1</CELL></ROW>"
           b"</CORPUS></TBL></ENACTING.TERMS></ACT>")
    md = convert_formex(xml)
    assert "| Kop A | Kop B |" in md
    assert "| --- | --- |" in md
    assert "| a1 | b1 |" in md


def test_formex_falls_back_to_plain_text_when_structure_is_unknown():
    from mdconv.sources.formex import convert_formex
    xml = (b"<ONBEKEND><RARE>Dit is losse tekst die toch zichtbaar moet blijven "
           b"ook al kent de parser deze structuur niet.</RARE></ONBEKEND>")
    assert "losse tekst die toch zichtbaar moet blijven" in convert_formex(xml)


def test_formex_footnotes_are_isolated_per_conversion():
    """Voetnoten mogen niet lekken tussen conversies."""
    from mdconv.sources.formex import convert_formex
    convert_formex(b'<ACT><P>A<NOTE NOTE.ID="1">nootA</NOTE></P></ACT>')
    md_b = convert_formex(b'<ACT><P>B<NOTE NOTE.ID="1">nootB</NOTE></P></ACT>')
    assert "nootB" in md_b
    assert "nootA" not in md_b


def test_formex_footnotes_survive_concurrent_conversions():
    """Twee documenten tegelijk omzetten mag hun voetnoten niet vermengen.

    Dit faalde in de vorige opzet: `_CURRENT_CTX` was een module-level stack, dus
    gelijktijdige conversies pikten elkaars voetnoten op. Flask draait lokaal met
    threads en documenten worden parallel geüpload, dus dat was echt bereikbaar.
    """
    import re
    import threading
    from mdconv.sources.formex import convert_formex

    def document(tag: str, n: int = 12) -> bytes:
        body = "".join(
            f'<P>{tag}{i} <NOTE NOTE.ID="{tag}{i}">noot-{tag}{i}</NOTE></P>' for i in range(n)
        )
        return f"<ACT><ENACTING.TERMS>{body}</ENACTING.TERMS></ACT>".encode()

    problems: list[str] = []

    def worker(tag: str):
        for _ in range(30):
            md = convert_formex(document(tag))
            foreign = [d for d in re.findall(r"\[\^(\w+)\]:", md) if not d.startswith(tag)]
            missing = [f"{tag}{i}" for i in range(12) if f"[^{tag}{i}]:" not in md]
            if foreign or missing:
                problems.append(f"{tag}: vreemd={foreign[:3]} ontbreekt={missing[:3]}")
                return

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C", "D")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not problems, "voetnoten lekten tussen gelijktijdige conversies: " + "; ".join(problems)


# ---------------------------------------------------------------------------
# Opschoon-profielen en fence-stripping
# ---------------------------------------------------------------------------

def test_profiles_exist_and_obsidian_runs_unsplit():
    from mdconv.cleanup import config, prompts
    assert set(prompts.DEFAULTS) == {"generic", "caselaw", "obsidian"}
    assert "obsidian" in config.NO_CHUNK_PROFILES


def test_caselaw_prompt_forbids_promoting_paragraph_numbers():
    from mdconv.cleanup.config import get_prompt
    prompt = get_prompt("caselaw")
    assert "never turn" in prompt.lower() or "never promote" in prompt.lower()
    assert "##" in prompt


def test_default_model_keeps_its_leading_tilde():
    from mdconv.cleanup.config import DEFAULT_MODEL
    assert DEFAULT_MODEL.startswith("~"), "de tilde is de OpenRouter latest-alias"


def test_strip_markdown_fence():
    from mdconv.cleanup.openrouter import strip_markdown_fence
    assert strip_markdown_fence("```markdown\n# Titel\n\ntekst\n```") == "# Titel\n\ntekst"
    assert strip_markdown_fence("# Titel\n\ntekst") == "# Titel\n\ntekst"


@pytest.mark.parametrize("piece_size,expected", [
    (1000, "# Titel\n\nEen langere alinea met genoeg tekst voor de holdback-buffer."),
    (1, "# Titel\n\nEen langere alinea met genoeg tekst voor de holdback-buffer."),  # per karakter
])
def test_strip_fence_stream_matches_the_non_streaming_version(piece_size, expected):
    """De streaming-fence-stripper moet, ongeacht hoe klein de binnenkomende
    stukjes zijn (tot en met één losse letter), hetzelfde resultaat geven als
    de niet-streaming versie — anders lekt het codeblok van het obsidian-
    profiel even mee in de live-weergave."""
    from mdconv.cleanup.openrouter import strip_fence_stream

    raw = "```markdown\n" + expected + "\n```"

    def pieces():
        for i in range(0, len(raw), piece_size):
            yield raw[i:i + piece_size]

    assert "".join(strip_fence_stream(pieces())) == expected


def test_strip_fence_stream_without_a_fence_passes_text_through():
    from mdconv.cleanup.openrouter import strip_fence_stream
    assert "".join(strip_fence_stream(iter(["gewone tekst zonder fence"]))) == "gewone tekst zonder fence"


def test_base_url_strips_chat_completions_suffix(monkeypatch):
    from mdconv.cleanup import config
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
    assert config.base_url() == "https://openrouter.ai/api/v1"


def test_base_url_empty_env_falls_back_to_default(monkeypatch):
    from mdconv.cleanup import config
    monkeypatch.setenv("OPENROUTER_BASE_URL", "")
    assert config.base_url() == config.DEFAULT_BASE_URL


def test_estimate_counts_input_without_network(monkeypatch):
    import mdconv.cleanup as cleanup
    from mdconv.cleanup import config
    monkeypatch.setattr(config, "is_available", lambda: False)
    est = cleanup.estimate("regel\n\n" * 100, profile="generic")
    assert est["chunks"] == 1
    assert est["input_tokens"] > 0
    assert est["cost_usd"] is None


def test_estimate_obsidian_expects_more_output_than_input(monkeypatch):
    import mdconv.cleanup as cleanup
    from mdconv.cleanup import config
    monkeypatch.setattr(config, "is_available", lambda: False)
    text = "tekst " * 2000
    generic = cleanup.estimate(text, profile="generic")
    obsidian = cleanup.estimate(text, profile="obsidian")
    assert obsidian["output_tokens"] > generic["output_tokens"]


def test_pricing_lookup_matches_model_id_without_nitro_suffix(monkeypatch):
    """:nitro bestaat niet als los item in de OpenRouter-catalogus."""
    from mdconv.cleanup import openrouter

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "anthropic/claude-haiku-4.5",
                              "pricing": {"prompt": "0.000001", "completion": "0.000005"}}]}

    class FakeSession:
        @staticmethod
        def get(*_a, **_k):
            return FakeResp()

    monkeypatch.setattr(openrouter.net, "llm", lambda: FakeSession())
    openrouter.clear_pricing_cache()
    pricing = openrouter.get_pricing("anthropic/claude-haiku-4.5:nitro")
    assert pricing is not None and pricing["completion"] == 0.000005
    openrouter.clear_pricing_cache()


def test_clean_chunk_rejects_a_truncated_response(monkeypatch):
    """finish_reason='length' betekent dat het model afkapte vóórdat de tekst
    klaar was. Zonder deze check kwam er stilletjes afgekapte tekst terug —
    bij het obsidian-profiel (dat nooit chunkt) verdween daardoor het laatste
    deel van een groot document zonder enige melding."""
    from mdconv.cleanup import openrouter
    from mdconv.errors import ConversionError

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "afgekapte tekst..."},
                                  "finish_reason": "length"}]}

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeResp())})())

    with pytest.raises(ConversionError, match="afgekapt"):
        openrouter.clean_chunk("tekst", model="x", system="y", profile="generic")

    with pytest.raises(ConversionError, match="Opmaken voor Obsidian"):
        openrouter.clean_chunk("tekst", model="x", system="y", profile="obsidian")


def test_stream_chunk_rejects_a_truncated_response(monkeypatch):
    from mdconv.cleanup import openrouter
    from mdconv.errors import ConversionError

    class FakeStreamResp:
        status_code = 200

        @staticmethod
        def iter_lines(decode_unicode=True):
            return iter([
                'data: {"choices":[{"delta":{"content":"Dit is een stuk"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
                "data: [DONE]",
            ])

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeStreamResp())})())

    with pytest.raises(ConversionError, match="afgekapt"):
        list(openrouter.stream_chunk("tekst", model="x", system="y", profile="generic"))


# ---------------------------------------------------------------------------
# Instellingen: leeg/ongeldig wist terug naar de standaardwaarde
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """Laat de instellingen naar een tijdelijke map schrijven i.p.v. .deploy-state."""
    from mdconv.cleanup import config
    from mdconv.state import StateFile

    monkeypatch.setattr("mdconv.state.STATE_DIR", str(tmp_path))
    store = StateFile("settings.json")
    monkeypatch.setattr(config, "_store", store)
    return config


def test_settings_defaults_when_nothing_stored(isolated_settings):
    cfg = isolated_settings
    assert cfg.get_chunk_tokens() == cfg.DEFAULT_CHUNK_TOKENS
    assert cfg.get_model_choices() == cfg.DEFAULT_MODEL_CHOICES
    from mdconv.cleanup import prompts
    assert cfg.get_prompt("generic") == prompts.DEFAULTS["generic"]


def test_settings_roundtrip_and_take_effect_immediately(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({"chunk_tokens": 30000, "prompts": {"generic": "eigen prompt"}})
    assert cfg.get_chunk_tokens() == 30000
    assert cfg.get_prompt("generic") == "eigen prompt"


def test_settings_empty_value_clears_back_to_default(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({"chunk_tokens": 30000, "models": [{"id": "x/y", "label": "X"}],
                         "prompts": {"generic": "eigen"}})
    cfg.update_settings({"chunk_tokens": None, "models": [], "prompts": {"generic": ""}})
    from mdconv.cleanup import prompts
    assert cfg.get_chunk_tokens() == cfg.DEFAULT_CHUNK_TOKENS
    assert cfg.get_model_choices() == cfg.DEFAULT_MODEL_CHOICES
    assert cfg.get_prompt("generic") == prompts.DEFAULTS["generic"]


def test_settings_out_of_range_chunk_size_is_rejected(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({"chunk_tokens": 10 ** 9})
    assert cfg.get_chunk_tokens() == cfg.DEFAULT_CHUNK_TOKENS
    cfg.update_settings({"chunk_tokens": 1})
    assert cfg.get_chunk_tokens() == cfg.DEFAULT_CHUNK_TOKENS


def test_settings_only_stores_changed_keys(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({"chunk_tokens": 40000})
    stored = json.loads(open(cfg._store.path, encoding="utf-8").read())
    assert list(stored) == ["chunk_tokens"]


def test_settings_payload_exposes_defaults_for_reset(isolated_settings):
    payload = isolated_settings.settings_payload()
    for key in ("models", "chunk_tokens", "prompts"):
        assert key in payload and key in payload["defaults"]
    assert payload["defaults"]["min_chunk_tokens"] < payload["defaults"]["max_chunk_tokens"]


def test_settings_change_is_picked_up_without_restart(isolated_settings):
    """De mtime-cache mag een wijziging niet blijven verbergen."""
    cfg = isolated_settings
    assert cfg.get_chunk_tokens() == cfg.DEFAULT_CHUNK_TOKENS  # vult de cache
    cfg._store.write({"chunk_tokens": 12345})                  # schrijf er buitenom
    assert cfg.get_chunk_tokens() == 12345


def test_model_override_must_be_in_the_configured_list(isolated_settings):
    cfg = isolated_settings
    assert cfg.resolve_model("deepseek/deepseek-v4-flash:nitro") == \
        "deepseek/deepseek-v4-flash:nitro"
    assert cfg.resolve_model("verzonnen/model") == cfg.DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Classificatie, bestandsnamen, Formex-detectie
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,expected", [
    ("Rechtspraak.nl • ECLI:NL:HR:2012:BQ9251", "caselaw"),
    ("HUDOC • 001-210077", "caselaw"),
    ("EUR-Lex (Cellar) • ECLI:EU:C:2020:1041 • NL", "caselaw"),
    ("EUR-Lex (Cellar) • CELEX:62019CJ0311 • NL", "caselaw"),
    ("EUR-Lex (Cellar) • CELEX:32016R0679 • NL", "document"),
    ("MarkItDown • rapport.pdf", "document"),
    ("pdf-inspector • rapport.pdf", "document"),
])
def test_kind_for_source(source, expected):
    from mdconv.sources import kind_for_source
    assert kind_for_source(source) == expected


@pytest.mark.parametrize("url,ct,expected", [
    ("https://x.nl/pad/rapport.pdf", "application/pdf", "rapport.pdf"),
    ("https://x.nl/download", "application/pdf", "download.pdf"),
    ("https://x.nl/bestand%20met%20ruimte.docx", "", "bestand met ruimte.docx"),
])
def test_filename_from_url(url, ct, expected):
    from mdconv.api import _filename_from_url
    assert _filename_from_url(url, ct) == expected


def test_looks_like_formex():
    from mdconv.sources import looks_like_formex
    assert looks_like_formex(FORMEX_SAMPLE)
    assert not looks_like_formex(b"<html><body>gewoon html</body></html>")


def test_valid_profiles_are_the_three_known_ones():
    import mdconv.cleanup as cleanup
    assert set(cleanup.PROFILES) == {"generic", "caselaw", "obsidian"}


# ---------------------------------------------------------------------------
# HTTP-laag (zonder netwerk): validatie en foutmeldingen
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from mdconv import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_convert_link_requires_a_query(client):
    r = client.post("/api/convert/link", json={"query": "  "})
    assert r.status_code == 400
    assert "CELEX" in r.get_json()["error"]


def test_convert_file_url_rejects_non_http_scheme(client):
    r = client.post("/api/convert/file-url", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400
    assert "geldige URL" in r.get_json()["error"]


def test_convert_file_without_file_is_rejected(client):
    r = client.post("/api/convert/file", data={})
    assert r.status_code == 400
    assert "Geen bestand" in r.get_json()["error"]


def test_clean_rejects_empty_markdown(client):
    r = client.post("/api/clean", json={"markdown": "   ", "profile": "generic"})
    assert r.status_code == 400
    assert "Niets om op te schonen" in r.get_json()["error"]


def test_estimate_falls_back_to_generic_for_unknown_profile(client, monkeypatch):
    from mdconv.cleanup import config
    monkeypatch.setattr(config, "is_available", lambda: False)
    r = client.post("/api/estimate", json={"markdown": "tekst", "profile": "onzin"})
    assert r.status_code == 200


def test_config_reports_models_and_availability(client):
    data = client.get("/api/config").get_json()
    assert "llm_available" in data
    assert isinstance(data["models"], list) and data["models"]
    assert all("id" in m and "label" in m for m in data["models"])


def test_download_sanitises_the_filename(client):
    r = client.post("/api/download", json={"markdown": "# x", "filename": "../../etc/passwd"})
    assert r.status_code == 200
    assert "/" not in r.headers["Content-Disposition"].split("filename=")[-1].strip('"')


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Markdown converter" in r.data


def test_settings_endpoints_roundtrip(client):
    payload = client.get("/api/settings").get_json()
    assert payload["chunk_tokens"] >= payload["defaults"]["min_chunk_tokens"]
    # Een lege payload mag niets stukmaken.
    assert client.post("/api/settings", json={}).status_code == 200


# ---------------------------------------------------------------------------
# Bestandsconversie
# ---------------------------------------------------------------------------

def test_pdf_conversion_prefers_pdf_inspector():
    """Een tekst-PDF gaat via pdf-inspector, niet via MarkItDown."""
    from mdconv.sources.files import convert
    markdown, engine = convert(_minimal_text_pdf(), "test.pdf")
    assert engine == "pdf-inspector"
    assert "Hallo" in markdown


def test_non_pdf_still_uses_markitdown():
    from mdconv.sources.files import convert
    markdown, engine = convert(b"# Kop\n\ntekst\n", "notitie.md")
    assert engine == "MarkItDown"


def test_formex_upload_uses_the_structural_parser():
    from mdconv.sources import from_file
    doc = from_file(FORMEX_SAMPLE, "avg.xml")
    assert doc.source.startswith("Formex XML")
    assert "# VERORDENING (EU) 2016/679" in doc.markdown


def test_pdf_upload_reports_the_engine_in_the_source():
    from mdconv.sources import from_file
    doc = from_file(_minimal_text_pdf(), "rapport.pdf")
    assert doc.source == "pdf-inspector • rapport.pdf"
    assert doc.kind == "document"


# ---------------------------------------------------------------------------
# Front-end: één regel waar de UI stilletjes op stukliep
# ---------------------------------------------------------------------------

def test_css_forces_the_hidden_attribute_to_win():
    """`[hidden]` moet een expliciete display uit een class-regel verslaan.

    De JS regelt zichtbaarheid via het hidden-attribuut, maar de UA-stijl
    ([hidden] → display:none) heeft de laagste specificiteit. Daardoor bleef het
    Obsidian-vinkje zichtbaar bij gewone documenten: `.checkbox` zet
    `display: inline-flex`. Zonder deze regel komt die bug terug.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = open(os.path.join(root, "static", "app.css"), encoding="utf-8").read()
    assert "[hidden]" in css and "display: none !important" in css


def test_editor_mirror_and_textarea_share_one_font_and_padding():
    """De regelnummer-spiegel moet exact meten wat de textarea rendert.

    Font en padding staan daarom in variabelen die beide elementen gebruiken;
    wijkt dat uiteen, dan lopen de regelnummers scheef bij gewrapte regels.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = open(os.path.join(root, "static", "app.css"), encoding="utf-8").read()
    for block in (".editor textarea {", ".line-mirror {"):
        start = css.index(block)
        body = css[start:css.index("}", start)]
        assert "var(--editor-font)" in body, f"{block} gebruikt niet --editor-font"
        assert "var(--editor-padding)" in body, f"{block} gebruikt niet --editor-padding"


def _minimal_text_pdf() -> bytes:
    """Een handgeschreven, geldige PDF met één tekstregel."""
    body = (
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"5 0 obj<</Length 44>>stream\n"
        b"BT /F1 24 Tf 72 700 Td (Hallo wereld) Tj ET\n"
        b"endstream endobj\n"
    )
    out = b"%PDF-1.4\n"
    offsets = []
    for chunk in body.split(b"endobj\n")[:-1]:
        offsets.append(len(out))
        out += chunk + b"endobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(offsets) + 1, start))
    return out
