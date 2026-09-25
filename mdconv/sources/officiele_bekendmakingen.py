"""Kamerstukken uit de officiële XML van KOOP (officielebekendmakingen.nl).

Een Kamerstuk komt als PDF, als HTML en als XML. Alleen de XML draagt wat de andere twee
kwijtraken: koppen met hun eigen nummering (`<kop><nr>1.</nr><titel>Inleiding</titel>`),
voetnoten op de plek van hun marker (`<noot><noot.nr>1</noot.nr><noot.al>…`), lijsten met
hun eigen tekens en tabellen als CALS. Gemeten op `kst-34851-4`, dat als PDF (`--quality
poor`) nul gekoppelde noten en nauwelijks koppen opleverde: 85 koppen, 146 noten (145 in de
tekst, één in een tabel), 30 lijstitems, twee bijlagen en twee CALS-tabellen.

Twee bestanden per publicatie: `<id>.xml` (de tekst) en `<id>/metadata.xml` (de
Dublin Core- en `OVERHEIDop`-velden). De metadata is gegeven en niet geschat: titel,
indieners, datum en identiteit staan er letterlijk in, terwijl `classify.py` in de
kennisbank ze uit de eerste zestig regels moest raden en bij de KNMG-richtlijn ernaast zat.

**Geen XML is een weigering, geen terugval op de PDF.** Een `blg-`-bijlage heeft geen eigen
XML (`404`). Stil terugvallen op de PDF zou een document met een andere bewijskracht als
hetzelfde laten doorgaan; dezelfde lijn als bij het Hof en het EHRM. De gebruiker kiest
daarna bewust de PDF-route.

**De raw-vorm is die het profiel `md-clean-documenten` al aankan** (AGENTS.md, regel 3):
de nummering staat in de koptekst (`## 1. Inleiding`), zodat het profiel de bronnummering
gebruikt en geen anker afleidt; een noot is native (`[^1]` en `[^1]: …`), zoals bij de
rechtspraak. Opmaak (`<nadruk>`) levert geen `*` op: een kop die als `*Inleiding*` in de raw
komt, matcht de kopherkenning niet meer. De tekst verandert er niet door.

Wat de route **weigert** in plaats van raadt: een ander worteldocument dan `kamerstuk` (het
vocabulaire van Staatsblad en Staatscourant is niet gemeten), elk element met tekst zonder
eigen behandeling, een lijst die niet `expliciet` genummerd is, een geneste lijst, een
tabel die niet rechthoekig te maken is, een nootmarker zonder definitie, een `nootref` die
naar geen noot in het stuk wijst, en een bron waarvan de woorden na omzetting niet als
multiset gelijk zijn.
"""

from __future__ import annotations

import re
from collections import Counter

from lxml import etree

from .. import net
from ..errors import ConversionError
from ..herkomst import Herkomst
from ..source_structure import record_source
from . import xml_gedeeld as xg

# Een publicatie-id van de Officiële Bekendmakingen, kaal of in een link. Volledig
# geankerd voor het kale geval: `kst-34851-4` is een id, `kst` in een zin niet.
_ID = r"(?:kst|stb|stcrt|trb|ah-tk|kv-tk|h-tk|blg)-[a-z0-9]+(?:-[a-z0-9]+)*"
ID_RE = re.compile(rf"^{_ID}$", re.I)
LINK_RE = re.compile(rf"officielebekendmakingen\.nl/({_ID})(?:[./?#]|$)", re.I)

_BASIS = "https://zoek.officielebekendmakingen.nl"
_TIMEOUT = 60
_MEDIA_TYPE = "application/xml"

# Elementen zonder eigen betekenis in de uitvoer: de kinderen tellen.
_TRANSPARANT = {"algemeen", "vrije-tekst", "tekst", "stuk", "al-groep"}


def matches(query: str) -> bool:
    """Is dit een publicatie-id of een link naar de Officiële Bekendmakingen?

    De link wordt niet geopend om hem te herkennen.
    """
    q = query.strip()
    return bool(ID_RE.match(q) or LINK_RE.search(q))


def publicatie_id(query: str) -> str:
    q = query.strip()
    m = ID_RE.match(q) or LINK_RE.search(q)
    if not m:
        raise ConversionError(
            "Geen geldig publicatienummer herkend (bv. kst-34851-4 of "
            "https://zoek.officielebekendmakingen.nl/kst-34851-4.html).")
    return (m.group(1) if m.lastindex else m.group(0)).lower()


def slug(pub_id: str, metadata: dict) -> str:
    """De documentidentiteit, volgens `identifiers.md` van de kennisbank.

    Een Kamerstuk krijgt `kst-<dossier>-nr-<ondernummer>`, uit de metadata van de bron en
    niet uit het patroon van het id. Wat geen Kamerstuk is heeft geen afgesproken slug en
    wordt hier niet bedacht.
    """
    dossier, onder = metadata.get("dossiernummer"), metadata.get("ondernummer")
    if pub_id.startswith("kst-") and dossier and onder:
        kaal = re.sub(r"[^a-z0-9]", "", dossier.lower())
        nr = re.sub(r"[^a-z0-9]", "", onder.lower())
        if kaal and nr:
            return f"kst-{kaal}-nr-{nr}"
    raise ConversionError(
        f"Voor {pub_id} is geen documentidentiteit af te leiden uit de metadata (dossier "
        f"{dossier!r}, ondernummer {onder!r}); omzetting geweigerd.")


def fetch(query: str) -> tuple[str, str, Herkomst]:
    pub_id = publicatie_id(query)
    url = f"{_BASIS}/{pub_id}.xml"
    r = net.documents().get(url, timeout=_TIMEOUT)
    if r.status_code == 404:
        raise ConversionError(
            f"{pub_id} heeft geen officiële XML (404). Een bijlage of een stuk zonder "
            "gestructureerde bron kan alleen als PDF; die route is een bewuste keuze en geen "
            "terugval. Upload de PDF met een documentnummer.")
    if r.status_code != 200 or not r.content:
        raise ConversionError(f"Kon {pub_id} niet ophalen (status {r.status_code}).")
    mr = net.documents().get(f"{_BASIS}/{pub_id}/metadata.xml", timeout=_TIMEOUT)
    if mr.status_code != 200 or not mr.content:
        raise ConversionError(
            f"De metadata van {pub_id} is niet op te halen (status {mr.status_code}); zonder "
            "metadata is de identiteit niet vast te stellen, en die wordt niet geraden.")
    return converteer(bytes(r.content), bytes(mr.content), pub_id, url)


def converteer(data: bytes, metadata_bytes: bytes, pub_id: str,
               url: str | None = None) -> tuple[str, str, Herkomst]:
    """Zet de XML en zijn metadata om; los van het netwerk, en dus te testen."""
    metadata = lees_metadata(metadata_bytes, pub_id)
    markdown, meta = omzetten(data, metadata)
    identiteit = slug(pub_id, metadata)
    url = url or f"{_BASIS}/{pub_id}.xml"
    record_source(data, media_type=_MEDIA_TYPE, source_format="op-xml", source_url=url,
                  identifier=identiteit, language=metadata.get("taal") or "nl")
    herkomst = Herkomst(
        format="op-xml", document_id=identiteit, title=metadata.get("dc_title"),
        language=metadata.get("taal") or "nl", source_url=f"{_BASIS}/{pub_id}.html",
        requested_url=url, koppen_bron=meta["koppen"],
        koppen_markdown=sum(1 for r in markdown.splitlines() if r.startswith("#")),
        waarschuwingen=tuple(meta["waarschuwingen"]),
        extra={"publicatie_id": pub_id, "metadata": metadata, **{
            k: meta[k] for k in ("noten", "lijstitems", "tabellen", "bijlagen", "opmaak_weggelaten",
                                  "extrefs", "nootverwijzingen")}},
    )
    return markdown, f"Officiële Bekendmakingen • {pub_id}", herkomst


# --------------------------------------------------------------------------
# De metadata
# --------------------------------------------------------------------------

_META_ENKEL = {
    "DC.title": "dc_title", "OVERHEIDop.documenttitel": "documenttitel",
    "OVERHEIDop.dossiernummer": "dossiernummer", "OVERHEIDop.dossiertitel": "dossiertitel",
    "OVERHEIDop.ondernummer": "ondernummer", "OVERHEIDop.publicationName": "publicatienaam",
    "OVERHEIDop.vergaderjaar": "vergaderjaar", "DCTERMS.language": "taal",
    "DCTERMS.issued": "datum", "DCTERMS.available": "beschikbaar", "DC.creator": "instantie",
    "DC.identifier": "identifier", "OVERHEIDop.documentStatus": "status",
}


def lees_metadata(data: bytes, pub_id: str) -> dict:
    """De velden uit `metadata.xml`, met de identiteitscontrole.

    De bron moet zeggen wie hij is: noemt `DC.identifier` een ander id dan gevraagd, dan is
    dat een weigering en geen terugval.
    """
    root = _wortel(data, "metadata_gegevens", "metadata")
    uit: dict = {"indieners": []}
    for el in root:
        naam, inhoud = el.get("name"), " ".join((el.get("content") or "").split())
        if naam == "OVERHEIDop.indiener" and inhoud:
            uit["indieners"].append(inhoud)
        elif naam in _META_ENKEL and inhoud:
            uit.setdefault(_META_ENKEL[naam], inhoud)
    if (uit.get("identifier") or "").lower() != pub_id:
        raise ConversionError(
            f"De metadata noemt zichzelf {uit.get('identifier')!r} en niet {pub_id}; "
            "omzetting geweigerd.")
    return uit


# --------------------------------------------------------------------------
# De omzetting
# --------------------------------------------------------------------------

def _kort(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else "?"


def _wortel(data: bytes, verwacht: str, wat: str):
    # `recover` staat bewust uit: een bron die niet heel is, is een weigering.
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ConversionError(f"De {wat} van de publicatie is geen leesbare XML: {exc}") from exc
    if _kort(root) != verwacht.replace("_", "-") and _kort(root) != verwacht:
        raise ConversionError(f"Het worteldocument van de {wat} is <{_kort(root)}> en geen <{verwacht}>.")
    return root


def _woorden(tekst: str) -> list[str]:
    return re.findall(r"\w+", _MARKER.sub(" ", tekst).lower(), re.UNICODE)


_MARKER = re.compile(r"\[\^[^\]]+\]:?")
_STREEP = {"–", "-", "—", "•"}


class _Lezer:
    def __init__(self) -> None:
        self.uit = xg.Uitvoer()
        self.noot_labels: dict = {}      # element -> label (lxml houdt de proxy levend zolang we hem bewaren)
        self.noot_ids: dict[str, str] = {}   # @id van een noot -> label, voor `nootref`
        self.nootrefs = 0
        self.gebruikt: list[str] = []
        self.koppen = self.lijstitems = self.tabellen = self.bijlagen = 0
        self.opmaak = self.extrefs = 0
        self.tabelnr = 0
        self.gegenereerd: Counter = Counter()

    # -- inline ---------------------------------------------------------

    def inline(self, el, *, eenregelig: bool = True) -> str:
        """De tekst van een element als blok, met `[^n]` op de plek van elke noot."""
        tekst = " ".join(self._ruw(el).split())
        return tekst

    def _ruw(self, el) -> str:
        delen = [el.text or ""]
        for kind in el:
            naam = _kort(kind)
            if naam == "noot":
                label = self.noot_labels.get(kind)
                if label is None:
                    raise ConversionError("Een noot in de bron is niet verzameld; omzetting geweigerd.")
                self.gebruikt.append(label)
                delen.append(f"[^{label}]")
            elif naam == "nootref":
                # Een tweede verwijzing naar een noot die al eerder staat:
                # `…de wettelijke grondslag noemen.<nootref refid="ID-…-d36e7172"/>` in de
                # memorie van toelichting bij de Cyberbeveiligingswet (kst-36764-3). De
                # bron zegt met `@refid` naar welke noot; de marker is die van die noot,
                # en er komt geen tweede definitie. Een `refid` die naar geen noot in het
                # stuk wijst, of een `nootref` met inhoud, is niet gemeten.
                label = self.noot_ids.get(kind.get("refid") or "")
                if label is None:
                    raise ConversionError(
                        f"Een nootverwijzing (nootref) wijst naar {kind.get('refid')!r}, en dat is "
                        "geen noot in dit stuk; omzetting geweigerd.")
                if len(kind) or (kind.text or "").strip():
                    raise ConversionError("Een nootverwijzing (nootref) met inhoud; omzetting geweigerd.")
                self.nootrefs += 1
                delen.append(f"[^{label}]")
            elif naam == "nadruk":
                self.opmaak += 1
                delen.append(self._ruw(kind))
            elif naam == "extref":
                self.extrefs += 1
                delen.append(self._ruw(kind))
            elif naam == "ondernummer":
                # `Nr. <ondernummer>4</ondernummer>`: het nummer hoort bij zijn voorvoegsel.
                delen.append(self._ruw(kind))
            elif naam in ("functie", "voornaam", "achternaam", "naam"):
                delen.append(" " + self._ruw(kind) + " ")
            elif naam.startswith("?") or naam == "?":
                pass
            else:
                raise ConversionError(
                    f"XML-element zonder eigen behandeling binnen een alinea ({naam}); omzetting geweigerd.")
            delen.append(kind.tail or "")
        return "".join(delen)

    # -- blokken --------------------------------------------------------

    def lees(self, el, diepte: int) -> None:
        for kind in el:
            naam = _kort(kind)
            if naam == "al":
                self.uit.blok(self.inline(kind))
            elif naam == "divisie":
                self.divisie(kind, diepte + 1)
            elif naam == "bijlage":
                self.bijlagen += 1
                self.divisie(kind, 1)
            elif naam == "tussenkop":
                self.uit.blok(f"{'#' * min(diepte + 2, 6)} {self.inline(kind)}")
                self.koppen += 1
            elif naam in _TRANSPARANT:
                self.lees(kind, diepte)
            elif naam == "lijst":
                self.lijst(kind)
            elif naam == "table":
                self.tabel(kind)
            elif naam == "tekst-sluiting":
                for onder in kind:
                    if _kort(onder) != "ondertekening":
                        raise ConversionError(f"Onverwacht element in de sluiting ({_kort(onder)}).")
                    self.uit.blok(self.inline(onder))
            elif naam == "kop":
                # Een kop buiten een divisie of bijlage hoort bij het stuk zelf; die staat er niet.
                raise ConversionError("Een kop buiten een divisie of bijlage; omzetting geweigerd.")
            elif naam.startswith("?"):
                pass
            elif len(kind) or (kind.text or "").strip():
                self.uit.markeer_onbekend(naam)

    def divisie(self, el, diepte: int) -> None:
        kop = next((k for k in el if _kort(k) == "kop"), None)
        if kop is not None:
            nr = next((k for k in kop if _kort(k) == "nr"), None)
            titel = next((k for k in kop if _kort(k) == "titel"), None)
            onbekend = [_kort(k) for k in kop if _kort(k) not in ("nr", "titel")]
            if onbekend or titel is None:
                raise ConversionError(f"Een kop met onverwachte inhoud ({onbekend or 'geen titel'}).")
            tekst = " ".join(t for t in (self.inline(nr) if nr is not None else "", self.inline(titel)) if t)
            self.uit.blok(f"{'#' * min(diepte + 1, 6)} {tekst}")
            self.koppen += 1
        rest = _Ouder(el, kop)
        self.lees(rest, diepte)

    def lijst(self, lijst, niveau: int = 0) -> None:
        if lijst.get("type") != "expliciet":
            raise ConversionError(
                f"Een lijst van type {lijst.get('type')!r}; alleen `expliciet` is gemeten en dan "
                "staat elk teken in de bron. Omzetting geweigerd.")
        sluiting = lijst.get("nr-sluiting") or ""
        for item in lijst:
            if _kort(item) != "li":
                raise ConversionError(f"Onverwacht element in een lijst ({_kort(item)}).")
            nr, regels, onder = None, [], []
            for deel in item:
                naam = _kort(deel)
                if naam == "li.nr":
                    nr = self.inline(deel)
                elif naam == "al":
                    tekst = self.inline(deel)
                    if tekst:
                        regels.append(tekst)
                elif naam == "lijst":
                    onder.append(deel)
                else:
                    raise ConversionError(f"Onverwacht element in een lijstitem ({naam}).")
            if not regels:
                if onder:
                    raise ConversionError("Een lijstitem met alleen een lijst; omzetting geweigerd.")
                continue
            if nr is None or nr in _STREEP:
                marker = "-"
            else:
                # De bron zegt welk nummer en welk sluitteken; het sluitteken is opmaak.
                marker = nr if (not sluiting or nr.endswith(sluiting)) else nr + sluiting
                if not nr.endswith(sluiting):
                    self.gegenereerd.update([])   # een leesteken is geen woord
            inspring = "  " * niveau
            blok = f"{inspring}{marker} {regels[0]}"
            for regel in regels[1:]:
                blok += "\n" + inspring + " " * (len(marker) + 1) + regel
            self.uit.blok(blok)
            self.lijstitems += 1
            for kind in onder:
                self.lijst(kind, niveau + 1)

    def tabel(self, tabel) -> None:
        self.tabellen += 1
        self.tabelnr_huidig = self.tabellen
        groepen = [g for g in tabel if _kort(g) == "tgroup"]
        if len(groepen) != 1:
            raise ConversionError(f"Een tabel heeft {len(groepen)} tgroup-elementen; precies één is vereist.")
        for k in tabel:
            if _kort(k) == "title":
                self.uit.blok(self.inline(k))
            elif _kort(k) not in ("tgroup",):
                raise ConversionError(f"Onverwacht element in een tabel ({_kort(k)}).")
        groep = groepen[0]
        kolomnamen = {}
        for nummer, spec in enumerate((s for s in groep if _kort(s) == "colspec"), start=1):
            if spec.get("colname"):
                kolomnamen[spec.get("colname")] = int(spec.get("colnum") or nummer) - 1
        kop_rijen, rijen = 0, []
        for deel in groep:
            naam = _kort(deel)
            if naam in ("colspec", "spanspec"):
                continue
            if naam not in ("thead", "tbody", "tfoot"):
                raise ConversionError(f"Onverwacht element in een tabel ({naam}).")
            for rij in deel:
                if _kort(rij) != "row":
                    raise ConversionError(f"Onverwacht element in een tabel ({_kort(rij)}).")
                cellen = []
                for cel in rij:
                    if _kort(cel) != "entry":
                        raise ConversionError(f"Onverwacht element in een tabelrij ({_kort(cel)}).")
                    start, eind = cel.get("namest"), cel.get("nameend")
                    colspan, kol = 1, None
                    if start or eind:
                        if start not in kolomnamen or eind not in kolomnamen:
                            raise ConversionError("Een cel verwijst naar een kolomnaam die de tabel niet kent.")
                        kol = kolomnamen[start]
                        colspan = kolomnamen[eind] - kolomnamen[start] + 1
                    tekst = " ".join(self._cel(cel).split())
                    rowspan = int(cel.get("morerows") or 0) + 1
                    if rowspan * colspan > 1:
                        self.gegenereerd.update(_woorden(tekst) * (rowspan * colspan - 1))
                    cellen.append({"tekst": tekst, "kol": kol, "colspan": colspan, "rowspan": rowspan})
                rijen.append(cellen)
                if naam == "thead":
                    kop_rijen += 1
        markdown, _ = xg.tabel_markdown(rijen, kop_rijen)
        self.uit.blok(markdown)

    def _cel(self, cel) -> str:
        delen = [cel.text or ""]
        for kind in cel:
            if _kort(kind) != "al":
                raise ConversionError(f"Onverwacht element in een tabelcel ({_kort(kind)}).")
            delen.append(" " + self.inline(kind) + " ")
            delen.append(kind.tail or "")
        return "".join(delen)


class _Ouder:
    """Een element zonder zijn kop: de kinderen van een divisie of bijlage."""

    def __init__(self, el, zonder) -> None:
        self._kinderen = [k for k in el if k is not zonder]

    def __iter__(self):
        return iter(self._kinderen)


def omzetten(data: bytes, metadata: dict) -> tuple[str, dict]:
    """Het Kamerstuk als Markdown, plus wat de herkomst erover vastlegt."""
    root = _wortel(data, "officiele-publicatie", "tekst")
    kinderen = [k for k in root if _kort(k) != "metadata"]
    if len(kinderen) != 1 or _kort(kinderen[0]) != "kamerstuk":
        raise ConversionError(
            f"De publicatie bevat {[_kort(k) for k in kinderen]} en geen enkel <kamerstuk>; alleen "
            "Kamerstukken zijn gemeten, en het vocabulaire van andere soorten wordt niet geraden.")
    stuk = kinderen[0]
    lezer = _Lezer()
    _verzamel_noten(stuk, lezer)

    kop = next((k for k in stuk if _kort(k) == "kamerstukkop"), None)
    dossier = next((k for k in stuk if _kort(k) == "dossier"), None)
    inhoud = next((k for k in stuk if _kort(k) == "stuk"), None)
    if dossier is None or inhoud is None:
        raise ConversionError("Het Kamerstuk mist zijn dossier of zijn stuk; omzetting geweigerd.")
    for k in stuk:
        if _kort(k) not in ("kamerstukkop", "dossier", "stuk"):
            raise ConversionError(f"Onverwacht element in het Kamerstuk ({_kort(k)}).")
    if kop is not None:
        for regel in kop:
            if _kort(regel) != "tekstregel":
                raise ConversionError(f"Onverwacht element in de kamerstukkop ({_kort(regel)}).")
            lezer.uit.blok(lezer.inline(regel))

    # De titelregel: dossiernummer, dossiertitel, stuknummer en stuktitel, zoals de PDF ze
    # op de titelpagina zet (`34 851 Regels … Nr. 4 ADVIES …`). Het leesteken ertussen is
    # opmaak; elk woord komt uit de bron.
    dossiernr = " ".join(t.strip() for t in dossier.find("dossiernummer").itertext() if t.strip()) \
        if dossier.find("dossiernummer") is not None else ""
    dossiertitel = lezer.inline(dossier.find("titel")) if dossier.find("titel") is not None else ""
    stuknr = lezer.inline(inhoud.find("stuknr")) if inhoud.find("stuknr") is not None else ""
    stuktitel = lezer.inline(inhoud.find("titel")) if inhoud.find("titel") is not None else ""
    onder = [_kort(k) for k in dossier if _kort(k) not in ("dossiernummer", "titel")]
    if onder:
        raise ConversionError(f"Onverwacht element in het dossier ({onder}).")
    lezer.uit.blok("# " + " ".join(p for p in (dossiernr, dossiertitel.rstrip(".") + ".", stuknr, stuktitel) if p.strip(". ")))
    lezer.koppen += 1
    rest = _Ouder(inhoud, None)
    rest._kinderen = [k for k in inhoud if _kort(k) not in ("stuknr", "titel")]
    lezer.lees(rest, 0)

    for noot in stuk.iter("noot"):
        label = lezer.noot_labels[noot]
        # Een noot mag uit meer dan één alinea bestaan (`kst-34851-3`: een URL die over twee
        # `noot.al` is verdeeld). Op de eerste alinea stoppen liet 'news/en/news-room/...' weg;
        # de woordtelling wees dat aan.
        alineas = [lezer.inline(al) for al in noot.findall("noot.al")]
        lezer.uit.noten.append((label, " ".join(a for a in alineas if a)))

    markdown = lezer.uit.markdown()
    ongebruikt = sorted(set(lezer.noot_labels.values()) - set(lezer.gebruikt), key=str)
    if ongebruikt:
        raise ConversionError(f"Een noot in de bron heeft geen marker: {ongebruikt}; omzetting geweigerd.")
    _zelfcontrole(stuk, lezer, markdown)
    waarschuwingen = []
    if lezer.opmaak:
        waarschuwingen.append(f"{lezer.opmaak} keer opmaak (<nadruk>) niet overgenomen; de tekst blijft.")
    if lezer.nootrefs:
        # Een tweede marker naar dezelfde noot: de kennisbank telt markers per noot, en
        # moet deze relatie ook lezen voordat het document door haar poort kan.
        waarschuwingen.append(
            f"{lezer.nootrefs} keer verwijst een tweede marker naar een noot die al eerder "
            "staat (nootref); de marker is die van die noot.")
    return markdown, {
        "koppen": lezer.koppen, "noten": len(lezer.uit.noten), "lijstitems": lezer.lijstitems,
        "tabellen": lezer.tabellen, "bijlagen": lezer.bijlagen, "opmaak_weggelaten": lezer.opmaak,
        "extrefs": lezer.extrefs, "nootverwijzingen": lezer.nootrefs, "waarschuwingen": waarschuwingen,
    }


def _verzamel_noten(stuk, lezer: _Lezer) -> None:
    """Geef elke noot zijn label vóór het lopen, want een marker staat vóór zijn definitie.

    Een voetnoot heet zoals de bron hem nummert (`1` tot en met `145`). Een tabelnoot begint in
    elke tabel bij 1 opnieuw en krijgt daarom `t<tabel>-<nr>`, zodat geen label dubbel voorkomt.
    """
    tabelvolgorde = {t: i for i, t in enumerate(stuk.iter("table"), start=1)}
    ouders = {c: p for p in stuk.iter() for c in p}
    gezien: set[str] = set()
    for noot in stuk.iter("noot"):
        nr = (noot.find("noot.nr").text or "").strip() if noot.find("noot.nr") is not None else ""
        if not nr or not noot.findall("noot.al"):
            raise ConversionError("Een noot in de bron mist zijn nummer of zijn tekst.")
        soort = noot.get("type")
        if soort == "voet":
            label = nr
        elif soort == "tabel":
            tabel, p = None, noot
            while p in ouders:
                p = ouders[p]
                if _kort(p) == "table":
                    tabel = p
                    break
            if tabel is None:
                raise ConversionError("Een tabelnoot staat buiten een tabel.")
            label = f"t{tabelvolgorde[tabel]}-{nr}"
        else:
            raise ConversionError(f"Een noot van type {soort!r}; alleen `voet` en `tabel` zijn gemeten.")
        if not re.fullmatch(r"[A-Za-z0-9-]+", label):
            raise ConversionError(f"Een nootnummer dat geen label kan zijn ({nr!r}).")
        if label in gezien:
            raise ConversionError(f"Twee noten met hetzelfde label ({label}).")
        gezien.add(label)
        lezer.noot_labels[noot] = label
        if noot.get("id"):
            lezer.noot_ids[noot.get("id")] = label


def _zelfcontrole(stuk, lezer: _Lezer, markdown: str) -> None:
    """Weigeren als de omzetting tekst heeft verloren of verdubbeld.

    De bron wordt met een eigen traversal geteld, niet met de code die de Markdown bouwde:
    alle tekst behalve `noot.nr` (dat wordt een label) en `metadata`, met een spatie na elk
    element dat geen opmaak is. Voor- en achternaam staan in de bron aan elkaar
    (`<voornaam>S.</voornaam><achternaam>Dekker</achternaam>`), en de omzetter zet er een
    spatie tussen; die spatie is opmaak.
    """
    inline = {"nadruk", "extref"}
    delen: list[str] = []

    def loop(el) -> None:
        if _kort(el) == "noot.nr":
            return
        delen.append(el.text or "")
        for kind in el:
            if not isinstance(kind.tag, str):
                # Een verwerkingsinstructie (`<?xpp afbm?>`, een zetinstructie midden in een
                # woord: `UZI-server<?xpp afbm?>certificaat`) is onzichtbaar. Ze draagt geen
                # woord en geen spatie; alleen wat erna komt telt.
                delen.append(kind.tail or "")
                continue
            loop(kind)
            if _kort(kind) not in inline:
                delen.append(" ")
            delen.append(kind.tail or "")

    loop(stuk)
    bron = Counter(lezer.gegenereerd)
    bron.update(_woorden("".join(delen)))
    kaal = re.sub(r"^\s*#{1,6}\s+", "", markdown, flags=re.M)
    uit = Counter(_woorden(kaal))
    if bron != uit:
        tekort, teveel = bron - uit, uit - bron
        raise ConversionError(
            "De omzetting mist of verdubbelt tekst ten opzichte van de bron "
            f"(ontbreekt: {dict(list(tekort.items())[:5])}; te veel: {dict(list(teveel.items())[:5])}); "
            "omzetting geweigerd.")
