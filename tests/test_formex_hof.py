"""De Formex-route voor arresten en beschikkingen van het Hof en het Gerecht.

Geen netwerk: `net.documents` wordt vervangen, net als bij `_fake_cellar` in
`test_characterisation.py`. De fixture is een klein, met de hand geschreven arrest
met de constructies die in het corpus (143 documenten, gemeten op 21 september 2026)
de raw-vorm bepalen: een titel met `HT TYPE="UC"`, een procestaalnoot, partijen in
vet, een geciteerd lid, een geciteerd genummerd punt, een opsomming, het dictum en de
ondertekeningsgroep van het Gerecht.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

import pytest

from mdconv.errors import ConversionError
from mdconv.sources import eurlex, formex_hof, from_link

NBSP = " "

ARREST = """<?xml version="1.0" encoding="UTF-8"?>
<JUDGMENT>
<BIB.JUDGMENT><REF.CASE FILE="n.a."><NO.CASE>C-1/26</NO.CASE></REF.CASE>
<NO.CELEX>62026CJ0001</NO.CELEX><NO.ECLI ECLI="ECLI:EU:C:2026:1">EU:C:2026:1</NO.ECLI>
<NO.SEQ>0001</NO.SEQ><AUTHOR>CJ</AUTHOR></BIB.JUDGMENT>
<CURR.TITLE><PAGE.HEADER><P>Arrest van 1. 1. 2026 – Zaak C-1/26</P><P>Voorbeeld</P></PAGE.HEADER></CURR.TITLE>
<TITLE><TI><P><HT TYPE="UC">Arrest van het Hof</HT> (Vierde kamer)</P>
<P><DATE ISO="20260109">9 januari 2026</DATE><NOTE NOTE.ID="E0001" NUMBERING="STAR" TYPE="FOOTNOTE"><P>Procestaal: Engels.</P></NOTE></P></TI></TITLE>
<INTERMEDIATE><INDEX IDX.CLOSE="”" SEPARATOR="&#160;– " IDX.OPEN="„"><KEYWORD>Prejudiciële verwijzing</KEYWORD><KEYWORD>Persoonsgegevens</KEYWORD></INDEX></INTERMEDIATE>
<JUDGMENT.INIT><P>In zaak C‑1/26,</P></JUDGMENT.INIT>
<PARTIES><PLAINTIFS><P><HT TYPE="BOLD">Alfa B.V.,</HT> vertegenwoordigd door X,</P></PLAINTIFS>
<AGAINST>tegen</AGAINST>
<DEFENDANTS><P><HT TYPE="BOLD">Beta N.V.,</HT></P><P>in tegenwoordigheid van:</P><P><HT TYPE="BOLD">Gamma,</HT></P></DEFENDANTS></PARTIES>
<P>wijst</P>
<PREAMBLE><PREAMBLE.INIT><P>HET HOF (Vierde kamer),</P></PREAMBLE.INIT>
<GR.VISA><VISA>gezien de stukken,</VISA></GR.VISA>
<GR.CONSID><CONSID><P>gelet op de opmerkingen van:</P><LIST TYPE="NDASH"><ITEM><P>Alfa, vertegenwoordigd door X,</P></ITEM></LIST></CONSID></GR.CONSID>
<PREAMBLE.FINAL>het navolgende</PREAMBLE.FINAL></PREAMBLE>
<CONTENTS.JUDGMENT>
<GR.SEQ LEVEL="1"><TITLE><TI><P><HT TYPE="BOLD">Arrest</HT></P></TI></TITLE>
<NP.ECR IDENTIFIER="NP0001"><NO.P>1</NO.P><TXT>Het verzoek betreft de uitlegging van artikel 4:</TXT>
<P><QUOT.S LEVEL="1"><PARAG IDENTIFIER="004.001"><NO.PARAG><QUOT.START CODE="201E" REF.END="QE1" ID="QS1"/>1.</NO.PARAG><ALINEA>Eerste lid.</ALINEA></PARAG>
<PARAG IDENTIFIER="004.002"><NO.PARAG>2.</NO.PARAG><ALINEA>Tweede lid.<QUOT.END REF.START="QS1" CODE="201D" ID="QE1"/></ALINEA></PARAG></QUOT.S></P></NP.ECR>
<NP.ECR IDENTIFIER="NP0002"><NO.P>2</NO.P><TXT>Bijlage II somt op, waaronder:</TXT>
<P><QUOT.S LEVEL="1"><NP><NO.P><QUOT.START CODE="201E" REF.END="QE2" ID="QS2"/>10.</NO.P><TXT>Infrastructuur</TXT></NP>
<NP><NO.P>12.</NO.P><TXT>Wijzigingen in projecten.<QUOT.END REF.START="QS2" CODE="201D" ID="QE2"/></TXT></NP></QUOT.S></P></NP.ECR>
</GR.SEQ>
<GR.SEQ LEVEL="1"><TITLE><TI><P>Beantwoording van de vragen</P></TI></TITLE>
<NP.ECR IDENTIFIER="NP0003"><NO.P>3</NO.P><TXT>Het Hof overweegt als volgt.</TXT></NP.ECR>
<NP.ECR IDENTIFIER="NP0004"><NO.P>4</NO.P><TXT>Bedrag van <FT TYPE="NUMBER">10000000</FT> euro, zaak 1<REF.DOC.ECR>18/77, Jurispr.</REF.DOC.ECR></TXT></NP.ECR>
</GR.SEQ>
<GR.SEQ LEVEL="1"><TITLE><TI><P>Kosten</P></TI></TITLE>
<NP.ECR IDENTIFIER="NP0005"><NO.P>5</NO.P><TXT>De kosten blijven voor rekening van partijen.</TXT></NP.ECR></GR.SEQ>
<JURISDICTION><INTRO>Het Hof (Vierde kamer) verklaart voor recht:</INTRO>
<LIST TYPE="ARAB"><ITEM><NP><NO.P><HT TYPE="BOLD">1)</HT></NO.P><TXT><HT TYPE="BOLD">Artikel 4 moet aldus worden uitgelegd.</HT></TXT></NP></ITEM>
<ITEM><NP><NO.P><HT TYPE="BOLD">2)</HT></NO.P><TXT><HT TYPE="BOLD">Partijen dragen hun eigen kosten.</HT></TXT></NP></ITEM></LIST></JURISDICTION>
</CONTENTS.JUDGMENT>
<SIGNATURE.CASE><SIGNATORY><P>Rechter Een</P></SIGNATORY><SIGNATORY><P>Rechter Twee</P></SIGNATORY>
<P>Uitgesproken ter openbare terechtzitting te Luxemburg op <DATE ISO="20260109">9 januari 2026</DATE>.</P>
<SIGNATORY><P>ondertekeningen</P></SIGNATORY></SIGNATURE.CASE>
</JUDGMENT>
"""


def zipje(*onderdelen: tuple[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for naam, inhoud in onderdelen:
            zf.writestr(naam, inhoud)
    return buf.getvalue()


def arrest_zip(xml: str = ARREST) -> bytes:
    return zipje(("ECR_62026CJ0001_NL_01.xml", xml.encode("utf-8")))


def test_de_rawvorm_is_die_van_het_profiel():
    markdown, meta = formex_hof.omzetten(arrest_zip(), "62026CJ0001")
    regels = markdown.split("\n\n")

    # Titel in kapitalen (HT UC), de datum met een echte noot, geen '#'.
    assert regels[0] == "ARREST VAN HET HOF (Vierde kamer)"
    assert regels[1] == f"9 januari 2026[^procestaal]"
    assert not any(r.startswith("#") for r in regels)
    # Trefwoorden als één regel met het scheidingsteken en de aanhalingstekens van de bron.
    assert regels[2] == f"„Prejudiciële verwijzing{NBSP}– Persoonsgegevens”"
    # De kale regel `Arrest` markeert het begin van het lichaam; secties zijn kale regels.
    assert "Arrest" in regels
    assert "Beantwoording van de vragen" in regels
    # Een overweging: nummer, punt, één gewone spatie.
    assert "3. Het Hof overweegt als volgt." in regels
    # Geciteerd lid: nummer, punt en DRIE harde spaties; het eerste met aanhalingsteken.
    assert f"„1.{NBSP * 3}Eerste lid." in regels
    assert f"2.{NBSP * 3}Tweede lid.”" in regels
    # Een geciteerd genummerd punt is geen rechtsoverweging: dezelfde drie harde spaties.
    assert f"„10.{NBSP * 3}Infrastructuur" in regels
    assert f"12.{NBSP * 3}Wijzigingen in projecten.”" in regels
    assert not any(r.startswith(("10. ", "12. ")) for r in regels)
    # Een opsomming houdt het teken van de bron.
    assert "- Alfa, vertegenwoordigd door X," in regels
    # Het dictum: één inleidingsregel, dan de punten als alinea's - geen tabel.
    assert "Het Hof (Vierde kamer) verklaart voor recht:" in regels
    assert "1) Artikel 4 moet aldus worden uitgelegd." in regels
    assert not any(r.lstrip().startswith("|") for r in regels)
    # De ondertekeningsgroep is één alinea, met enkele regeleinden: zo valt hij in de
    # kennisbank als geheel buiten `outcome`.
    assert ("Rechter Een\nRechter Twee\nUitgesproken ter openbare terechtzitting te "
            "Luxemburg op 9 januari 2026.\nondertekeningen") in regels
    # De procestaalnoot is een echte definitie.
    assert markdown.rstrip().endswith("[^procestaal]: Procestaal: Engels.")


def test_getal_en_element_midden_in_een_woord():
    markdown, _ = formex_hof.omzetten(arrest_zip())
    # `FT NUMBER` krijgt de groepering van het Publicatieblad, met harde spaties ...
    assert f"10{NBSP}000{NBSP}000 euro" in markdown
    # ... en een zaaknummer dat midden door een element loopt blijft één getal.
    assert "zaak 118/77, Jurispr." in markdown


def test_de_metadata_komt_uit_de_bron_zelf():
    _, meta = formex_hof.omzetten(arrest_zip())
    assert meta["celex"] == "62026CJ0001"
    assert meta["ecli"] == "ECLI:EU:C:2026:1"
    assert meta["zaaknummers"] == ["C-1/26"]
    assert meta["titelregels"] == ["ARREST VAN HET HOF (Vierde kamer)", "9 januari 2026"]
    assert meta["datum"] == "2026-01-09"
    assert meta["procestaal"] == "Engels"
    # De partijen volgen uit de opmaak: vet is een partij, `in tegenwoordigheid van` is
    # een scheiding, en wat erna komt zijn de overige partijen.
    assert meta["partijen"] == ["Alfa B.V.", "Beta N.V."]
    assert meta["overige_partijen"] == ["Gamma"]
    assert meta["dictum_inleiding"] == "Het Hof (Vierde kamer) verklaart voor recht:"
    assert meta["secties"] == ["Arrest", "Beantwoording van de vragen", "Kosten"]
    # Het paginakop gaat naar de herkomst en niet stil verloren.
    assert meta["paginakop"] == ["Arrest van 1. 1. 2026 – Zaak C-1/26", "Voorbeeld"]


def test_partijrol_bij_meerdere_partijgroepen():
    # De vorm van C-413/23 P (ECLI:EU:C:2025:645): in hogere voorziening staat een
    # interveniënt tussen rekwirant en verweerder. De verweerder is een hoofdpartij;
    # tot 23 september 2026 kwam hij bij de overige partijen terecht.
    partijen = (
        '<PARTIES><PLAINTIFS><P><HT TYPE="BOLD">Alfa</HT>, vertegenwoordigd door X,</P>'
        "<PARTY.STATUS>rekwirant,</PARTY.STATUS></PLAINTIFS>"
        '<INTERVENERS><P>ondersteund door:</P><P><HT TYPE="BOLD">Beta</HT>,</P>'
        "<PARTY.STATUS>interveniënt in hogere voorziening,</PARTY.STATUS></INTERVENERS>"
        "<AGAINST>andere partij in de procedure:</AGAINST>"
        '<DEFENDANTS><P><HT TYPE="BOLD">Gamma</HT>,</P><P>in tegenwoordigheid van:</P>'
        '<P><HT TYPE="BOLD">Delta</HT>,</P></DEFENDANTS>'
        '<INTERVENERS><P>ondersteund door:</P><P><HT TYPE="BOLD">Epsilon</HT>,</P>'
        "</INTERVENERS></PARTIES>"
    )
    xml = re.sub(r"<PARTIES>.*?</PARTIES>", partijen, ARREST, flags=re.S)
    _, meta = formex_hof.omzetten(arrest_zip(xml))
    assert meta["partijen"] == ["Alfa", "Gamma"]
    # De scheiding binnen DEFENDANTS werkt nog zoals bij Schrems II: Delta is overig.
    assert meta["overige_partijen"] == ["Beta", "Delta", "Epsilon"]


@pytest.mark.parametrize("wijziging, verwachte_reden", [
    (lambda x: x.replace("<JUDGMENT>", "<CONCLUSION>").replace("</JUDGMENT>", "</CONCLUSION>"),
     "wordt niet ondersteund"),
    (lambda x: x.replace("<NP.ECR IDENTIFIER=\"NP0003\">", "<VERZONNEN>tekst</VERZONNEN><NP.ECR IDENTIFIER=\"NP0003\">"),
     "zonder eigen behandeling"),
    (lambda x: x.replace('<QUOT.START CODE="201E" REF.END="QE1" ID="QS1"/>', '<QUOT.START CODE="ZZ" ID="QS1"/>'),
     "aanhalingscode"),
    (lambda x: x.replace(
        '<ITEM><NP><NO.P><HT TYPE="BOLD">1)</HT></NO.P><TXT><HT TYPE="BOLD">Artikel 4 moet aldus '
        'worden uitgelegd.</HT></TXT></NP></ITEM>',
        '<ITEM><P>Artikel 4 moet aldus worden uitgelegd.</P></ITEM>'), "genummerde lijst"),
    (lambda x: x.replace('NUMBERING="STAR"', 'NUMBERING="XYZ"'), "nootnummering"),
])
def test_weigeringen(wijziging, verwachte_reden):
    with pytest.raises(ConversionError, match=verwachte_reden):
        formex_hof.omzetten(arrest_zip(wijziging(ARREST)))


def test_een_zip_met_meer_dan_een_onderdeel_of_een_afbeelding_wordt_geweigerd():
    xml = ARREST.encode("utf-8")
    with pytest.raises(ConversionError, match="2 XML-onderdelen"):
        formex_hof.omzetten(zipje(("a.xml", xml), ("b.xml", xml)))
    with pytest.raises(ConversionError, match="afbeelding of bijlage"):
        formex_hof.omzetten(zipje(("a.xml", xml), ("plaatje.tif", b"II*\x00")))
    with pytest.raises(ConversionError, match="geen leesbare zip"):
        formex_hof.omzetten(b"PK\x03\x04kapot")


BEELD = "ECR_62026CJ0001_NL_01_01.tif"


def _met_beeld(aanroep: str) -> str:
    """62012TJ0235 (Żubrówka): het merk staat als TIFF in een eigen P achter de zin die het aankondigt."""
    return ARREST.replace("<TXT>Het Hof overweegt als volgt.</TXT>",
                          "<TXT>Het Hof overweegt als volgt.</TXT>" + aanroep)


def test_een_afbeelding_die_de_uitspraak_aanroept_wordt_weggelaten_met_een_melding():
    xml = _met_beeld(f'<P><INCL.ELEMENT FILEREF="{BEELD}" TYPE="TIFF"/></P>')
    markdown, meta = formex_hof.omzetten(zipje(("ECR_62026CJ0001_NL_01.xml", xml), (BEELD, b"II*\x00")))
    assert "\n3. Het Hof overweegt als volgt.\n\n4. Bedrag" in markdown
    assert meta["afbeeldingen_weggelaten"] == [{"fileref": BEELD, "format": "TIFF"}]


@pytest.mark.parametrize("aanroep, bestanden, reden", [
    # Aangeroepen, maar niet in de zip.
    (f'<P><INCL.ELEMENT FILEREF="{BEELD}" TYPE="TIFF"/></P>', (), "niet in de zip"),
    # Midden in een zin: dan zou de alinea stil in tweeën vallen.
    (f'<P>Zie <INCL.ELEMENT FILEREF="{BEELD}" TYPE="TIFF"/> hierboven.</P>', (BEELD,), "niet als eigen alinea"),
    # Met eigen tekst: bij het Hof niet gemeten.
    (f'<P><INCL.ELEMENT FILEREF="{BEELD}" TYPE="TIFF"><IMG.CNT><P>Formulier</P></IMG.CNT>'
     "</INCL.ELEMENT></P>", (BEELD,), "IMG.CNT"),
])
def test_een_afbeelding_die_niet_als_losse_alinea_in_de_zip_staat_blijft_een_weigering(aanroep, bestanden, reden):
    onderdelen = [("ECR_62026CJ0001_NL_01.xml", _met_beeld(aanroep))] + [(b, b"II*\x00") for b in bestanden]
    with pytest.raises(ConversionError, match=reden):
        formex_hof.omzetten(zipje(*onderdelen))


def test_de_weggelaten_afbeelding_staat_in_de_herkomst(monkeypatch):
    xml = _met_beeld(f'<P><INCL.ELEMENT FILEREF="{BEELD}" TYPE="TIFF"/></P>')
    _fake(monkeypatch, {"resource/celex/": (200, zipje(("ECR_62026CJ0001_NL_01.xml", xml), (BEELD, b"II*\x00")))})
    h = from_link("62026CJ0001").provenance
    assert h.extra["afbeeldingen_weggelaten"] == [{"fileref": BEELD, "format": "TIFF"}]
    assert any(w.startswith("1 afbeelding uit de Formex-bron niet overgenomen") for w in h.waarschuwingen)


def test_een_andere_identiteit_dan_gevraagd_is_een_weigering():
    with pytest.raises(ConversionError, match="en niet 62099CJ0001"):
        formex_hof.omzetten(arrest_zip(), "62099CJ0001")
    # Het CELEX-alias van vóór de aparte gerechtsletter is dezelfde bron.
    oud = ARREST.replace("62026CJ0001", "62026J0001").replace(
        '<NO.ECLI ECLI="ECLI:EU:C:2026:1">EU:C:2026:1</NO.ECLI>', "")
    markdown, meta = formex_hof.omzetten(arrest_zip(oud), "62026CJ0001")
    assert meta["celex"] == "62026J0001" and meta["ecli"] is None


def test_de_zelfcontrole_weigert_verloren_tekst(monkeypatch):
    # Een omzetter die een alinea laat vallen moet door de onafhankelijke telling
    # worden betrapt, ook als hij zelf niets merkt.
    oorspronkelijk = formex_hof._Omzetter.gemengd

    def slordig(self, el):
        blokken = oorspronkelijk(self, el)
        if el.tag == "GR.VISA":
            return []
        return blokken

    monkeypatch.setattr(formex_hof._Omzetter, "gemengd", slordig)
    with pytest.raises(ConversionError, match="mist of verdubbelt tekst"):
        formex_hof.omzetten(arrest_zip())


# --------------------------------------------------------------------------
# De route: ophalen, herkomst en de weigering zonder terugval
# --------------------------------------------------------------------------

def _fake(monkeypatch, antwoorden):
    """`net.documents` geeft per URL-deel een vast antwoord; alle aanroepen worden geteld."""
    calls = []

    class Antwoord:
        def __init__(self, status, data):
            self.status_code = status
            self.content = data
            self.text = data.decode("latin-1", errors="ignore")
            self.apparent_encoding = "utf-8"
            self.url = ""

        def json(self):
            import json as _json
            return _json.loads(self.content)

    def get(url, headers=None, timeout=None, allow_redirects=None, params=None):
        calls.append((url, dict(headers or {})))
        for deel, (status, data) in antwoorden.items():
            if deel in url:
                return Antwoord(status, data)
        return Antwoord(404, b"")

    monkeypatch.setattr(eurlex.net, "documents", lambda: type("S", (), {"get": staticmethod(get)})())
    return calls


def test_ecli_en_celex_geven_dezelfde_route(monkeypatch):
    data = arrest_zip()
    calls = _fake(monkeypatch, {"resource/ecli/": (200, data), "resource/celex/": (200, data)})

    doc = from_link("ECLI:EU:C:2026:1")
    # Een ECLI moet url-encoded, en de Cellar krijgt de Formex-headers in het Nederlands.
    url, headers = calls[0]
    assert url == "http://publications.europa.eu/resource/ecli/ECLI%3AEU%3AC%3A2026%3A1"
    assert headers == {"Accept": "application/zip;mtype=fmx4", "Accept-Language": "nld"}
    assert doc.kind == "caselaw"
    assert doc.source == "EUR-Lex (Cellar Formex) • ECLI:EU:C:2026:1 • NL"

    h = doc.provenance
    assert (h.format, h.ecli, h.celex) == ("formex-hvj", "ECLI:EU:C:2026:1", "62026CJ0001")
    assert h.title == "ARREST VAN HET HOF (Vierde kamer) 9 januari 2026"
    assert h.extra["partijen"] == ["Alfa B.V.", "Beta N.V."]
    assert h.extra["procestaal"] == "Engels"

    # Het bronbewijs draagt precies de bytes die zijn opgehaald; `from_link` heeft het al
    # aan de herkomst gebonden.
    documenten = h.extra["source_structure"]["sources"]
    assert len(documenten) == 1
    assert documenten[0]["source_format"] == "formex-hvj"
    assert documenten[0]["source_sha256"] == hashlib.sha256(data).hexdigest()

    doc2 = from_link("62026CJ0001")
    assert calls[-1][0] == "http://publications.europa.eu/resource/celex/62026CJ0001"
    assert doc2.source == "EUR-Lex (Cellar Formex) • CELEX:62026CJ0001 • NL"


def test_geen_formex_is_een_weigering_zonder_terugval_op_html(monkeypatch):
    calls = _fake(monkeypatch, {"resource/celex/": (404, b"")})
    with pytest.raises(ConversionError, match="Er is geen terugval op HTML") as fout:
        from_link("62099CJ0001")
    assert "HTTP 404" in str(fout.value)
    # Precies één verzoek: er is niet stilletjes een tweede route geprobeerd.
    assert len(calls) == 1

    _fake(monkeypatch, {"resource/celex/": (200, b"<HTML>een pagina</HTML>")})
    with pytest.raises(ConversionError, match="het antwoord is geen zip"):
        from_link("62099CJ0001")


def test_sector_6_gaat_niet_langs_de_wetgevingsroute(monkeypatch):
    # De wetgevingsroute weigerde rechtspraak met "de zip bevat 0 inhoudsopgaven" - een
    # melding die niets zei. Een sector-6 CELEX komt nu bij de eigen omzetter uit.
    data = arrest_zip()
    _fake(monkeypatch, {"resource/celex/": (200, data)})
    doc = from_link("62026CJ0001")
    assert doc.provenance.format == "formex-hvj"


def test_een_arrest_krijgt_een_kb_bundel_op_zijn_ecli(monkeypatch):
    from mdconv import kb_bundle
    from mdconv.api import _doc_payload

    data = arrest_zip()
    _fake(monkeypatch, {"resource/ecli/": (200, data)})
    doc = from_link("ECLI:EU:C:2026:1")
    payload = _doc_payload(doc)
    assert "bundle_token" in payload

    stream, pad_id = kb_bundle.build(payload["bundle_token"], doc.markdown, bewerkt_met_ai=False)
    assert pad_id == "ECLI-EU-C-2026-1"
    digest = hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(stream) as archive:
        assert set(archive.namelist()) == {
            "raw/jurisprudentie/ECLI-EU-C-2026-1.md",
            "raw/jurisprudentie/ECLI-EU-C-2026-1.source.json",
            "raw/source-evidence/ECLI-EU-C-2026-1/fetch.json",
            f"raw/source-evidence/ECLI-EU-C-2026-1/{digest}.fmx4.zip",
        }
        assert archive.read(f"raw/source-evidence/ECLI-EU-C-2026-1/{digest}.fmx4.zip") == data


ZONDER_ECLI = ARREST.replace('<NO.ECLI ECLI="ECLI:EU:C:2026:1">EU:C:2026:1</NO.ECLI>', "")
SPARQL_ECLI = (b'{"results": {"bindings": [{"ecli": {"type": "literal", "value": "ECLI:EU:C:2026:1"}}]}}')


def test_een_ecli_uit_de_cellar_metadata_krijgt_zijn_herkomst_mee(monkeypatch):
    """Satamedia (62007CJ0073) noemt in zijn Formex geen ECLI; de Cellar kent hem wel
    (cdm:case-law_ecli). Vragen op de ECLI slaagt dan ook, en het zijbestand zegt waar de
    ECLI vandaan komt (T2-F14, kb WP-20)."""
    data = arrest_zip(ZONDER_ECLI)
    calls = _fake(monkeypatch, {"resource/ecli/": (200, data), "resource/celex/": (200, data),
                                "webapi/rdf/sparql": (200, SPARQL_ECLI)})
    for vraag in ("ECLI:EU:C:2026:1", "62026CJ0001"):
        doc = from_link(vraag)
        h = doc.provenance
        assert (h.ecli, h.celex, h.extra["ecli_herkomst"]) == ("ECLI:EU:C:2026:1", "62026CJ0001", "cellar-metadata")
        assert ("De bron noemt geen ECLI; ECLI:EU:C:2026:1 komt uit de Cellar-metadata (cdm:case-law_ecli) "
                "van 62026CJ0001.") in h.waarschuwingen
    assert any("sparql" in url for url, _ in calls)


def test_een_ecli_uit_de_bron_zelf_heeft_herkomst_formex_en_vraagt_de_cellar_niets(monkeypatch):
    calls = _fake(monkeypatch, {"resource/celex/": (200, arrest_zip())})
    h = from_link("62026CJ0001").provenance
    assert (h.ecli, h.extra["ecli_herkomst"]) == ("ECLI:EU:C:2026:1", "formex")
    assert not any("sparql" in url for url, _ in calls)


@pytest.mark.parametrize("antwoord", [
    (500, b""),                                                      # storing
    (200, b'{"results": {"bindings": []}}'),                          # geen ECLI bekend
    (200, b'{"results": {"bindings": [{"ecli": {"value": "ECLI:EU:C:2026:1"}}, '
          b'{"ecli": {"value": "ECLI:EU:C:2026:2"}}]}}'),             # twee: niet raden
    (200, b'{"results": {"bindings": [{"ecli": {"value": "EU:C:2026:1"}}]}}'),  # geen ECLI-vorm
])
def test_zonder_bruikbare_cellar_ecli_blijft_de_celex_de_identiteit(monkeypatch, antwoord):
    data = arrest_zip(ZONDER_ECLI)
    _fake(monkeypatch, {"resource/ecli/": (200, data), "resource/celex/": (200, data),
                        "webapi/rdf/sparql": antwoord})
    h = from_link("62026CJ0001").provenance
    assert h.ecli is None and "ecli_herkomst" not in h.extra
    assert "De bron noemt geen ECLI; het CELEX-nummer is de identiteit van dit document." in h.waarschuwingen
    with pytest.raises(ConversionError, match="zonder ECLI"):
        from_link("ECLI:EU:C:2026:1")
