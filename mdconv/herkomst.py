"""Waar een omgezet document vandaan komt, in de vorm die ernaast wordt bewaard.

De kennisbank die deze bestanden afneemt vraagt om een zijbestand met de URL, de
datum van raadpleging en — bij een geconsolideerde tekst — de geldigheidsperiode.
Zonder dat weet ze niet wélke versie ze bewaart, en blijven `valid_from` en
`valid_until` leeg.

**Plat, geen nesting.** Het inleesscript aan de andere kant leest vijf sleutels
rechtstreeks van het hoogste niveau (`bwb`, `geldend_van`, `geldend_tot`,
`geraadpleegd`, `source_url`). Een identifier in een subobject zetten zou dat
breken. Daarom staan `bwb`, `celex`, `ecli` en `bestandsnaam` naast elkaar en
zijn de ongebruikte `null` in plaats van afwezig: een afnemer kan altijd
`.get("bwb")` doen.

Deze module staat los van `sources/` omdat de bronmodules hem importeren en
`sources/__init__` de bronmodules — andersom zou dat een kringetje zijn.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field


def nu() -> str:
    """Tijdstip van ophalen, in UTC met een Z-suffix."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vandaag() -> str:
    return dt.date.today().isoformat()


@dataclass(frozen=True, slots=True)
class Herkomst:
    """Eén zijbestand: `<naam>.source.json` naast `<naam>.md`."""

    format: str

    # Precies één identifier is gevuld; de rest blijft null.
    bwb: str | None = None
    celex: str | None = None
    ecli: str | None = None
    bestandsnaam: str | None = None

    title: str | None = None
    language: str | None = None

    # Officiële XML-bronnen leveren deze velden rechtstreeks. De Engelse
    # sleutels zijn bewust gelijk aan wat de kennisbank al uit een zijbestand
    # leest (`extract_meta.py`); via `extra` zouden ze daar onzichtbaar zijn.
    oj_reference: str | None = None
    base_celex: str | None = None
    consolidation_date: str | None = None
    version: str | None = None

    # Welke versie, en sinds/tot wanneer die geldt. Leeg waar het begrip niet
    # bestaat: een uitspraak en een officiële bekendmaking wijzigen niet meer.
    versie: str | None = None
    geldend_van: str | None = None
    geldend_tot: str | None = None
    ingetrokken_op: str | None = None

    geraadpleegd: str = field(default_factory=vandaag)
    source_url: str | None = None
    requested_url: str | None = None

    # Meldingen die de bron zelf over deze versie doet, letterlijk overgenomen.
    toestand_meldingen: tuple[str, ...] = ()

    # BWB-XML noemt per vervallen artikel de datum in de structuur. Deze kaart
    # is autoritatiever dan een regex op de gerenderde melding.
    expired: dict[str, str] = field(default_factory=dict)

    fetched_at: str = field(default_factory=nu)
    converter: str | None = None

    # De telling waar de omzetting zelf op heeft gecontroleerd, zodat de afnemer
    # hem naast zijn eigen telling kan leggen.
    koppen_bron: int | None = None
    koppen_markdown: int | None = None

    waarschuwingen: tuple[str, ...] = ()

    # Een bestand dat door de AI-opschoning is geweest is geen ruwe bron meer.
    # Dit is de enige plek die dat kan vastleggen; het wordt pas bij het
    # downloaden gezet, want dán is bekend of er is opgeschoond.
    bewerkt_met_ai: bool = False

    # Wat alleen bij déze bron hoort (dcterms, engine, itemid, …).
    extra: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        d = asdict(self)
        d["toestand_meldingen"] = list(self.toestand_meldingen)
        d["waarschuwingen"] = list(self.waarschuwingen)
        return d

    def met(self, **velden) -> "Herkomst":
        """Een kopie met een paar velden anders (frozen, dus niet muteren)."""
        huidig = {f: getattr(self, f) for f in self.__slots__}
        huidig.update(velden)
        return Herkomst(**huidig)


def als_zijbestand(provenance, *, bewerkt_met_ai: bool, markdown: str | None = None) -> str | None:
    """`<naam>.source.json` als tekst, of `None` als er geen herkomst is.

    Bedoeld voor de kennisbankbundel; andere browserdownloads blijven een los
    `.md`-bestand. `bewerkt_met_ai` zegt of het document door "Opschonen" of
    "Vertalen" is gegaan — dat is pas bij het wegschrijven bekend. Met
    `markdown` wordt vastgelegd of de tekst sinds de omzetting is gewijzigd.
    """
    if provenance is None or provenance == {}:
        return None
    if not isinstance(provenance, dict):
        raise ValueError("Herkomst moet een JSON-object zijn.")
    # Via JSON kopiëren, zodat het aanvullen de herkomst van de aanroeper niet raakt.
    result = json.loads(json.dumps(provenance, ensure_ascii=False))
    result["bewerkt_met_ai"] = bool(bewerkt_met_ai or result.get("bewerkt_met_ai"))
    if markdown is not None:
        from .source_structure import sha256
        extra = result.get("extra")
        proof = extra.get("source_structure") if isinstance(extra, dict) else None
        if isinstance(proof, dict):
            proof["download_markdown_sha256"] = sha256(markdown)
            proof["markdown_changed"] = proof.get("markdown_sha256") != proof["download_markdown_sha256"]
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
