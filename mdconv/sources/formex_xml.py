"""Formex 4 (Publicatieblad) -> de raw-vorm die md-clean-eurlex verwacht.

De Cellar levert een zip met een documentmanifest (`.doc.xml` of
`.doc.fmx.xml`) en per onderdeel een XML: de handeling (`ACT`, of `CONS.ACT`
bij een geconsolideerde tekst) en elke bijlage. Nieuwe verpakkingen bevatten
daarnaast de publicatie-inhoudsopgave waarnaar het manifest verwijst. De
volgorde komt uit het documentmanifest, niet uit de bestandsnamen.

**Deze omzetter schrijft niet de mooiste Markdown, maar de vorm die het profiel
al aankan.** Gemeten op 20 september 2026: een vrijere vorm wordt door
`md-clean-eurlex/scripts/check_source.py` geweigerd (nul harde spaties) en laat
`plan_structure.py` 46 artikelen zonder anker. De conventies van het
Publicatieblad zijn dus geen opmaak maar dragende structuur:

- de titelregels staan in kapitalen (`HT TYPE="UC"`), elk op een eigen regel;
- een artikelkop is kaal (`### Artikel 1`), het opschrift staat op de regel
  eronder — `plan_structure` voegt die twee zelf samen;
- een lid is een gewone alinea die begint met `1.` plus **drie harde spaties**;
  dat is het enige wat een lid van een alinea onderscheidt;
- een overweging is `(1)` plus één spatie, een voetnootdefinitie `(1)` plus
  twee harde spaties: hetzelfde nummer, ander aantal spaties;
- onderdelen (`a)`, `i)`) zijn alinea's, geen Markdown-lijst;
- alleen echte tabellen worden een pipe-tabel; de tweekoloms markeropmaak van
  het Publicatieblad is in raw al platgeslagen tot alinea's;
- het notenblok van de wettekst staat ná de ondertekening, de tabelnoten van
  een bijlage staan achter die bijlage en tellen daar opnieuw vanaf (1).

De ankers in `Uitvoer.eenheden` zijn niet voor het profiel bedoeld — dat leidt
zijn ankers zelf af — maar voor de zelfcontrole van de omzetter: elk artikel,
lid en onderdeel uit de XML moet terug te vinden zijn in de uitvoer.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

from ..errors import ConversionError
from .xml_gedeeld import LATIJN, Uitvoer, nummer_anker, tabel_markdown, ws

NBSP = " "
ONGENUMMERD = {"DASH", "NDASH", "BULLET", "NONE", "DISC"}
METADATA = {"BIB.INSTANCE", "BIB.DOC", "BIB.DATA", "PUBLICATION.REF", "NO.DOC", "INFO.CONSLEG",
            "INFO.PROD", "FAM.COMP", "GR.MOD.ACT", "DOCUMENT.REF", "PAGE.FIRST", "PAGE.LAST",
            "PAGE.SEQ", "PAGE.TOTAL", "LG.DOC", "NO.SEQ", "VOLUME.REF"}
INLINE_TEKST = {"DATE", "REF.DOC.OJ", "FT", "HT", "QUOT.S", "IE", "PERIOD", "REF.DOC", "ACRONYM",
                "ADDR", "PL.DATE", "NO.CELEX", "UNIT", "EXPONENT", "INF", "SUP", "TERM", "DEFINITION",
                # De ELI-verwijzing die het Publicatieblad sinds 2026 achter elke
                # REF.DOC.OJ in een noot zet; de zichtbare tekst is de URI zelf.
                "LINK"}
INLINE_TRANSPARANT = {"TI", "STI", "NP", "NO.P", "NO.PARAG", "TXT", "ITEM", "PREFIX"}
STRUCTUUR_ELEMENTEN = {
    "ACT", "CONS.ACT", "CONS.DOC", "ANNEX", "CONS.ANNEX", "TITLE", "PREAMBLE",
    "GR.VISA", "GR.CONSID", "CONSID", "ENACTING.TERMS", "FINAL", "DIVISION",
    "ARTICLE", "PARAG", "NO.PARAG", "ALINEA", "P", "LIST", "DLIST", "DLIST.ITEM",
    "ITEM", "NP", "NO.P", "TXT", "TBL", "CORPUS", "ROW", "CELL", "GR.NOTES",
    "NOTE", "CONTENTS", "GR.SEQ", "TI", "STI", "TI.ART", "STI.ART", "PREFIX",
    "TERM", "DEFINITION", "PREAMBLE.INIT", "PREAMBLE.FINAL", "GR.CONSID.INIT",
    "VISA", "SIGNATORY", "SIGNATURE", "COM",
    # Een afbeelding (TIFF-inclusie) kan haar tekst meedragen: het formulier van
    # Brussel I bis staat als P's in IMG.CNT. Wat daarbinnen staat, moet zelf
    # bekend zijn; een FORMULA blijft dus een weigering.
    "INCL.ELEMENT", "IMG.CNT",
}
# De letter van een CELEX-nummer volgens het soort handeling (`LEG.VAL`). Wat hier
# niet in staat, is niet af te leiden en moet dan uit `NO.CELEX` komen.
CELEX_LETTER = {"REG": "R", "DIR": "L", "DEC": "D"}
BEKENDE_TEKSTELEMENTEN = METADATA | INLINE_TEKST | INLINE_TRANSPARANT | STRUCTUUR_ELEMENTEN
# Het enige afbeeldingstype dat in de meetlat voorkomt (257 inclusies in 14
# documenten, 23 september 2026). Een ander type blijft een weigering.
AFBEELDINGSTYPE = "TIFF"
# De kop van een bijlageonderdeel met een nummer: `A.`, `1.`, of een woord met een
# nummer erachter. Bijlage VIII en XI van 2024/1689 schrijven `Afdeling A —` en
# `Afdeling 1`; MiCA (2023/1114) `Deel A:` tot en met `Deel I:`, de
# zorgvuldigheidsrichtlijn (2024/1760) `Deel I` en `Deel II`, de SCC's (2021/914)
# `AFDELING II` met daarin `Bepaling 8`, en de ITS-richtlijn (2010/40)
# `— Prioritair gebied I:`. Elk van die delen begint weer bij punt 1; zonder dat
# niveau in het anker weigerde de zelfcontrole ze op dubbele ankers. Een
# decimaal nummer blijft heel: de SCC's nummeren binnen bepaling 8 `8.1.` tot
# en met `8.9.`, en als `8` kregen die negen onderdelen één anker.
ONDERDEELKOP = re.compile(
    r"[—–-]?\s*(?:(?:Afdeling|Deel|Onderdeel|Bepaling|Module|Titel|Hoofdstuk|Sectie|"
    r"Prioritair\s+gebied)\s+)?([A-Z]|[IVXLC]+|\d+(?:\.\d+)*)(?:\.|\b)", re.I)


def _xml_fout(boodschap: str, exc: Exception | None = None) -> ConversionError:
    fout = ConversionError(f"Formex-bron geweigerd: {boodschap}")
    if exc is not None:
        fout.__cause__ = exc
    return fout


def _is_documentmanifest(naam: str) -> bool:
    klein = naam.lower()
    return klein.endswith(".doc.xml") or klein.endswith(".doc.fmx.xml")


def _onderdelen(data: bytes) -> tuple[ET.Element, list[tuple[str, ET.Element]], dict[str, ET.Element], set[str]]:
    """Lees één manifestatie, uitsluitend in de volgorde van het documentmanifest.

    Geeft `(manifest, onderdelen, inclusies)`. De omgekeerde controle is bewust:
    een extra XML-onderdeel dat niet in de inhoudsopgave staat mag niet stil
    buiten de omzetting blijven.

    Een **inclusie** wijst niet het manifest aan maar een onderdeel zelf
    (`BIB.INSTANCE/INCLUSIONS/INCL.ELEMENT TYPE="FORMEX.DOC"`): een geciteerde
    bijlage die een wijzigingshandeling in een andere handeling invoegt (de
    Digitale omnibus `32026R1744` voegt zo bijlage XIV aan de AI-verordening toe).
    Zij is geen documentonderdeel — haar plek is waar de tekst haar aanroept,
    binnen een `QUOT.S` — maar wel brontekst, en dus geen buitenstaander.

    Een inclusie van het type `TIFF` is een **afbeelding** (een formulier, een
    pictogram, een handtekening, een aankruisvakje als lijstteken). Die komt als
    vierde terug: de namen, zodat de omzetter een aanroep kan controleren.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise _xml_fout(f"de download is geen leesbare zip ({exc})", exc)
    with zf:
        xml_infos = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".xml")]
        korte_namen = [i.filename.rsplit("/", 1)[-1] for i in xml_infos]
        if len(korte_namen) != len(set(korte_namen)):
            raise _xml_fout("de zip bevat XML-bestanden met dezelfde korte naam")
        per_naam = dict(zip(korte_namen, xml_infos))
        doc_namen = [n for n in korte_namen if _is_documentmanifest(n)]
        if len(doc_namen) != 1:
            raise _xml_fout(
                f"de zip bevat {len(doc_namen)} documentmanifesten (.doc.xml of .doc.fmx.xml); "
                "precies één is vereist"
            )
        try:
            doc = ET.fromstring(zf.read(per_naam[doc_namen[0]]))
        except (ET.ParseError, KeyError) as exc:
            raise _xml_fout(f"het documentmanifest is niet leesbaar ({exc})", exc)
        toc_verwijzingen = {
            (r.get("FILE") or "").rsplit("/", 1)[-1]
            for r in doc.iter("PUBLICATION.REF") if r.get("FILE")
        }
        aanwezige_tocs = toc_verwijzingen & set(korte_namen)
        for toc_naam in aanwezige_tocs:
            try:
                toc = ET.fromstring(zf.read(per_naam[toc_naam]))
            except (ET.ParseError, KeyError) as exc:
                raise _xml_fout(f"de publicatie-inhoudsopgave {toc_naam} is niet leesbaar ({exc})", exc)
            terug = {
                (item.get("DOC.INSTANCE") or "").rsplit("/", 1)[-1]
                for item in toc.iter("ITEM.PUB") if item.get("DOC.INSTANCE")
            }
            if doc_namen[0] not in terug:
                raise _xml_fout(
                    f"de publicatie-inhoudsopgave {toc_naam} verwijst niet terug naar {doc_namen[0]}"
                )
        volgorde = [r.get("FILE") for r in doc.iter("REF.PHYS") if r.get("TYPE") == "DOC.XML"]
        if not volgorde or any(not n for n in volgorde):
            raise _xml_fout("het documentmanifest noemt geen geldige onderdelen (REF.PHYS TYPE=DOC.XML)")
        kort = [n.rsplit("/", 1)[-1] for n in volgorde]
        if len(kort) != len(set(kort)):
            raise _xml_fout("het documentmanifest noemt hetzelfde onderdeel meer dan één keer")
        werkelijk = set(korte_namen) - set(doc_namen) - aanwezige_tocs
        genoemd = set(kort)
        ontbreekt = genoemd - werkelijk
        if ontbreekt:
            raise _xml_fout(f"het documentmanifest noemt ontbrekende onderdelen: {', '.join(sorted(ontbreekt))}")
        uit = []
        for naam in kort:
            try:
                uit.append((naam, ET.fromstring(zf.read(per_naam[naam]))))
            except (ET.ParseError, KeyError) as exc:
                raise _xml_fout(f"onderdeel {naam} is niet leesbaar ({exc})", exc)
        inclusie_namen: list[str] = []
        afbeeldingen: set[str] = set()
        for _, root in uit:
            for incl in root.iterfind("BIB.INSTANCE/INCLUSIONS/INCL.ELEMENT"):
                soort = (incl.get("TYPE") or "").upper()
                if soort not in ("FORMEX.DOC", AFBEELDINGSTYPE):
                    raise _xml_fout(f"een inclusie heeft het onbekende type {incl.get('TYPE')!r}")
                naam = (incl.get("FILEREF") or "").rsplit("/", 1)[-1]
                if not naam:
                    raise _xml_fout("een inclusie (INCL.ELEMENT) noemt geen bestand")
                (afbeeldingen.add if soort == AFBEELDINGSTYPE else inclusie_namen.append)(naam)
        alle_namen = {i.filename.rsplit("/", 1)[-1] for i in zf.infolist() if not i.is_dir()}
        ontbrekende_afbeeldingen = afbeeldingen - alle_namen
        if ontbrekende_afbeeldingen:
            raise _xml_fout(
                f"de handeling noemt ontbrekende afbeeldingen: {', '.join(sorted(ontbrekende_afbeeldingen))}"
            )
        dubbel_genoemd = set(inclusie_namen) & genoemd
        if dubbel_genoemd:
            raise _xml_fout(
                f"een inclusie is tegelijk een documentonderdeel: {', '.join(sorted(dubbel_genoemd))}"
            )
        ontbrekende_inclusies = set(inclusie_namen) - werkelijk
        if ontbrekende_inclusies:
            raise _xml_fout(
                f"de handeling noemt ontbrekende inclusies: {', '.join(sorted(ontbrekende_inclusies))}"
            )
        extra = werkelijk - genoemd - set(inclusie_namen)
        if extra:
            raise _xml_fout(f"de zip bevat onderdelen buiten het documentmanifest: {', '.join(sorted(extra))}")
        inclusies: dict[str, ET.Element] = {}
        for naam in dict.fromkeys(inclusie_namen):
            try:
                inclusies[naam] = ET.fromstring(zf.read(per_naam[naam]))
            except (ET.ParseError, KeyError) as exc:
                raise _xml_fout(f"inclusie {naam} is niet leesbaar ({exc})", exc)
        return doc, uit, inclusies, afbeeldingen


class _PiVerzamelaar(ET.TreeBuilder):
    """Bewaart de `CLG.MDFO`-instructies die ElementTree normaal weggooit.

    De consolidatie zet om elke gewijzigde passage `<?CLG.MDFO ... ACTIVE.DOC="32025R0037" ...?>`
    en `<?CLG.MDFC ...?>`. Dat is de machineleesbare vorm van het ▼M-teken uit de
    HTML-route: de tekst zelf draagt geen pijl, de instructie noemt de wijzigende handeling.
    """

    def __init__(self) -> None:
        super().__init__()
        self.actief: Counter = Counter()

    def pi(self, target, data):
        if target == "CLG.MDFO":
            m = re.search(r'\bACTIVE\.DOC="([^"]*)"', data or "")
            if m:
                self.actief[m.group(1)] += 1


def _wijzigingsmarkeringen(data: bytes) -> Counter:
    """Per CELEX-nummer het aantal passages in alle onderdelen dat ermee is gemarkeerd.

    Ook de bijlagen tellen mee: een handeling die alleen een bijlage wijzigt,
    heeft haar markering in dat onderdeel en niet in de handeling zelf.
    """
    totaal: Counter = Counter()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                naam = info.filename.lower()
                if (info.is_dir() or not naam.endswith(".xml") or _is_documentmanifest(naam)
                        or naam.endswith(".toc.xml") or naam.endswith(".toc.fmx.xml")):
                    continue
                bouwer = _PiVerzamelaar()
                parser = ET.XMLParser(target=bouwer)
                parser.feed(zf.read(info))
                parser.close()
                totaal.update(bouwer.actief)
    except (zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise _xml_fout(f"de wijzigingsmarkeringen zijn niet te lezen ({exc})", exc)
    return totaal


def _plat_bron(el) -> str:
    """Platte zichtbare tekst, los van de omzetter die de Markdown bouwt."""
    if el.tag in METADATA:
        return ""
    uit = el.text or ""
    for kind in el:
        if kind.tag == "QUOT.START":
            stuk = "“"
        elif kind.tag == "QUOT.END":
            stuk = "”"
        elif kind.tag == "FT" and (kind.get("TYPE") or "").upper() == "NUMBER":
            cijfers = "".join(kind.itertext())
            if cijfers.isdigit() and len(cijfers) > 4:
                groepen = []
                while cijfers:
                    groepen.insert(0, cijfers[-3:])
                    cijfers = cijfers[:-3]
                stuk = NBSP.join(groepen)
            else:
                stuk = _plat_bron(kind)
        else:
            stuk = _plat_bron(kind)
        # Formex gebruikt elementgrenzen soms ook als woordgrens zonder een
        # letterlijke spatie in `.text` of `.tail` (bijvoorbeeld TITLE/TI gevolgd
        # door een datum). Voor woordbehoud moet die grens zichtbaar blijven.
        # Een `HT` is opmaak binnen de zin en geen grens: `cyberbeveiliging<HT
        # TYPE="BOLD">s</HT>certificering` is één woord, en de Markdown schrijft
        # het ook als één woord (zie `inline`).
        uit += stuk if kind.tag == "HT" else f" {stuk} "
        uit += kind.tail or ""
    return uit


def _woorden(tekst: str) -> list[str]:
    # Nootmarkers en overwegingnummers hebben dezelfde gedrukte vorm. Voor de
    # behoudscontrole mogen ze beide weg: de hiërarchiecontrole telt de
    # overwegingen apart, terwijl NOTE-nummers uit attributen worden opgebouwd.
    tekst = re.sub(r"\(\s*\d+\s*\)", " ", tekst)
    tekst = tekst.replace(NBSP, " ")
    vorige = None
    while vorige != tekst:
        vorige = tekst
        tekst = re.sub(r"(?<=\d)\s(?=\d{3}\b)", "", tekst)
    return re.findall(r"\w+", tekst.lower(), re.UNICODE)


def _bronwoorden(onderdelen: list[tuple[str, ET.Element]]) -> Counter:
    teller = Counter()
    for _, root in onderdelen:
        teller.update(_woorden(_plat_bron(root)))
        # Een samengevoegde broncel wordt in Markdown op elke bezette plek
        # herhaald. Voeg die herhalingen ook aan de onafhankelijke brontelling
        # toe, anders zou juist een correcte rowspan als verdubbeling gelden.
        for cel in root.iter("CELL"):
            try:
                herhalingen = int(cel.get("ROWSPAN") or 1) * int(cel.get("COLSPAN") or 1) - 1
            except ValueError as exc:
                raise _xml_fout("een tabelcel heeft een niet-numerieke span", exc)
            if herhalingen > 0:
                teller.update(_woorden(_plat_bron(cel)) * herhalingen)
    return teller


BLADALINEA_TAGS = {
    "P", "TXT", "TI", "STI", "TI.ART", "STI.ART", "DEFINITION", "TERM",
    "PREAMBLE.INIT", "PREAMBLE.FINAL", "GR.CONSID.INIT", "VISA", "COM",
}
BLADALINEA_BLOKKEN = {"LIST", "DLIST", "TBL", "NP", "ALINEA", "P"}


def _bladalineas(onderdelen: list[tuple[str, ET.Element]]) -> list[tuple[str, list[str]]]:
    """Tekstdragende bladregels die niet over een genest blok heen lopen."""
    uit = []

    def loop(naam: str, el) -> None:
        if el.tag in METADATA:
            return
        if el.tag in BLADALINEA_TAGS:
            if not any(kind is not el and kind.tag in BLADALINEA_BLOKKEN for kind in el.iter()):
                woorden = _woorden(_plat_bron(el))
                if woorden:
                    uit.append((naam, woorden))
        for kind in el:
            loop(naam, kind)

    for naam, root in onderdelen:
        loop(naam, root)
    return uit


def _celex_jaar_nummer(celex: str) -> tuple[int, int] | None:
    """`32019R0881` -> (2019, 881); alleen de vorm van een handeling uit sector 3."""
    m = re.fullmatch(r"3(\d{4})[A-Z]{1,2}(\d{4})", celex or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def basis_preambule(basis: bytes, base_celex: str | None) -> tuple[ET.Element | None, str | None]:
    """De `PREAMBLE` van de basishandeling achter een geconsolideerde tekst.

    Geeft `(element, None)`, of `(None, reden)` als de basishandeling geen
    bruikbare considerans heeft. Dat tweede is geen bronfout: een oudere
    handeling mag er geen hebben, en dan blijft de geconsolideerde tekst zonder,
    met die reden in de herkomst.

    Een zip die een **andere** handeling blijkt te zijn, wordt geweigerd. Dat is
    wél een fout: elke overweging van een verkeerde handeling zou stil onder een
    verordening komen te staan waar ze niet bij hoort. De identiteit komt uit de
    handeling zelf (`BIB.INSTANCE/NO.DOC`, jaar en volgnummer), niet uit de URL
    waarmee ze is opgehaald.
    """
    _, delen, _, _ = _onderdelen(basis)
    handelingen = [root for _, root in delen if root.tag == "ACT"]
    if len(handelingen) != 1:
        raise _xml_fout(f"de basishandeling bevat {len(handelingen)} handelingen (ACT); precies één is vereist")
    handeling = handelingen[0]
    verwacht = _celex_jaar_nummer(base_celex or "")
    nodoc = handeling.find("BIB.INSTANCE/NO.DOC")
    jaar = ws(nodoc.findtext("YEAR") or "") if nodoc is not None else ""
    nummer = ws(nodoc.findtext("NO.CURRENT") or "") if nodoc is not None else ""
    if verwacht is None or not (jaar.isdigit() and nummer.isdigit()):
        return None, "de identiteit van de basishandeling is niet uit de bron vast te stellen"
    if (int(jaar), int(nummer)) != verwacht:
        raise _xml_fout(
            f"de opgehaalde basishandeling is {nummer}/{jaar}, maar {base_celex} "
            f"({verwacht[1]}/{verwacht[0]}) werd gevraagd"
        )
    preambule = handeling.find("PREAMBLE")
    if preambule is None or next(preambule.iter("CONSID"), None) is None:
        return None, "de basishandeling heeft in Formex geen overwegingen"
    return preambule, None


class FormexOmzetter:
    def __init__(self) -> None:
        self.u = Uitvoer()
        self.noten: list[tuple[int, str]] = []      # wachtende definities van de huidige reeks
        self.nootnummer = 0
        self.nootlabels: dict[str, int] = {}
        self.herhaalde_cellen = 0
        self.bijlagen = 0
        self.metadata: dict = {}
        self.markeringen: Counter = Counter()      # CELEX -> passages met een CLG.MDFO
        self.divisies: Counter = Counter()         # anker -> keren dat een kop het draagt
        self.basis: ET.Element | None = None       # PREAMBLE van de basishandeling
        self.zonder: frozenset = frozenset()       # opmaaksoorten die nu niet geschreven worden
        self.inclusies: dict[str, ET.Element] = {}  # geciteerde bijlagen, op bestandsnaam
        self.gebruikte_inclusies: set[str] = set()
        self.afbeeldingen: set[str] = set()         # gedeclareerde TIFF-inclusies
        self.afbeeldingen_weggelaten: list[dict] = []

    def onbekend(self, context: str, el) -> None:
        """Weiger onbekende inhoud; een leeg technisch element mag verdwijnen."""
        if ws(" ".join(el.itertext())):
            self.u.markeer_onbekend(f"{context}:{el.tag}")

    @staticmethod
    def controleer_elementen(onderdelen: list[tuple[str, ET.Element]]) -> None:
        """Alleen expliciet gekende tekstcontainers mogen transparant doorlopen."""
        onbekend = Counter()

        def loop(el) -> None:
            if el.tag in METADATA:
                return
            if el.tag not in BEKENDE_TEKSTELEMENTEN and ws(" ".join(el.itertext())):
                onbekend[el.tag] += 1
                return
            for kind in el:
                loop(kind)

        for _, root in onderdelen:
            loop(root)
        if onbekend:
            opsomming = ", ".join(f"{tag} ({aantal}×)" for tag, aantal in sorted(onbekend.items()))
            raise ConversionError(
                f"Formex-element(en) met tekst zonder eigen behandeling: {opsomming}; "
                "omzetting geweigerd."
            )

    # ------------------------------------------------------------ inline

    def inline(self, el) -> str:
        delen = [el.text or ""]
        kinderen = list(el)
        for index, kind in enumerate(kinderen):
            vorige = next((d[-1] for d in reversed(delen) if d), "")
            volgende = (kind.tail or "")[:1]
            if not volgende and index + 1 < len(kinderen):
                volgende = (kinderen[index + 1].text or "")[:1]
            # Vet of cursief dat aan een woord vastzit staat midden in dat woord
            # (`cyberbeveiliging<HT TYPE="BOLD">s</HT>certificering`). Met
            # sterretjes ertussen valt het woord in twee tokens uiteen en vindt de
            # kennisbank het niet meer terug; de opmaak gaat dan liever verloren.
            vast = vorige.isalnum() or volgende.isalnum()
            delen.append(self.inline_el(kind, vast_aan_woord=vast))
            delen.append(kind.tail or "")
        return "".join(delen)

    def inline_el(self, el, vast_aan_woord: bool = False) -> str:
        tag = el.tag
        if tag == "NOTE":
            return self.noot(el)
        if tag == "QUOT.START":
            return "“"
        if tag == "QUOT.END":
            return "”"
        if tag == "HT":
            binnen = self.inline(el)
            soort = (el.get("TYPE") or "").upper()
            if not binnen.strip():
                return binnen
            if soort == "UC":          # het Publicatieblad zet dit in kapitalen
                return binnen.upper()
            if vast_aan_woord or soort in self.zonder:
                return binnen
            if soort == "ITALIC":
                return f"*{ws(binnen)}*"
            if soort == "BOLD":
                return f"**{ws(binnen)}**"
            return binnen
        if tag == "FT" and (el.get("TYPE") or "").upper() == "NUMBER":
            # Het Publicatieblad groepeert duizendtallen met een harde spatie;
            # de XML bewaart alleen de cijfers.
            cijfers = "".join(el.itertext())
            if cijfers.isdigit() and len(cijfers) > 4:
                groepen = []
                while cijfers:
                    groepen.insert(0, cijfers[-3:])
                    cijfers = cijfers[:-3]
                return NBSP.join(groepen)
            return self.inline(el)
        if tag in METADATA:
            return ""
        if tag == "NO.P":
            # Een NP binnen een geciteerde wijziging (QUOT.S) loopt hier inline
            # door; zonder scheiding stond `“67)Verordening` aaneen.
            return self.inline(el) + " "
        if tag in ("P", "ALINEA", "PARAG", "ARTICLE", "TI.ART", "STI.ART"):
            # De AI-verordening bevat vier ALINEA's en negen PARAG's binnen
            # QUOT.S; daar zijn het inline bladregels, net als P in oudere
            # handelingen. NO.PARAG blijft binnen dit citaat gewone tekst.
            # Een wijzigingshandeling citeert ook hele artikelen (zeven in
            # 32026R1744): kop, opschrift en leden lopen dan net zo inline
            # door, want een citaat krijgt geen eigen structuur of ankers.
            return " " + self.inline(el) + " "
        if tag in ("LIST", "ITEM"):
            # Een opsomming binnen QUOT.S citeert de structuur van een andere
            # handeling. De eigen planner mag daar geen onderdelen van maken,
            # maar de woorden moeten wel in documentvolgorde blijven staan.
            return " " + self.inline(el) + " "
        if tag in INLINE_TEKST or tag in INLINE_TRANSPARANT:
            return self.inline(el)
        if tag == "INCL.ELEMENT" and self.afbeelding(el):
            return ""
        if tag == "INCL.ELEMENT":
            # Leeg, dus `onbekend()` zou hem stil laten vallen — en daarmee een
            # hele bijlage. Alleen de kale vorm als eigen alinea is gemeten.
            raise _xml_fout(
                "een inclusie (INCL.ELEMENT) staat midden in een zin; alleen een "
                "inclusie als eigen alinea binnen QUOT.S is gemeten"
            )
        self.onbekend("inline", el)
        return ""

    def kop_tekst(self, el, *soorten: str) -> str:
        """Tekst van een kop of opschrift, zonder de genoemde opmaak (standaard alle).

        Het Publicatieblad zet de koppen van een geconsolideerde tekst cursief
        (`HOOFDSTUK II` als `<HT TYPE="ITALIC">`). De planner herkent een kop aan
        zijn kale vorm, dus `## *HOOFDSTUK II*` kreeg geen anker. Opmaak in een
        kop is typografie en geen inhoud; bij een opschrift blijft vet staan,
        zoals de planner dat van een Publicatiebladtekst gewend is.
        """
        eerder = self.zonder
        self.zonder = frozenset(soorten or ("ITALIC", "BOLD"))
        try:
            return ws(self.inline(el))
        finally:
            self.zonder = eerder

    def noot(self, el) -> str:
        """Een nootverwijzing; de definitie wacht op het einde van haar reeks."""
        sleutel = el.get("NOTE.REF") or el.get("NOTE.ID")
        nummer = self.nootlabels.get(sleutel) if sleutel else None
        if nummer is None:
            self.nootnummer += 1
            nummer = self.nootnummer
            if sleutel:
                self.nootlabels[sleutel] = nummer
        if el.get("NOTE.REF") is None and len(el):
            self.noten.append((nummer, ws(self.inline(el))))
        return f"{NBSP}({nummer})"

    def notenblok(self) -> None:
        """De wachtende definities, in de vorm `(1)` + twee harde spaties."""
        for nummer, tekst in sorted(self.noten):
            self.u.blok(f"({nummer}){NBSP}{NBSP}{tekst}")
        self.noten = []

    def nieuwe_nootreeks(self) -> None:
        self.nootnummer = 0
        self.nootlabels = {}

    # ------------------------------------------------------------ documenten

    def omzetten(self, data: bytes, basis: bytes | None = None) -> Uitvoer:
        doc, onderdelen, inclusies, afbeeldingen = _onderdelen(data)
        self.inclusies = inclusies
        self.afbeeldingen = afbeeldingen
        inclusiedelen = [(f"inclusie:{naam}", root) for naam, root in inclusies.items()]
        self.controleer_elementen(onderdelen + inclusiedelen)
        self.markeringen = _wijzigingsmarkeringen(data)
        if basis is not None:
            self.basis = self.kies_basis(onderdelen, basis)
        for index, (naam, root) in enumerate(onderdelen):
            if index:
                self.u.blok("---")
            if root.tag in ("ACT", "CONS.ACT"):
                self.kop_van_de_handeling(doc, root)
                self.handeling(root.find("CONS.DOC") if root.tag == "CONS.ACT" else root)
            elif root.tag in ("ANNEX", "CONS.ANNEX"):
                self.bijlage(root)
            else:
                self.onbekend("document", root)
        ongebruikt = set(inclusies) - self.gebruikte_inclusies
        if ongebruikt:
            # De woordcontrole zou dit ook vangen, maar dan als raadsel; hier
            # staat de reden: de tekst roept de inclusie nergens aan.
            raise _xml_fout(f"inclusie(s) nergens in de tekst aangeroepen: {', '.join(sorted(ongebruikt))}")
        if self.afbeeldingen_weggelaten:
            self.meld_afbeeldingen()
        if self.basis is not None:
            # De ingevoegde considerans is brontekst als elke andere: dezelfde
            # controles op verlies, verdubbeling en verweving gelden ook voor haar.
            onderdelen = onderdelen + [("basishandeling:PREAMBLE", self.basis)]
        self.zelfcontrole(onderdelen, inclusiedelen)
        return self.u

    def kies_basis(self, onderdelen: list[tuple[str, ET.Element]], basis: bytes) -> ET.Element:
        """De considerans van de basishandeling, voor een tekst die er zelf geen heeft."""
        acts = [root for _, root in onderdelen if root.tag == "CONS.ACT"]
        if len(acts) != 1:
            raise _xml_fout("een basishandeling is alleen zinvol bij één geconsolideerde handeling (CONS.ACT)")
        if next(acts[0].iter("CONSID"), None) is not None:
            raise _xml_fout("de geconsolideerde tekst heeft zelf al een considerans; er komt geen tweede bij")
        cons = acts[0].find("INFO.CONSLEG")
        base_celex = self.celex_uit_conslegref(cons.get("CONSLEG.REF", "") if cons is not None else "")
        preambule, reden = basis_preambule(basis, base_celex)
        if preambule is None:
            raise _xml_fout(reden or "de basishandeling levert geen considerans")
        self.controleer_elementen([("basishandeling", preambule)])
        return preambule

    def zelfcontrole(self, onderdelen: list[tuple[str, ET.Element]],
                     inclusies: list[tuple[str, ET.Element]] | None = None) -> None:
        """Bewijs binnen deze route dat tekst en structurele eenheden aankomen.

        Een inclusie (geciteerde bijlage) is brontekst en telt mee voor woorden en
        bladalinea's, maar niet voor de structuur: haar `ANNEX` is niet een
        bijlage van deze handeling, net zoals een `ARTICLE` binnen `QUOT.S` niet
        een artikel van deze handeling is.
        """
        tekstdelen = onderdelen + list(inclusies or [])
        markdown = self.u.markdown()
        controle_markdown = markdown
        if self.metadata.get("format") == "clg":
            # Deze referentieregel komt uit INFO.CONSLEG-attributen en is dus
            # herkomst, geen tekstnode uit de manifestatie.
            controle_markdown = controle_markdown.split("\n\n", 1)[-1]

        verwacht = _bronwoorden(tekstdelen)
        gekregen = Counter(_woorden(controle_markdown))
        if verwacht != gekregen:
            ontbreekt = list((verwacht - gekregen).elements())[:12]
            extra = list((gekregen - verwacht).elements())[:12]
            raise ConversionError(
                "Formex-tekstbehoud faalt (woordmultiset verschilt; "
                f"ontbreekt={ontbreekt or 'niets'}, extra={extra or 'niets'})."
            )

        alle_woorden = " " + " ".join(_woorden(controle_markdown)) + " "
        for naam, woorden in _bladalineas(tekstdelen):
            if f" {' '.join(woorden)} " not in alle_woorden:
                voorbeeld = " ".join(woorden[:14])
                raise ConversionError(
                    f"Formex-bladalinea uit {naam} staat niet aaneengesloten in de Markdown: "
                    f"{voorbeeld!r}."
                )

        telling = Counter(e.soort for e in self.u.eenheden)

        def eigen_artikelen(el, in_citaat: bool = False) -> int:
            """Tel alleen artikelen van de handeling, niet de zeven geciteerde uit 32026R1744."""
            citaat = in_citaat or el.tag == "QUOT.S"
            eigen = int(el.tag == "ARTICLE" and not citaat)
            return eigen + sum(eigen_artikelen(kind, citaat) for kind in el)

        artikelen = sum(eigen_artikelen(root) for _, root in onderdelen)

        def structurele_leden(el, in_citaat: bool = False) -> int:
            """Tel alleen leden van de handeling, niet negen geciteerde uit 2024/1689."""
            citaat = in_citaat or el.tag == "QUOT.S"
            eigen = int(
                el.tag == "PARAG" and not citaat and el.find("NO.PARAG") is not None
                and bool(ws(_plat_bron(el.find("NO.PARAG"))))
            )
            return eigen + sum(structurele_leden(kind, citaat) for kind in el)

        leden = sum(structurele_leden(root) for _, root in onderdelen)
        overwegingen = sum(1 for _, root in onderdelen for _ in root.iter("CONSID"))
        bijlagen = sum(
            1 for _, root in onderdelen for el in root.iter()
            if el.tag in ("ANNEX", "CONS.ANNEX")
        )
        verwacht_per_soort = {
            "artikel": artikelen,
            "lid": leden,
            "overweging": overwegingen,
            "bijlage": bijlagen,
        }
        fouten = [
            f"{soort}: bron {aantal}, Markdown {telling[soort]}"
            for soort, aantal in verwacht_per_soort.items()
            if telling[soort] != aantal
        ]
        artikelkoppen = sum(1 for regel in markdown.splitlines() if regel.startswith("### "))
        if artikelkoppen != artikelen:
            fouten.append(f"artikelkoppen: bron {artikelen}, Markdown {artikelkoppen}")
        ankers = [e.anker for e in self.u.eenheden if e.anker]
        dubbel = sorted(a for a, aantal in Counter(ankers).items() if aantal > 1)
        if dubbel:
            fouten.append(
                f"dubbele structurele ankers: {', '.join(dubbel[:8])} (de Formex-bron "
                "nummert op één niveau twee eenheden gelijk; alleen een herhaalde "
                "markering binnen één opsomming wordt met een volgnummer onderscheiden)"
            )
        if fouten:
            raise ConversionError("Formex-structuurcontrole faalt: " + "; ".join(fouten))

        for eenheid in self.u.eenheden:
            woorden = _woorden(eenheid.tekst)
            if woorden and f" {' '.join(woorden)} " not in alle_woorden:
                raise ConversionError(
                    f"Formex-{eenheid.soort} {eenheid.anker} is niet terug te vinden in de Markdown."
                )

    def kop_van_de_handeling(self, doc, root) -> None:
        """Masthead (Publicatieblad) of referentieregel (geconsolideerd)."""
        cons = root.find("INFO.CONSLEG")
        if cons is not None:
            ref, datum = cons.get("CONSLEG.REF", ""), cons.get("START.DATE", "")
            stand = f"{datum[6:8]}.{datum[4:6]}.{datum[0:4]}" if len(datum) == 8 else datum
            reeks = cons.get("PROD.SEQ", "")
            taal = (root.findtext("CONS.DOC/BIB.INSTANCE/LG.DOC") or "NL").upper()
            self.metadata = {"format": "clg", "base_celex": self.celex_uit_conslegref(ref),
                             "consolidation_date": self.iso(datum), "version": reeks,
                             "language": taal.lower(),
                             "valid_from": self.iso(datum),
                             "valid_until": self.iso(cons.get("END.DATE", "")) or None}
            document = root.find("CONS.DOC")
            oj_reference, meldingen = self.vindplaats_basis(document, self.metadata["base_celex"])
            amendments, meer = self.wijzigende_handelingen(document)
            self.metadata["oj_reference"] = oj_reference
            self.metadata["amendments"] = amendments
            self.metadata["waarschuwingen"] = meldingen + meer
            self.u.blok(f"{ref} — {taal} — {stand} — {reeks}")
            return
        pub = doc.find(".//PUBLICATION.REF")
        bib = root.find("BIB.INSTANCE")
        if pub is None:
            return
        coll = pub.findtext("COLL") or ""
        nummer = pub.findtext("NO.OJ") or ""
        taal = (pub.findtext("LG.OJ") or "NL").upper()
        iso = (pub.find("DATE").get("ISO") if pub.find("DATE") is not None else "") or ""
        bladzijde = (bib.findtext("PAGE.FIRST") if bib is not None else "") or ""
        self.metadata = {"format": "oj", "language": taal.lower(),
                         "oj_reference": self.pb_vindplaats(coll, nummer, iso, bladzijde)}
        # Geen mastheadtabel in de uitvoer: die is opmaak van de gedrukte
        # bladzijde, geen inhoud, en als pipe-tabel zou hij als inhoudstabel
        # meetellen in het bronbewijs. De vindplaats staat in het zijbestand
        # (`oj_reference`), waar `extract_meta.py` hem ook leest.
        self.u.blok("---")

    @staticmethod
    def pb_vindplaats(coll: str, nummer: str, iso: str, bladzijde: str) -> str:
        """`PB L 151 van 7.6.2019, blz. 15`: dezelfde vorm voor een handeling en een basishandeling."""
        datum = f"{int(iso[6:8])}.{int(iso[4:6])}.{iso[0:4]}" if len(iso) == 8 else iso
        return f"PB {coll} {nummer} van {datum}, blz. {bladzijde}"

    def vindplaats_basis(self, document, base_celex: str | None) -> tuple[str | None, list[str]]:
        """De vindplaats van de basishandeling uit `FAM.COMP/BIB.DATA/BIB.INSTANCE.CONS`.

        Geeft `(vindplaats, [])` of `(None, [reden])`. Ontbreekt een onderdeel, dan
        blijft de vindplaats leeg met een melding: een deel van een vindplaats is
        geen vindplaats. Noemt het blok een **andere** handeling dan die van de
        consolidatie, dan is dat een tegenspraak en weigert de omzetting.
        """
        data = document.find("FAM.COMP/BIB.DATA") if document is not None else None
        instantie = data.find("BIB.INSTANCE.CONS") if data is not None else None
        if instantie is None:
            return None, ["De geconsolideerde Formex noemt de vindplaats van de basishandeling niet "
                          "(FAM.COMP/BIB.DATA/BIB.INSTANCE.CONS ontbreekt); oj_reference is leeg."]
        genoemd = ws(data.findtext("NO.CELEX") or "")
        if base_celex and genoemd and genoemd != base_celex:
            raise _xml_fout(f"de vindplaats in FAM.COMP hoort bij {genoemd}, "
                            f"maar de consolidatie is van {base_celex}")
        nodoc = instantie.find("NO.DOC")
        verwacht = _celex_jaar_nummer(base_celex or "")
        if nodoc is not None and verwacht is not None:
            jaar, nummer = ws(nodoc.findtext("YEAR") or ""), ws(nodoc.findtext("NO.CURRENT") or "")
            if jaar.isdigit() and nummer.isdigit() and (int(jaar), int(nummer)) != verwacht:
                raise _xml_fout(f"de vindplaats in FAM.COMP hoort bij {nummer}/{jaar}, "
                                f"maar de consolidatie is van {base_celex}")
        ref = instantie.find("DOCUMENT.REF.CONS")
        datum = instantie.find("DATE")
        onderdelen = {
            "COLL": ws(ref.findtext("COLL") or "") if ref is not None else "",
            "NO.OJ": ws(ref.findtext("NO.OJ") or "") if ref is not None else "",
            "PAGE.FIRST": ws(ref.findtext("PAGE.FIRST") or "") if ref is not None else "",
            "DATE/@ISO": (datum.get("ISO") or "") if datum is not None else "",
        }
        ontbreekt = [naam for naam, waarde in onderdelen.items() if not waarde]
        if ontbreekt or not (len(onderdelen["DATE/@ISO"]) == 8 and onderdelen["DATE/@ISO"].isdigit()):
            return None, ["De vindplaats van de basishandeling in de geconsolideerde Formex is onvolledig "
                          f"({', '.join(ontbreekt) or 'datum onleesbaar'}); oj_reference is leeg."]
        return self.pb_vindplaats(onderdelen["COLL"], onderdelen["NO.OJ"],
                                  onderdelen["DATE/@ISO"], onderdelen["PAGE.FIRST"]), []

    @staticmethod
    def celex_van_wijziging(mod) -> tuple[str | None, str | None]:
        """Het CELEX-nummer van één wijzigende handeling, uit de bron en nooit geraden.

        Twee onafhankelijke wegen die het eens moeten zijn: `NO.CELEX`, zoals de
        bron het zelf noemt, en `3` + jaar + de letter van `LEG.VAL` + het
        vierciferige volgnummer. Ze spreken elkaar tegen: weigeren. Ontbreekt de ene,
        dan geldt de andere; ontbreken beide, dan `(None, reden)`.
        """
        data = mod.find("BIB.DATA")
        genoemd = ws(data.findtext("NO.CELEX") or "") if data is not None else ""
        nodoc = mod.find("BIB.DATA/BIB.INSTANCE.CONS/DOCUMENT.REF.CONS/NO.DOC")
        jaar = ws(nodoc.findtext("YEAR") or "") if nodoc is not None else ""
        nummer = ws(nodoc.findtext("NO.CURRENT") or "") if nodoc is not None else ""
        heeft_nummer = len(jaar) == 4 and jaar.isdigit() and nummer.isdigit() and 0 < int(nummer) < 10000
        letter = CELEX_LETTER.get(mod.get("LEG.VAL") or "")
        gebouwd = f"3{jaar}{letter}{int(nummer):04d}" if heeft_nummer and letter else None
        if genoemd:
            m = re.fullmatch(r"3(\d{4})[A-Z](\d{4})", genoemd)
            if not m:
                raise _xml_fout(f"NO.CELEX {genoemd!r} van een wijzigende handeling is geen CELEX-nummer")
            if heeft_nummer and (m.group(1), int(m.group(2))) != (jaar, int(nummer)):
                raise _xml_fout(f"NO.CELEX {genoemd} van een wijzigende handeling spreekt "
                                f"NO.DOC {nummer}/{jaar} tegen")
            if gebouwd and gebouwd != genoemd:
                raise _xml_fout(f"NO.CELEX {genoemd} van een wijzigende handeling spreekt "
                                f"{gebouwd} (LEG.VAL {mod.get('LEG.VAL')}) tegen")
            return genoemd, None
        if gebouwd:
            return gebouwd, None
        return None, ("geen NO.CELEX en het nummer of het soort (LEG.VAL="
                      f"{mod.get('LEG.VAL')!r}) is niet af te leiden")

    def wijzigende_handelingen(self, document) -> tuple[list[dict], list[str]]:
        """De wijzigende handelingen uit `FAM.COMP/GR.MOD.ACT`, in de vorm van het zijbestand.

        Elk element heeft `celex`; `shown` staat er alleen als de tekst zelf zegt dat
        een passage door deze handeling is gewijzigd (een `CLG.MDFO` met haar
        CELEX-nummer). Zo bepaalt ook de HTML-route `shown`, uit het pijlteken. Dat de
        markering *ontbreekt* is hier geen bewijs dat de wijziging is overschreven
        (`shown: false`): dat is voor Formex niet gemeten, dus dan blijft het veld weg.
        Wat niet als wijziging te lezen is, komt met een reden in de meldingen.
        """
        groep = document.find("FAM.COMP/GR.MOD.ACT") if document is not None else None
        if groep is None:
            return [], []
        lijst: list[dict] = []
        meldingen: list[str] = []
        gezien: set[str] = set()
        zonder_markering: list[str] = []
        for mod in groep:
            if mod.tag != "MOD.ACT":
                meldingen.append(f"GR.MOD.ACT bevat {mod.tag}; dat is niet gelezen.")
                continue
            celex, reden = self.celex_van_wijziging(mod)
            if mod.get("TYPE") != "MOD":
                meldingen.append(f"MOD.ACT {celex or '(zonder CELEX-nummer)'} heeft TYPE={mod.get('TYPE')!r}, "
                                 "geen wijziging; niet in amendments opgenomen.")
                continue
            if celex is None:
                meldingen.append(f"Een wijzigende handeling is niet in amendments opgenomen: {reden}.")
                continue
            if celex in gezien:
                meldingen.append(f"Wijzigende handeling {celex} staat meer dan eens in GR.MOD.ACT; eenmaal opgenomen.")
                continue
            gezien.add(celex)
            entry: dict = {"celex": celex}
            if self.markeringen.get(celex):
                entry["shown"] = True
            else:
                zonder_markering.append(celex)
            lijst.append(entry)
        if zonder_markering:
            meldingen.append(
                f"Geen CLG.MDFO in de tekst noemt {', '.join(zonder_markering)}; van deze wijzigende "
                "handeling(en) is `shown` niet vastgesteld."
            )
        onbekend = sorted(set(self.markeringen) - gezien)
        if onbekend:
            meldingen.append(
                f"CLG.MDFO in de tekst noemt {', '.join(onbekend)}, dat niet als wijziging in "
                "GR.MOD.ACT staat; amendments kan onvolledig zijn."
            )
        return lijst, meldingen

    @staticmethod
    def iso(datum: str) -> str | None:
        if len(datum) != 8 or not datum.isdigit() or datum.startswith("9999"):
            return None
        return f"{datum[0:4]}-{datum[4:6]}-{datum[6:8]}"

    @staticmethod
    def celex_uit_conslegref(ref: str) -> str | None:
        m = re.fullmatch(r"(\d{4})([A-Z]{1,2})(\d{4})", ref or "")
        return f"3{m.group(1)}{m.group(2)}{m.group(3)}" if m else None

    def handeling(self, act) -> None:
        for kind in act:
            tag = kind.tag
            if tag == "TITLE":
                for p in kind.iter("P"):
                    self.u.blok(ws(self.inline(p)))
            elif tag == "PREAMBLE":
                if self.basis is not None:
                    self.preambule_van_basis()
                else:
                    self.preambule(kind)
            elif tag == "ENACTING.TERMS":
                self.bepalingen(kind, pad={})
            elif tag == "FINAL":
                for sub in kind.iter("P"):
                    self.u.blok(ws(self.inline(sub)))
                self.notenblok()
            elif tag in ("ANNEX", "CONS.ANNEX"):
                self.u.blok("---")
                self.bijlage(kind)
            elif tag in METADATA:
                continue
            else:
                self.onbekend("act", kind)
        # Een geconsolideerde tekst heeft geen ondertekening; dan komt het
        # notenblok aan het eind van de handeling.
        self.notenblok()

    def preambule(self, el) -> None:
        for kind in el:
            if kind.tag in ("GR.VISA", "GR.CONSID"):
                self.preambule(kind)
            elif kind.tag == "CONSID":
                self.overweging(kind)
            elif kind.tag in METADATA:
                continue
            else:
                self.u.blok(ws(self.inline(kind)))

    def preambule_van_basis(self) -> None:
        """De considerans van de basishandeling, in de vorm die de HTML-route ook schrijft.

        Een geconsolideerde tekst heeft geen aanhef en geen overwegingen; die
        staan alleen in de handeling zelf. Zoals in het Publicatieblad-formaat
        van een geconsolideerde tekst komt eerst de aanhef met de overwegingen,
        dan de noten die erbij horen, en pas daarna de vaststellingsformule. De
        noten van de considerans zijn een eigen reeks: die van de wettekst
        beginnen daarna opnieuw bij (1), en `plan_structure` zoekt de grens op
        de formule.
        """
        preambule = self.basis
        formule = [k for k in preambule if k.tag == "PREAMBLE.FINAL"]
        voor = ET.Element(preambule.tag)
        voor.extend(k for k in preambule if k.tag != "PREAMBLE.FINAL")
        self.nieuwe_nootreeks()
        self.preambule(voor)
        self.notenblok()
        self.nieuwe_nootreeks()
        for kind in formule:
            self.u.blok(ws(self.inline(kind)))
        self.metadata["recitals_from"] = self.metadata.get("base_celex")

    def overweging(self, el) -> None:
        np = el.find("NP")
        doel = np if np is not None else el
        nr = ws(self.inline(doel.find("NO.P"))) if doel.find("NO.P") is not None else ""
        txt_el = doel.find("TXT")
        txt = ws(self.inline(txt_el)) if txt_el is not None else ws(self.inline(doel))
        regel = f"{nr} {txt}".strip()
        self.u.blok(regel)
        if nr:
            self.u.eenheid(f"rec-{nummer_anker(nr)}", "overweging", regel)
        for vervolg in doel:
            if vervolg.tag not in ("NO.P", "TXT"):
                self.inhoud(vervolg, basis="", teller={"lijsten": 0})

    # ------------------------------------------------------------ bepalingen

    def divisie_anker(self, kop: str, pad: dict) -> tuple[str, str | None, str]:
        # `HOOFDSTUK IX bis` (geconsolideerde SIS-verordening 02018R1862) is
        # een ander hoofdstuk dan IX; alleen `IX` lezen gaf twee keer `hfd-9`.
        m = re.match(r"(HOOFDSTUK|AFDELING|TITEL|DEEL|ONDERAFDELING)\s+(\S+)"
                     r"(?:\s+(" + "|".join(LATIJN) + r")\b)?", kop, re.I)
        if not m:
            return "", None, ""
        soort = m.group(1).upper()
        nr = nummer_anker(m.group(2), romeins_omrekenen=True) + (m.group(3) or "").lower()
        if soort == "TITEL":
            return f"tit-{nr}", "tit", nr
        if soort == "HOOFDSTUK":
            return (f"hfd-{pad['tit']}-{nr}" if "tit" in pad else f"hfd-{nr}"), "hfd", nr
        if soort == "AFDELING":
            ouder = pad.get("hfd") or pad.get("tit")
            return (f"afd-{ouder}-{nr}" if ouder else f"afd-{nr}"), "afd", nr
        if soort == "DEEL":
            return f"deel-{nr}", "deel", nr
        return "", None, nr

    def bepalingen(self, el, pad: dict) -> None:
        for kind in el:
            if kind.tag == "DIVISION":
                titel = kind.find("TITLE")
                ti = self.kop_tekst(titel.find("TI")) if titel is not None and titel.find("TI") is not None else ""
                sti = (self.kop_tekst(titel.find("STI"), "ITALIC")
                       if titel is not None and titel.find("STI") is not None else "")
                # Kop en opschrift op eigen regels, net als in het Publicatieblad:
                # het profiel voegt ze zelf samen (instructie `merge_next`).
                self.u.blok(f"## {ti}")
                if sti:
                    self.u.blok(sti)
                anker, sleutel, nr = self.divisie_anker(ti, pad)
                nieuw = dict(pad)
                if anker:
                    self.divisies[anker] += 1
                    if self.divisies[anker] > 1:
                        anker, nr = self.dubbele_divisie(anker, nr, self.divisies[anker], ti)
                    self.u.eenheid(anker, "divisie", f"{ti} {sti}".strip())
                    nieuw[sleutel] = nr
                wrapper = ET.Element("x")
                wrapper.extend([c for c in kind if c.tag != "TITLE"])
                self.bepalingen(wrapper, nieuw)
            elif kind.tag == "ARTICLE":
                self.artikel(kind)
            elif kind.tag in METADATA:
                continue
            else:
                self.onbekend("bepalingen", kind)

    def dubbele_divisie(self, anker: str, nr: str, volgnummer: int, kop: str) -> tuple[str, str]:
        """Een tweede hoofdstuk, afdeling of titel met hetzelfde nummer op hetzelfde niveau.

        De Nederlandse Formex van DORA (32022R2554) noemt hoofdstuk VII `HOOFDSTUK III`
        (artikelen 46 tot en met 56); het Publicatieblad heeft daar VII. Het is dezelfde
        soort bronfout als de dubbele `d)` in artikel 13 AVG, en krijgt dezelfde regel:
        het volgnummer wordt het onderscheid (`hfd-3-2`), de kop en de tekst blijven zoals
        de bron ze geeft, en de bronfout gaat als waarschuwing mee. Het volgnummer loopt
        door in het ankerpad, zodat een afdeling onder het tweede hoofdstuk niet botst
        met een afdeling onder het eerste. Een dubbel artikel blijft een weigering.
        """
        onderscheiden = f"{anker}-{volgnummer}"
        self.metadata.setdefault("waarschuwingen", []).append(
            f"De Formex-bron gebruikt de kop {kop} {volgnummer} keer; het {volgnummer}e kreeg "
            f"de structuureenheid {onderscheiden}. De kop en de tekst zijn ongewijzigd overgenomen."
        )
        return onderscheiden, f"{nr}-{volgnummer}"

    def artikel(self, el) -> None:
        ti = self.kop_tekst(el.find("TI.ART")) if el.find("TI.ART") is not None else "Artikel"
        sti = self.kop_tekst(el.find("STI.ART"), "ITALIC") if el.find("STI.ART") is not None else ""
        m = re.match(r"Artikel\s+(.+)$", ti, re.I)
        anker = f"art-{nummer_anker(m.group(1))}" if m else f"art-{int(el.get('IDENTIFIER', '0'))}"
        self.u.blok(f"### {ti.replace(' ', NBSP)}")
        if sti:
            self.u.blok(sti)
        self.u.eenheid(anker, "artikel", f"{ti} {sti}".strip())
        teller = {"lijsten": 0}
        for kind in el:
            if kind.tag in ("TI.ART", "STI.ART"):
                continue
            if kind.tag == "PARAG":
                nr = ws(self.inline(kind.find("NO.PARAG"))) if kind.find("NO.PARAG") is not None else ""
                ankernummer = nummer_anker(nr)
                identificatie = (kind.get("IDENTIFIER") or "").rsplit(".", 1)[-1]
                # In artikel 73 van 2024/1689 heet IDENTIFIER 073.010 in de
                # bron zichtbaar "11."; het volgende lid heet eveneens 11.
                # De machine-identiteit houdt beide bronpassages uniek zonder
                # het gedrukte nummer of de tekst te veranderen.
                if identificatie.isdigit() and nr.rstrip(".").isdigit():
                    ankernummer = str(int(identificatie))
                self.lid(kind, nr, f"{anker}-{ankernummer}" if nr else anker)
            else:
                self.inhoud(kind, basis=anker, teller=teller)

    def lid(self, el, nr: str, anker: str) -> None:
        """Een lid: `1.` plus drie harde spaties, daarna de tekst."""
        prefix = [f"{nr}{NBSP}{NBSP}{NBSP}" if nr else ""]
        geankerd = [False]
        teller = {"lijsten": 0}
        for kind in el:
            if kind.tag == "NO.PARAG":
                continue
            self.inhoud(kind, basis=anker, teller=teller, prefix=prefix,
                        lid_anker=anker, geankerd=geankerd)
        if prefix[0]:                      # een lid zonder tekst: alleen het nummer
            self.u.blok(prefix[0].rstrip())
            if not geankerd[0]:
                self.u.eenheid(anker, "lid", nr)

    def inhoud(self, el, basis: str, teller: dict, prefix=None, lid_anker=None, geankerd=None) -> None:
        def schrijf(tekst: str) -> None:
            if not tekst:
                return
            if isinstance(prefix, list) and prefix[0]:
                tekst = prefix[0] + tekst
                prefix[0] = ""
            self.u.blok(tekst)
            if lid_anker and geankerd is not None and not geankerd[0]:
                self.u.eenheid(lid_anker, "lid", tekst)
                geankerd[0] = True

        tag = el.tag
        if tag == "ALINEA":
            if not any(c.tag in ("LIST", "TBL", "P", "NP", "DLIST") for c in el):
                schrijf(ws(self.inline(el)))
                return
            tekst = el.text or ""
            for kind in el:
                if kind.tag in ("LIST", "TBL", "P", "NP", "DLIST"):
                    schrijf(ws(tekst))
                    tekst = ""
                    self.inhoud(kind, basis, teller, prefix, lid_anker, geankerd)
                    tekst = kind.tail or ""
                else:
                    tekst += self.inline_el(kind) + (kind.tail or "")
            schrijf(ws(tekst))
        elif tag == "P":
            inclusie = self._inclusie_in(el)
            if inclusie is not None:
                self.geciteerde_inclusie(inclusie)
            elif el.find("LIST") is not None or el.find("TBL") is not None or el.find("DLIST") is not None:
                kopie = ET.Element("ALINEA")
                kopie.text = el.text
                kopie.extend(list(el))
                self.inhoud(kopie, basis, teller, prefix, lid_anker, geankerd)
            else:
                schrijf(ws(self.inline(el)))
        elif tag == "NP":
            # Een los genummerd punt (bijlagen) draagt in het Publicatieblad
            # dezelfde vorm als een lid: nummer plus drie harde spaties.
            nr = ws(self.inline(el.find("NO.P"))) if el.find("NO.P") is not None else ""
            txt = ws(self.inline(el.find("TXT"))) if el.find("TXT") is not None else ""
            scheiding = NBSP * 3 if re.fullmatch(r"\d{1,3}\.", nr) else " "
            schrijf(f"{nr}{scheiding}{txt}".strip() if nr else txt)
            anker = f"{basis}-{nummer_anker(nr)}" if basis and nr else ""
            if anker:
                # Een tweede reeks losse punten in hetzelfde blok draagt `al<k>`,
                # de afspraak van het profiel (patronen.md: bijlage XI van de
                # Schengengrenscode telt twee keer 1. tot en met 8.). Bijlage I.A
                # van de SCC's nummert de gegevensexporteurs en daarna de
                # -importeurs elk vanaf 1.; zonder onderscheid weigerde de
                # zelfcontrole op dubbele ankers.
                gezien = teller.setdefault("punten", set())
                if anker in gezien:
                    teller["reeks"] = teller.get("reeks", 1) + 1
                    gezien.clear()
                gezien.add(anker)
                if teller.get("reeks", 1) > 1:
                    anker = f"{basis}-al{teller['reeks']}-{nummer_anker(nr)}"
                self.u.eenheid(anker, "punt", f"{nr} {txt}")
            for kind in el:
                if kind.tag not in ("NO.P", "TXT"):
                    self.inhoud(kind, anker or basis, {"lijsten": 0})
        elif tag in ("LIST", "DLIST"):
            # Een DLIST draagt zijn nummer in de PREFIX (`16)`) en is dus altijd
            # genummerd. Tot 22 september 2026 stond hier `tag == "LIST" and ...`,
            # waardoor een DLIST nooit een basis meekreeg en geen van de 26
            # definitiepunten van artikel 4 AVG een structuureenheid had. Een
            # DLIST telt mee als opsomming in het lid: artikel 28 bis, lid 2 van
            # de geconsolideerde AVMD (02010L0013) heeft een LIST a)–b) en daarna
            # een DLIST a)–c), en die tweede reeks is `al2-`, zoals een tweede LIST.
            genummerd = tag == "DLIST" or el.get("TYPE", "").upper() not in ONGENUMMERD
            if genummerd:
                teller["lijsten"] += 1
            extra = f"al{teller['lijsten']}-" if genummerd and teller["lijsten"] > 1 else ""
            if isinstance(prefix, list) and prefix[0]:
                # Een lid dat meteen met een opsomming begint: het nummer krijgt
                # een eigen regel, zoals het Publicatieblad het zet.
                schrijf(prefix[0].rstrip())
            self.lijst(el, basis if genummerd else "", extra)
        elif tag == "TBL":
            self.tabel(el)
        elif tag == "INCL.ELEMENT":
            # Leeg, dus `onbekend()` zou hem stil laten vallen.
            if not self.afbeelding(el, blok=True):
                raise _xml_fout("een inclusie (INCL.ELEMENT) als los blok is alleen voor een afbeelding gemeten")
        elif tag in METADATA:
            return
        else:
            self.onbekend("inhoud", el)

    @staticmethod
    def _inclusie_in(p) -> ET.Element | None:
        """Het `INCL.ELEMENT` van een `P` die niets anders is dan `QUOT.S` eromheen.

        Alleen die kale vorm (`<P><QUOT.S><INCL.ELEMENT/></QUOT.S></P>`, punt 43
        van 32026R1744) is gemeten. Een inclusie met tekst ernaast valt door naar
        de gewone inline-weg en wordt daar geweigerd.
        """
        if ws(p.text or "") or len(p) != 1:
            return None
        quot = p[0]
        if quot.tag != "QUOT.S" or ws(quot.tail or "") or ws(quot.text or "") or len(quot) != 1:
            return None
        incl = quot[0]
        if incl.tag != "INCL.ELEMENT" or ws(incl.tail or ""):
            return None
        if (incl.get("TYPE") or "").upper() != "FORMEX.DOC":
            return None
        return incl

    def afbeelding(self, incl, blok: bool = False) -> bool:
        """Een TIFF-inclusie: niet overnemen, wel vastleggen. False als het geen afbeelding is.

        Een afbeelding heeft geen tekst, dus de woordcontrole ziet het verschil niet;
        de melding in de herkomst en de lijst `afbeeldingen_weggelaten` zorgen dat het
        nooit stil gebeurt. Dezelfde afspraak als bij de rechtspraakroute. Tot 23
        september 2026 weigerde een TIFF-inclusie het hele document, en daarmee 14
        van de 347 documenten in de meetlat: de verordening productaansprakelijkheid
        voor medische hulpmiddelen (2017/745 en 746, de CE-markering), Brussel I bis
        (formulieren), e-evidence (2023/1543, 200 aankruisvakjes als lijstteken), het
        adequaatheidsbesluit voor de VS (2023/1795, handtekeningen), ...
        """
        if (incl.get("TYPE") or "").upper() != AFBEELDINGSTYPE:
            return False
        naam = (incl.get("FILEREF") or "").rsplit("/", 1)[-1]
        if naam not in self.afbeeldingen:
            raise _xml_fout(f"de tekst roept afbeelding {naam or '?'} aan, maar de handeling noemt haar niet")
        inhoud = incl.find("IMG.CNT")
        met_tekst = inhoud is not None and bool(ws(" ".join(inhoud.itertext())))
        self.afbeeldingen_weggelaten.append({"fileref": naam, "format": AFBEELDINGSTYPE,
                                             "tekst_overgenomen": met_tekst})
        if met_tekst:
            # IMG.CNT is de tekst van het beeld (een formulier van Brussel I bis,
            # van de beschermingsbevelrichtlijn 2011/99). Die is brontekst en
            # komt als alinea's mee, zonder eenheden: het is een formulier.
            if not blok:
                raise _xml_fout("een afbeelding met tekst (IMG.CNT) midden in een zin is niet gemeten")
            for kind in inhoud:
                self.inhoud(kind, basis="", teller={"lijsten": 0})
        return True

    def meld_afbeeldingen(self) -> None:
        weg = self.afbeeldingen_weggelaten
        bestanden = list(dict.fromkeys(b["fileref"] for b in weg))
        voorbeeld = ", ".join(bestanden[:5]) + (f" en {len(bestanden) - 5} meer" if len(bestanden) > 5 else "")
        soort = "afbeelding" if len(weg) == 1 else "afbeeldingen"
        met_tekst = sum(1 for b in weg if b["tekst_overgenomen"])
        tekst = (f"; de tekst die de bron bij {met_tekst} ervan meelevert (IMG.CNT) is wel overgenomen"
                 if met_tekst else "")
        self.metadata.setdefault("waarschuwingen", []).append(
            f"{len(weg)} {soort} uit de Formex-bron niet overgenomen (TIFF){tekst}; de tekst "
            f"eromheen staat er wel: {voorbeeld}.")
        self.metadata["afbeeldingen_weggelaten"] = weg

    def geciteerde_inclusie(self, incl) -> None:
        """Een geciteerde bijlage, als blok op de plek waar de tekst haar aanroept.

        Inline kan niet: de bijlage draagt `GR.SEQ` en tabellen, en een tabel
        bestaat alleen als blok. Structuur krijgt ze niet — geen `##`-kop, geen
        eenheid — want het is tekst van een ándere handeling; de kennisbank haalt
        die uit de geconsolideerde versie. De nootreeks loopt gewoon door: dit is
        geen eigen bijlage.
        """
        naam = (incl.get("FILEREF") or "").rsplit("/", 1)[-1]
        root = self.inclusies.get(naam)
        if root is None:
            raise _xml_fout(f"de tekst roept inclusie {naam or '?'} aan, maar de handeling noemt haar niet")
        if naam in self.gebruikte_inclusies:
            raise _xml_fout(f"inclusie {naam} wordt meer dan één keer aangeroepen")
        self.gebruikte_inclusies.add(naam)
        if root.tag != "ANNEX":
            raise _xml_fout(f"inclusie {naam} is geen bijlage (ANNEX) maar {root.tag}")
        titel = root.find("TITLE")
        if titel is not None:
            if titel.find("TI") is not None:
                ti = self.kop_tekst(titel.find("TI"))
                if ti:
                    self.u.blok(ti)
            if titel.find("STI") is not None:
                sti = self.kop_tekst(titel.find("STI"), "ITALIC")
                if sti:
                    self.u.blok(sti)
        inhoud = root.find("CONTENTS")
        if inhoud is not None:
            self.bijlage_inhoud(inhoud, "", geciteerd=True)

    def definitiepunt(self, item, basis: str, extra: str = "") -> None:
        """Eén `DLIST.ITEM`: `16) “hoofdvestiging” …` als eigen alinea.

        Draagt de `DEFINITION` zelf een opsomming of een tabel, dan blijft die
        een opsomming: de kopregel loopt tot het eerste structurele kind en de
        onderdelen hangen daaronder. Tot 22 september 2026 ging `DEFINITION`
        altijd door `inline()`, waardoor artikel 4 AVG punt 16, 22 en 23 en
        artikel 3 LED punt 7 als één alinea werden geschreven; de planner zag
        `a)` en `b)` dan als onderdeel van het artikel in plaats van van het
        punt, en het bronbewijs vond de samengevoegde regel niet terug.
        """
        term = ws(self.inline(item.find("TERM"))) if item.find("TERM") is not None else ""
        prefix = ws(self.inline(item.find("PREFIX"))) if item.find("PREFIX") is not None else ""
        definitie = item.find("DEFINITION")
        anker = f"{basis}-{extra}{nummer_anker(prefix)}" if basis and prefix else ""

        def schrijf_kop(aanhef: str) -> None:
            regel = " ".join(x for x in (prefix, term, aanhef) if x)
            self.u.blok(regel)
            if anker:
                self.u.eenheid(anker, "onderdeel", regel)

        structureel = ("LIST", "DLIST", "TBL")
        if definitie is None or not any(k.tag in structureel for k in definitie):
            schrijf_kop(ws(self.inline(definitie)) if definitie is not None else "")
            return

        # Eén doorloop, zodat tekst ná een opsomming niet stil wegvalt: alles
        # tot het eerste structurele kind hoort bij de kopregel, wat erna komt
        # wordt een eigen alinea zonder eigen anker (zoals ALINEA het doet).
        lopend, kop_geschreven = definitie.text or "", False
        for kind in list(definitie) + [None]:
            if kind is not None and kind.tag not in structureel:
                lopend += self.inline_el(kind) + (kind.tail or "")
                continue
            tekst, lopend = ws(lopend), ""
            if not kop_geschreven:
                schrijf_kop(tekst)
                kop_geschreven = True
            elif tekst:
                self.u.blok(tekst)
            if kind is None:
                break
            if kind.tag == "TBL":
                self.tabel(kind)
            else:
                self.lijst(kind, anker or basis, "")
            lopend = kind.tail or ""

    def lijst(self, el, basis: str, extra: str) -> None:
        """Elk onderdeel is een eigen alinea: `a) tekst`, niet een Markdown-lijst."""
        if el.tag == "DLIST":
            for item in el.findall("DLIST.ITEM"):
                self.definitiepunt(item, basis, extra)
            return
        genummerd = el.get("TYPE", "").upper() not in ONGENUMMERD
        gezien: Counter = Counter()
        for item in el.findall("ITEM"):
            np = item.find("NP")
            if np is not None:
                nr = ws(self.inline(np.find("NO.P"))) if np.find("NO.P") is not None else ""
                txt_el = np.find("TXT")
                txt = ws(self.inline(txt_el)) if txt_el is not None else ""
                binnen = [c for c in np if c.tag not in ("NO.P", "TXT")]
            else:
                nr = ""
                eerste = item.find("P")
                txt = ws(self.inline(eerste)) if eerste is not None and eerste.find("LIST") is None else ""
                binnen = [c for c in item if c is not eerste or not txt]
            anker = f"{basis}-{extra}{nummer_anker(nr)}" if (basis and genummerd and nr) else ""
            if anker:
                gezien[anker] += 1
                if gezien[anker] > 1:
                    anker = self.dubbele_markering(anker, gezien[anker], nr, basis)
            regel = f"{nr} {txt}".strip() if genummerd or nr else f"— {txt}".strip()
            if regel:
                self.u.blok(regel)
            if anker:
                self.u.eenheid(anker, "onderdeel", regel)
            for sub in binnen:
                if sub.tag in ("LIST", "DLIST"):
                    self.lijst(sub, anker, "")
                else:
                    self.inhoud(sub, anker or basis, {"lijsten": 0})

    def dubbele_markering(self, anker: str, volgnummer: int, nr: str, basis: str) -> str:
        """Een tweede onderdeel met dezelfde gedrukte markering binnen één opsomming.

        De Nederlandse Formex van de AVG (32016R0679, `L_2016119NL.01000101.xml`)
        nummert in artikel 13, lid 1 de onderdelen a), b), c), d), d), e) waar het
        Publicatieblad a) t/m f) heeft; de Engelse manifestatie heeft wél (a)–(f).
        Een `ITEM`/`NP` draagt geen IDENTIFIER (0 van 558 in die bron), dus de
        machine-identiteit die artikel 73 van 2024/1689 uniek houdt bestaat hier
        niet. Wat de bron wél geeft is de volgorde: het tweede d) is het tweede
        d). Dat volgnummer wordt het onderscheid (`art-13-1-d-2`); de gedrukte
        markering en de tekst blijven zoals ze zijn — de omzetter corrigeert de
        bron niet tot e), want dat zou raden zijn. De melding gaat als
        waarschuwing mee in de herkomst, zodat dit nooit stil gebeurt. Botst het
        volgnummer alsnog met een genest punt (`d) … 2.`), dan vangt de
        zelfcontrole dat als dubbel anker en weigert de omzetting."""
        onderscheiden = f"{anker}-{volgnummer}"
        self.metadata.setdefault("waarschuwingen", []).append(
            f"De Formex-bron gebruikt in {_beschrijf_basis(basis)} de markering {nr} "
            f"{volgnummer} keer; het {volgnummer}e onderdeel {nr} kreeg de structuureenheid "
            f"{onderscheiden}. De gedrukte markering en de tekst zijn ongewijzigd overgenomen."
        )
        return onderscheiden

    # ------------------------------------------------------------ tabellen

    def cel_tekst(self, cel) -> str:
        """De tekst van een cel. Blokken (lijst, punt, alinea) staan gescheiden door
        een spatie; wat inline in de tekst staat, zoals een aanhalingsteken of
        opmaak, sluit aan zonder scheiding. Eerst stond overal een spatie tussen,
        en dat gaf `“ smart home ” -apparaat`."""
        delen: list[str] = []
        lopend: list[str] = [cel.text or ""]

        def sluit() -> None:
            tekst = ws("".join(lopend))
            lopend.clear()
            if tekst:
                delen.append(tekst)

        for kind in cel:
            if kind.tag == "LIST":
                sluit()
                genummerd = kind.get("TYPE", "").upper() not in ONGENUMMERD
                for item in kind.findall("ITEM"):
                    np = item.find("NP")
                    if np is not None:
                        nr = ws(self.inline(np.find("NO.P"))) if np.find("NO.P") is not None else ""
                        txt = ws(self.inline(np.find("TXT"))) if np.find("TXT") is not None else ""
                        delen.append(f"{nr} {txt}".strip())
                    else:
                        delen.append(("" if genummerd else "- ") + ws(self.inline(item)))
            elif kind.tag == "NP":
                sluit()
                nr = ws(self.inline(kind.find("NO.P"))) if kind.find("NO.P") is not None else ""
                txt = ws(self.inline(kind.find("TXT"))) if kind.find("TXT") is not None else ""
                delen.append(f"{nr} {txt}".strip())
            elif kind.tag in ("P", "ALINEA"):
                sluit()
                delen.append(self.cel_tekst(kind))
            else:
                lopend.append(self.inline_el(kind))
            lopend.append(kind.tail or "")
        sluit()
        return ws(" ".join(d for d in delen if d))

    def tabel(self, el) -> None:
        if any(kind is not el for kind in el.iter("TBL")):
            raise ConversionError(
                "Geneste inhoudstabel vereist afzonderlijke broncontrole; omzetting geweigerd."
            )
        # De titel van een tabel (`TBL/TITLE`) is brontekst. Hij bleef weg, en
        # daarmee weigerde de woordcontrole 12 van de 347 documenten in de meetlat
        # op één woord: "CONCORDANTIETABEL" (23 september 2026). Een losse alinea
        # boven de tabel is de vorm die de raw van NIS 2 al heeft, waar hetzelfde
        # woord als opschrift van bijlage III binnenkomt.
        titel = el.find("TITLE")
        if titel is not None:
            for deel in titel:
                tekst = self.kop_tekst(deel)
                if tekst:
                    self.u.blok(tekst)
        gr = el.find("GR.NOTES")
        definities = list(gr.findall("NOTE")) if gr is not None else []
        rijen, koprijen = [], 0
        for row in el.iter("ROW"):
            cellen = []
            for cel in row.findall("CELL"):
                kol = int(cel.get("COL")) - 1 if cel.get("COL") else None
                cellen.append({"tekst": self.cel_tekst(cel), "kol": kol,
                               "colspan": int(cel.get("COLSPAN") or 1),
                               "rowspan": int(cel.get("ROWSPAN") or 1)})
            if row.get("TYPE") == "HEADER" and len(rijen) == koprijen:
                koprijen += 1
            rijen.append(cellen)
        if not rijen:
            return
        # De kopregel van het Publicatieblad staat in raw als eerste body-rij,
        # met een lege kopregel erboven; zo levert de HTML-route hem ook.
        breedte = max((int(c.get("colspan", 1)) + (c["kol"] or 0) for rij in rijen for c in rij), default=1)
        leeg = [[{"tekst": "", "kol": i} for i in range(breedte)]]
        md, herhaald = tabel_markdown(leeg + rijen, 1)
        self.herhaalde_cellen += herhaald
        self.u.blok(md)
        for noot in definities:
            sleutel = noot.get("NOTE.ID")
            nummer = self.nootlabels.get(sleutel)
            if nummer is None:
                self.nootnummer += 1
                nummer = self.nootlabels[sleutel] = self.nootnummer
            self.noten.append((nummer, ws(self.inline(noot))))

    # ------------------------------------------------------------ bijlagen

    def bijlage(self, root) -> None:
        self.bijlagen += 1
        self.nieuwe_nootreeks()      # de tabelnoten van een bijlage tellen opnieuw
        titel = root.find("TITLE")
        ti = self.kop_tekst(titel.find("TI")) if titel is not None and titel.find("TI") is not None else "BIJLAGE"
        sti = (self.kop_tekst(titel.find("STI"), "ITALIC")
               if titel is not None and titel.find("STI") is not None else "")
        m = re.match(r"BIJLAGE\s+(\S+)", ti, re.I)
        # Een ongenummerde bijlage krijgt haar volgnummer met een `o` ervoor. Als
        # `annex-<volgnummer>` botste ze met een genummerde: het SCC-besluit
        # (2021/914) heeft een `BIJLAGE`, een `AANHANGSEL` en daarna `BIJLAGE I`
        # en `II`, en weigerde op "dubbele structurele ankers: annex-1".
        anker = (f"annex-{nummer_anker(m.group(1), romeins_omrekenen=True)}" if m
                 else f"annex-o{self.bijlagen}")
        self.u.blok(f"## {ti.replace(' ', NBSP)}")
        if sti:
            self.u.blok(sti)
        self.u.eenheid(anker, "bijlage", f"{ti} {sti}".strip())
        inhoud = root.find("CONTENTS")
        if inhoud is not None:
            self.bijlage_inhoud(inhoud, anker)
        self.notenblok()

    def bijlage_inhoud(self, el, anker: str, geciteerd: bool = False) -> None:
        """`geciteerd`: een ingesloten bijlage van een andere handeling — geen eenheden."""
        teller = {"lijsten": 0}
        onderdelen = [k for k in el if k.tag == "GR.SEQ"]
        for kind in el:
            if kind.tag == "GR.SEQ":
                titel = kind.find("TITLE")
                # De kop van een bijlageonderdeel staat als NP: het letterteken
                # in NO.P, de tekst in TXT. Zonder de scheiding ertussen leest
                # `A.“Algemeen”` niet als onderdeel (RE_ANNEX_PART in het profiel).
                np = titel.find(".//NP") if titel is not None else None
                if np is not None and np.find("NO.P") is not None:
                    letter = ws(self.inline(np.find("NO.P")))
                    rest = ws(self.inline(np.find("TXT"))) if np.find("TXT") is not None else ""
                    ti = f"{letter}{NBSP * 3}{rest}".strip()
                    kop = self.kop_tekst(np)
                else:
                    ti = ws(self.inline(titel)) if titel is not None else ""
                    # Het nummer staat in de eerste P: `Deel II` en het opschrift
                    # `VERBODSBEPALINGEN` zijn aparte P's, en aan elkaar
                    # (`Deel IIVERBODSBEPALINGEN`) is het nummer niet meer te lezen.
                    # Zonder opmaak: `*Bepaling 8*` (cursief in de SCC's) leest anders
                    # niet als nummer.
                    eerste = titel.find(".//P") if titel is not None else None
                    kop = self.kop_tekst(eerste) if eerste is not None else ti
                m = ONDERDEELKOP.match(kop)
                if geciteerd:
                    sub = anker
                elif m:
                    sub = f"{anker}-{nummer_anker(m.group(1))}"
                elif len(onderdelen) > 1:
                    # Een ongenummerd onderdeel naast andere onderdelen krijgt zijn
                    # plaats als anker. Liep het transparant door, dan kregen de
                    # punten 1., 2. … van twee zulke onderdelen hetzelfde anker.
                    sub = f"{anker}-s{onderdelen.index(kind) + 1}"
                else:
                    sub = anker
                if ti:
                    self.u.blok(ti)
                    if m and not geciteerd:
                        self.u.eenheid(sub, "bijlagedeel", ti)
                wrapper = ET.Element("x")
                wrapper.extend([c for c in kind if c.tag != "TITLE"])
                self.bijlage_inhoud(wrapper, sub, geciteerd)
            elif kind.tag == "QUOT.S":
                # Een bijlage die (een deel van) een bijlage van een andere
                # handeling vervangt, citeert die als blok: een tabel, een
                # onderdeel of alinea's binnen QUOT.S (16 van de 347 documenten
                # in de meetlat, meest wijzigingsrichtlijnen). Zoals bij een
                # ingesloten bijlage: wel brontekst, geen eenheden.
                self.bijlage_inhoud(kind, "", geciteerd=True)
            elif kind.tag in METADATA:
                continue
            else:
                self.inhoud(kind, basis=anker, teller=teller)


def _beschrijf_basis(basis: str) -> str:
    """`art-13-1` -> 'artikel 13, lid 1'; een andere vorm blijft het anker zelf."""
    m = re.fullmatch(r"art-([a-z0-9]+)(?:-([a-z0-9]+))?", basis)
    if not m:
        return basis
    return f"artikel {m.group(1)}" + (f", lid {m.group(2)}" if m.group(2) else "")


def omzetten(data: bytes, basis: bytes | None = None) -> tuple[str, list, dict, dict]:
    """Zet één ongewijzigde Cellar-zip om naar de EUR-Lex-raw-vorm.

    `basis` is de Cellar-zip van de basishandeling achter een geconsolideerde
    tekst; alleen haar considerans komt mee, vóór de bepalingen.
    """
    o = FormexOmzetter()
    u = o.omzetten(data, basis)
    return u.markdown(), u.eenheden, u.onbekend, {"herhaalde_cellen": o.herhaalde_cellen,
                                                  "metadata": o.metadata}
