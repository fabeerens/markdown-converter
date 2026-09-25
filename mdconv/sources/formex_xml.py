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
# Markeert in inline tekst de plek van een geciteerde tabel (zie `geciteerde_tabel`).
# U+0000 kan in XML niet voorkomen, dus nooit in brontekst.
TABELMARKER = "\x00"
ONGENUMMERD = {"DASH", "NDASH", "BULLET", "NONE", "DISC"}
METADATA = {"BIB.INSTANCE", "BIB.DOC", "BIB.DATA", "PUBLICATION.REF", "NO.DOC", "INFO.CONSLEG",
            "INFO.PROD", "FAM.COMP", "GR.MOD.ACT", "DOCUMENT.REF", "PAGE.FIRST", "PAGE.LAST",
            "PAGE.SEQ", "PAGE.TOTAL", "LG.DOC", "NO.SEQ", "VOLUME.REF",
            # De paginaverwijzing in een regel van een inhoudsopgave (`TOC.ITEM/ITEM.REF`,
            # `38` achter `1) Persoonsinformatie die bijzondere zorg vereist`): het
            # bladzijdenummer van het Publicatieblad, net als PAGE.FIRST. De
            # adequaatheidsbesluiten voor Japan (32019D0419) en Korea (32022D0254)
            # weigerden erop (T1-F16, kb WP-20).
            "ITEM.REF"}
INLINE_TEKST = {"DATE", "REF.DOC.OJ", "FT", "HT", "QUOT.S", "IE", "PERIOD", "REF.DOC", "ACRONYM",
                "ADDR", "PL.DATE", "NO.CELEX", "UNIT", "EXPONENT", "INF", "SUP", "TERM", "DEFINITION",
                # De ELI-verwijzing die het Publicatieblad sinds 2026 achter elke
                # REF.DOC.OJ in een noot zet; de zichtbare tekst is de URI zelf.
                "LINK",
                # Kanttekst, het eerste kind van een alinea; `inline_el` zet er een
                # spatie achter.
                "MARGIN"}
INLINE_TRANSPARANT = {"TI", "STI", "NP", "NO.P", "NO.PARAG", "TXT", "ITEM", "PREFIX"}
STRUCTUUR_ELEMENTEN = {
    "ACT", "CONS.ACT", "CONS.DOC", "ANNEX", "CONS.ANNEX", "TITLE", "PREAMBLE",
    "GR.VISA", "GR.CONSID", "CONSID", "ENACTING.TERMS", "FINAL", "DIVISION",
    "ARTICLE", "PARAG", "NO.PARAG", "ALINEA", "P", "LIST", "DLIST", "DLIST.ITEM",
    "ITEM", "NP", "NO.P", "TXT", "TBL", "CORPUS", "ROW", "CELL", "GR.NOTES",
    "NOTE", "CONTENTS", "GR.SEQ", "TI", "STI", "TI.ART", "STI.ART", "PREFIX",
    "TERM", "DEFINITION", "PREAMBLE.INIT", "PREAMBLE.FINAL", "GR.CONSID.INIT",
    "VISA", "SIGNATORY", "SIGNATURE", "COM",
    # Het nummer van een bijlageonderdeel zonder kop; alleen `bijlage_inhoud`
    # schrijft het, overal elders blijft het een weigering.
    "NO.GR.SEQ",
    # Een inhoudsopgave in een bijlage; alleen `inhoudsopgave()` schrijft haar.
    "TOC", "TOC.BLK", "TOC.ITEM", "NO.ITEM", "ITEM.CONT",
    # Een afbeelding (TIFF-inclusie) kan haar tekst meedragen: het formulier van
    # Brussel I bis staat als P's in IMG.CNT. Wat daarbinnen staat, moet zelf
    # bekend zijn; een FORMULA blijft dus een weigering. Een bijschrift (CAPTION,
    # `(stempel van de organisatie)` bij de handtekeningvakken van de SCC's van
    # 2010, 32010D0087) is brontekst en komt op de plek van het beeld (`afbeelding()`).
    "INCL.ELEMENT", "IMG.CNT", "CAPTION",
    # Een annotatie is een noot die geen voetnoot is: een NB, een opmerking,
    # een technische noot (`annotatie()`).
    "GR.ANNOTATION", "ANNOTATION",
    # Een groep tabelrijen met haar titel (`tabel()`, `rijgroeptitel()`).
    "BLK", "TI.BLK",
    # Een groep overwegingen onder een kop (`1. INLEIDING`), genest tot zes
    # niveaus diep in het adequaatheidsbesluit voor het VK (2021/1772). Alleen
    # `overwegingengroep()` behandelt haar; elders blijft ze een weigering.
    "DIV.CONSID",
    # Een adresblok: P's, één per regel van het adres. Als blok (de brieven in de
    # bijlagen van 2023/1795) en in een tabelcel (de scheepsrecyclinginrichtingen
    # van 2020/1675); midden in een zin blijft het een weigering.
    "ADDR.S",
    # Een groep tabellen onder één titel; zie `tabelgroep()`.
    "GR.TBL",
    # Een verklaring of samenvatting naast of in plaats van de handeling; zie `algemeen()`.
    "GENERAL", "PROLOG",
    # Een brief in een bijlage; zie `brief()`.
    "LETTER",
}
ANNOTATIES = ("GR.ANNOTATION", "ANNOTATION")
# De letter van een CELEX-nummer volgens het soort handeling (`LEG.VAL`). Wat hier
# niet in staat, is niet af te leiden en moet dan uit `NO.CELEX` komen.
CELEX_LETTER = {"REG": "R", "DIR": "L", "DEC": "D"}
BEKENDE_TEKSTELEMENTEN = METADATA | INLINE_TEKST | INLINE_TRANSPARANT | STRUCTUUR_ELEMENTEN
# Een formule staat bewust in geen van de verzamelingen hierboven, en haar
# onderdelen (EXPR, IND, OP.MATH, OP.CMP, FRACTION, OVER, SUM, ROOT, ...) ook niet.
# Gemeten op 23 september 2026: 372 formules in zes documenten van de meetlat (220
# in de geconsolideerde CRR, 02013R0575-20270101), waarvan 307 met een index
# (`PD<IND>pp</IND>`, `EL<IND>BE</IND>`). Een index heeft geen lineaire vorm die de
# woordcontrole haalt en leesbaar blijft: `PD_pp` en `PDₚₚ` zijn één woord, een
# hoofdletterindex bestaat in Unicode niet, `<sub>` voegt woorden toe en `PD~pp~`
# is in GitHub-Markdown doorgehaalde tekst, in een wettekst het teken voor
# geschrapt. Operatoren zijn lege elementen (`<OP.MATH TYPE="MINUS"/>`) en geen
# woorden, dus of een minteken, breukstreep of haakje goed in de Markdown staat, ziet
# de woordcontrole niet: liep een formule transparant door, dan werd `Risk −
# weighted exposure amount` (artikel 153 CRR) `Risk weighted exposure amount`, en
# de zelfcontrole slaagde. En de breuk in 32005L0066 (`1<OVER/><EXPR>t2 - t1
# </EXPR> ∫ … adt`) zegt niet waar de noemer eindigt. Een half leesbare formule is
# erger dan een weigering.
FORMULE_ELEMENTEN = {"FORMULA", "FORMULA.S"}
# Het enige afbeeldingstype dat in de meetlat voorkomt (257 inclusies in 14
# documenten, 23 september 2026). Een ander type blijft een weigering.
AFBEELDINGSTYPE = "TIFF"
# Inline elementen die geen woordgrens zijn: wat de bron eraan vastschrijft, is
# één woord (zie `_plat_bron`).
AANEEN_IN_BRON = {"HT", "DATE", "FT"}
# De aanhalingstekens om een geciteerde inclusie, naar hun `CODE` (het Unicode-
# codepunt). Gemeten om de inclusies in de meetlat: 201E en 201C openen, 201D
# sluit. Een andere code is een weigering: een verkeerd teken is een andere tekst.
AANHALING = {"201E": "„", "201C": "“", "201D": "”"}
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
    # Een romeins nummer met deelnummers (`IV.1`, `V.2`: de onderdelen van de bijlagen
    # IV en V bij de EUCC-verordening 2024/482) staat vóór de losse letter, anders
    # leest `V.1` als letter V en krijgen V.1 en V.2 hetzelfde anker (T1-F16, kb WP-20).
    r"Prioritair\s+gebied)\s+)?([IVXLC]+(?:\.\d+)+|[A-Z]|[IVXLC]+|\d+(?:\.\d+)*)(?:\.|\b)", re.I)
# Het nummer van een bijlageonderdeel zonder kop (`GR.SEQ/NO.GR.SEQ`), in de vormen
# die de meetlat kent: 902 keer in 12 documenten (23 september 2026), `1.` (136),
# `1.1.` (567), `1.1.1.` (180), `2)` (17, rijbewijsrichtlijn 2025/2205) en `d)` (2,
# 32012L0027). Een andere vorm is niet gemeten: `(1)` zou in het profiel als
# overweging of noot lezen.
ONDERDEELNUMMER = re.compile(r"\d{1,3}(?:\.\d{1,3})*\.|\d{1,3}\)|[a-z]\)")


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
            # Een geconsolideerde tekst draagt haar BIB.INSTANCE een niveau dieper,
            # in CONS.DOC: daar noemt 02012R1215-20150226 zijn zeven certificaten
            # (TIFF), en 02013R0575-20270101 er 167. Wie alleen onder de wortel
            # zocht, weigerde met "de handeling noemt haar niet", terwijl ze dat wel doet.
            for incl in [*root.iterfind("BIB.INSTANCE/INCLUSIONS/INCL.ELEMENT"),
                         *root.iterfind("CONS.DOC/BIB.INSTANCE/INCLUSIONS/INCL.ELEMENT")]:
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
        if kind.tag == "NOTE" and kind.get("NOTE.REF") is not None:
            # Een verwijzing naar een eerdere noot draagt in de bron soms die
            # noottekst nog eens (MiFIR 600/2014, artikel 53, punt 3), maar de
            # druk zet de noot één keer, met twee verwijzingen, en zo doet de
            # omzetter het ook. `noot()` weigert als de herhaling afwijkt.
            uit += " " + (kind.tail or "")
            continue
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
        # het ook als één woord (zie `inline`). Een datum of getal (`DATE`, `FT`)
        # evenmin: de bron schrijft `27 april 2016</DATE>betreffende` (de noten
        # van 2024/1183, en daarmee de geconsolideerde eIDAS), `19 augustus
        # 2015</DATE>inzake` (32021L2167) en `8,9</FT>Z-MA4` (32007L0011), en de
        # authentieke PDF drukt ze net zo aaneen. De omzetter schrijft wat de
        # bron zegt en meldt het (`meld_aaneen`); met een grens hier weigerde de
        # woordcontrole die getrouwe tekst. Staat er in de bron wél een spatie,
        # dan staat die in `.tail` en telt ze gewoon mee.
        uit += stuk if kind.tag in AANEEN_IN_BRON else f" {stuk} "
        uit += kind.tail or ""
    return uit


def _vast_na_noot(root) -> dict[int, str]:
    """De nootverwijzingen waar de bron het volgende woord zonder witruimte achter schrijft.

    `Stored Communications Act<NOTE …/>voor rechtshandhavingsdoeleinden`
    (32023D1795, overweging 96) en `<CELL><NOTE NOTE.REF="E0007"/><FT
    TYPE="CN">ex07115900</FT>` (32016R2390). Een NOTE is voor
    `_plat_bron` een woordgrens (hij staat niet in AANEEN_IN_BRON), dus volgens de
    eigen woordcontrole zijn dat twee woorden; `noot()` schrijft ze dan ook als
    twee. Zonder scheiding stond in raw `Act (165)voor`, en de planner van de
    kennisbank leest een marker alleen met witruimte of een leesteken erachter
    (WP-08, klasse D). Gemeten op 24 september 2026 in de 282 cachezips buiten de
    holdout: 727 keer in de tekst, in twee documenten (725 in 32016R2390, 2 in
    32023D1795); daarnaast drie tabelnoten in een GR.NOTES met een annotatie erna
    (32019R0089, 32023R0173), die niet door `noot()` gaan en dus niets veranderen.
    Het volgende kind telt alleen als de staart leeg is, en een tweede NOTE niet:
    die begint zelf met een harde spatie.
    """
    uit: dict[int, str] = {}
    for ouder in root.iter():
        kinderen = list(ouder)
        for index, kind in enumerate(kinderen):
            if kind.tag != "NOTE":
                continue
            erna = kind.tail or ""
            if not erna and index + 1 < len(kinderen) and kinderen[index + 1].tag != "NOTE":
                erna = "".join(kinderen[index + 1].itertext())
            woord = re.match(r"\w+", erna)
            if woord:
                uit[id(kind)] = woord.group(0)
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
        # De titel van een groep tabelrijen overspant COL.START tot en met
        # COL.END, precies zoals een cel met COLSPAN (`rijgroeptitel`).
        for kop in root.iter("TI.BLK"):
            start, eind = kop.get("COL.START") or "", kop.get("COL.END") or ""
            if start.isdigit() and eind.isdigit() and int(eind) > int(start):
                teller.update(_woorden(_plat_bron(kop)) * (int(eind) - int(start)))
    return teller


def _definitie_delen(definitie, structureel) -> list[tuple[str, ET.Element | None]]:
    """`(tekst ervoor, kind)`-paren van een DEFINITION, met een `P` die een opsomming
    draagt opengevouwen; het laatste paar heeft `None` als kind.

    Artikel 2, punt 2, van 2019/1150 zet de opsomming niet direct in de DEFINITION
    maar in een `P` daarbinnen (`DEFINITION > [tekst, P > LIST]`). Via `inline()` werd
    die `P` één regel met a), b) en c) erin, en de kennisbank weigerde terecht: haar
    lezer ziet de onderdelen wel (T1-F6, kb WP-20). Alleen een `P` die zelf een
    opsomming of tabel draagt wordt opengevouwen; een gewone `P` blijft inline.
    """
    delen: list[tuple[str, ET.Element | None]] = []
    lopend = definitie.text or ""
    for kind in definitie:
        if kind.tag == "P" and any(k.tag in structureel for k in kind):
            lopend += kind.text or ""
            for sub in kind:
                delen.append((lopend, sub))
                lopend = sub.tail or ""
            lopend += kind.tail or ""
        else:
            delen.append((lopend, kind))
            lopend = kind.tail or ""
    delen.append((lopend, None))
    return delen


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
        self.nootinhoud: dict[str, list[str]] = {}  # nootsleutel -> woorden van de definitie
        self.aaneen: dict[int, str] = {}            # id(element) -> aaneengeschreven woord
        self.noot_erna: dict[int, str] = {}         # id(NOTE) -> woord dat de bron eraan vastschrijft
        self.vast_na_noot: dict[int, str] = {}      # id(NOTE) -> gescheiden woord (voor de melding)
        self.herhaalde_cellen = 0
        self.bijlagen = 0
        self.metadata: dict = {}
        self.markeringen: Counter = Counter()      # CELEX -> passages met een CLG.MDFO
        self.divisies: Counter = Counter()         # anker -> keren dat een kop het draagt
        self.basis: ET.Element | None = None       # PREAMBLE van de basishandeling
        self.zonder: frozenset = frozenset()       # opmaaksoorten die nu niet geschreven worden
        self.inclusies: dict[str, ET.Element] = {}  # geciteerde bijlagen, op bestandsnaam
        self.gebruikte_inclusies: set[str] = set()
        self.nootruimte = ""                        # voorvoegsel van een nootsleutel binnen een inclusie
        self.afbeeldingen: set[str] = set()         # gedeclareerde TIFF-inclusies
        self.afbeeldingen_weggelaten: list[dict] = []
        self.citaatdiepte = 0                       # >0 binnen een QUOT.S die inline loopt
        self.citaattabellen: list[list[str] | None] = []  # blokken per TABELMARKER, None = geschreven

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
            uitleg = (" Een formule wordt bewust niet omgezet: een index, breuk of operator heeft "
                      "geen tekstvorm die leesbaar blijft en door de woordcontrole te bewijzen is."
                      if onbekend.keys() & FORMULE_ELEMENTEN else "")
            raise ConversionError(
                f"Formex-element(en) met tekst zonder eigen behandeling: {opsomming}; "
                "omzetting geweigerd." + uitleg
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
            stuk = self.inline_el(kind, vast_aan_woord=vast)
            erna = (kind.tail or "") or (kinderen[index + 1].text or "" if index + 1 < len(kinderen) else "")
            self.let_op_aaneen(kind, stuk, "".join(delen), erna)
            delen.append(stuk)
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
        if tag == "MARGIN":
            # De gemeenschappelijke militaire lijst (32017L0433) zet de categorie
            # in de kantlijn van 22 alinea's (`ML1`) en 38 begripskoppen (`ML 7,
            # 22.`). De HTML-route schrijft haar vóór de alinea met een `<br/>`;
            # hier staat ze vooraan op dezelfde regel. Zonder de spatie plakte ze
            # aan het eerste woord (`ML1Wapens`), want MARGIN heeft geen staart.
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
        if tag == "QUOT.S":
            self.citaatdiepte += 1
            try:
                return self.inline(el)
            finally:
                self.citaatdiepte -= 1
        if self.citaatdiepte and tag in ("DIVISION", "GR.SEQ", "DLIST"):
            # Een wijzigingshandeling citeert ook een hele afdeling met haar
            # artikelen (eIDAS 2, 32024R1183: zes keer, van AFDELING 1 met de
            # artikelen 5 bis tot en met 5 septies tot HOOFDSTUK IV BIS), een
            # definitielijst (de interoperabiliteitsverordening 32019R0817: zes
            # keer) of een bijlageonderdeel (32023L2673, 32025R0038). Net als een
            # geciteerd artikel (32026R1744) loopt dat inline door: een `##`-kop
            # of een eigen alinea per punt las de kennisbank als structuur van
            # déze handeling. Buiten een citaat blijft het een weigering, want
            # daar zou een eenheid van de handeling zelf stil in een alinea opgaan.
            return " " + self.inline(el) + " "
        if self.citaatdiepte and tag == "TITLE":
            # De kop van zo'n geciteerde afdeling. Opmaak in een kop is
            # typografie (zie `kop_tekst`); in CRD VI (32024L1619) stond anders
            # `*AFDELING I* ***Algemene bepalingen***` midden in de alinea.
            return " " + self.kop_tekst(el) + " "
        if self.citaatdiepte and tag == "TBL":
            return self.geciteerde_tabel(el)
        if self.citaatdiepte and tag == "NP":
            # Een geciteerd bijlageonderdeel zet zijn punten als losse NP's
            # zonder witruimte ertussen (`…voor cyberbeveiliging</TXT></NP><NP>
            # <NO.P>3.2.</NO.P>`, 32025R0038); transparant liep dat aaneen tot
            # `cyberbeveiliging3.2.`, en in 2018/1724 tot `lid 1.12. Verordening`.
            # Binnen een ITEM gaf het ITEM de spatie al. Alleen ervóór: een
            # spatie erna zette het leesteken achter het citaat los (`/oj).” ;`).
            return " " + self.inline(el)
        if self.citaatdiepte and tag == "DLIST.ITEM":
            # PREFIX, TERM en DEFINITION staan in de bron zonder witruimte tegen
            # elkaar (`j)„levende verzwakte vaccins”vaccins die …`, 32012L0005);
            # `definitiepunt()` zet er buiten een citaat ook een spatie tussen.
            delen = [el.text or ""] + [self.inline_el(kind) + (kind.tail or "") for kind in el]
            return " " + " ".join(ws(deel) for deel in delen if ws(deel)) + " "
        if tag in INLINE_TEKST or tag in INLINE_TRANSPARANT:
            return self.inline(el)
        if tag == "INCL.ELEMENT":
            bijschrift = self.afbeelding(el)
            if bijschrift is not None:
                return f" {bijschrift} " if bijschrift else ""
            # Leeg, dus `onbekend()` zou hem stil laten vallen — en daarmee een
            # hele bijlage. Alleen een inclusie als eigen alinea is gemeten.
            raise _xml_fout(
                "een inclusie (INCL.ELEMENT) staat midden in een zin; alleen een "
                "inclusie als eigen alinea, met hooguit haar aanhalingstekens, is gemeten"
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
        if sleutel:
            sleutel = self.nootruimte + sleutel
        nummer = self.nootlabels.get(sleutel) if sleutel else None
        if nummer is None:
            self.nootnummer += 1
            nummer = self.nootnummer
            if sleutel:
                self.nootlabels[sleutel] = nummer
        if el.get("NOTE.REF") is None and len(el):
            self.noten.append((nummer, ws(self.inline(el))))
            if sleutel:
                self.nootinhoud[sleutel] = _woorden(_plat_bron(el))
        elif el.get("NOTE.REF") is not None and ws(" ".join(el.itertext())):
            # Een verwijzing die de tekst van haar noot herhaalt (zie `_plat_bron`):
            # geen tweede definitie, maar alleen als de herhaling woord voor woord
            # de noot is waarnaar ze verwijst. Een andere tekst, of een noot die
            # hier nog niet bekend is, is niet te bewijzen en weigert.
            if self.nootinhoud.get(sleutel) != _woorden(_plat_bron(el)):
                raise _xml_fout(
                    f"een nootverwijzing (NOTE.REF {el.get('NOTE.REF')}) draagt tekst die niet gelijk "
                    "is aan de noot waarnaar ze verwijst")
        if id(el) in self.noot_erna:
            # Zie `_vast_na_noot`: de bron leest hier twee woorden.
            self.vast_na_noot[id(el)] = self.noot_erna[id(el)]
            return f"{NBSP}({nummer}) "
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
        for _, root in onderdelen + inclusiedelen:
            self.noot_erna.update(_vast_na_noot(root))
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
            elif root.tag == "GENERAL":
                if not index:
                    # Het hele document is een GENERAL (32006D0857): dan draagt
                    # het de vindplaats, net als een handeling.
                    self.kop_van_de_handeling(doc, root)
                self.algemeen(root)
            else:
                self.onbekend("document", root)
        ongebruikt = set(inclusies) - self.gebruikte_inclusies
        if ongebruikt:
            # De woordcontrole zou dit ook vangen, maar dan als raadsel; hier
            # staat de reden: de tekst roept de inclusie nergens aan.
            raise _xml_fout(f"inclusie(s) nergens in de tekst aangeroepen: {', '.join(sorted(ongebruikt))}")
        if any(blokken is not None for blokken in self.citaattabellen):
            # De marker kwam in tekst terecht die niet door `schrijf()` in
            # `inhoud()` gaat (een cel, een definitie, een overweging): daar kan
            # geen tabel staan, en de tabel zelf is dan nergens geschreven.
            raise _xml_fout("een geciteerde tabel staat op een plek waar alleen tekst kan staan")
        if self.afbeeldingen_weggelaten:
            self.meld_afbeeldingen()
        if self.aaneen:
            self.meld_aaneen()
        if self.vast_na_noot:
            self.meld_vast_na_noot()
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
        self.noot_erna.update(_vast_na_noot(preambule))
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
                # De noten van de wettekst horen bij de wettekst. Een geconsolideerde
                # handeling heeft geen FINAL, dus zonder dit blok bleven ze wachten tot
                # het einde van de eerste bijlage en kwamen ze daar, hernummerd door
                # `nieuwe_nootreeks()`, tussen de bijlagenoten terecht (T1-F3, kb
                # WP-20: 02010L0013, 02015L2366 en 02018L1972 stonden daarop in review/).
                self.notenblok()
                self.u.blok("---")
                self.bijlage(kind)
            elif tag == "GR.ANNOTATION":
                # `ONTWERP` boven de titel van een ontwerpaanbeveling die als
                # tweede handeling bij een besluit hoort (32015D0926); de
                # HTML-route zet het als gewone alinea vóór de titel.
                self.annotatie(kind)
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
            elif kind.tag == "DIV.CONSID":
                self.overwegingengroep(kind)
            elif kind.tag in METADATA:
                continue
            else:
                self.u.blok(ws(self.inline(kind)))

    def overwegingengroep(self, el) -> None:
        """Een groep overwegingen onder een kop (`DIV.CONSID`), in documentvolgorde.

        Adequaatheids-, staatssteun- en antidumpingbesluiten delen hun considerans
        in: `1. INLEIDING`, `2.1. Toepassingsgebied`, tot zes niveaus diep in het
        besluit voor het VK (2021/1772). De overwegingen lopen daar gewoon door,
        (1) tot en met (292), en blijven dus overwegingen: `(n)` plus één spatie,
        met hun `rec-`-eenheid. Tot 23 september 2026 weigerde de omzetter elk
        document met zo'n groep, 9 van de 347 in de meetlat, waaronder het Data
        Privacy Framework (2023/1795) uit de eindtest.

        De kop is een gewone alinea, geen `##`: in de considerans plant het
        profiel alleen overwegingen (`plan_recitals`), en `plan_body` kent daar
        alleen deel-, titel-, hoofdstuk- en afdelingskoppen. Een andere kop zou
        er een kopniveau zonder anker zijn. Wat hier niet als kop, overweging of
        groep gemeten is, weigert.
        """
        for kind in el:
            if kind.tag == "TITLE":
                self.overwegingenkop(kind)
            elif kind.tag == "CONSID":
                self.overweging(kind)
            elif kind.tag == "DIV.CONSID":
                self.overwegingengroep(kind)
            elif kind.tag in METADATA:
                continue
            else:
                self.onbekend("overwegingen", kind)

    def overwegingenkop(self, titel) -> None:
        """De kop van een groep overwegingen: `1.` plus drie harde spaties, dan de tekst.

        Dezelfde vorm als de kop van een bijlageonderdeel (`A.   “Algemeen”`):
        het nummer uit `NO.P`, drie harde spaties, de tekst uit `TXT`. Met een
        gewone spatie leest Markdown `1. INLEIDING` als lijstitem. Vet en cursief
        (156 en 82 van de 330 koppen in de meetlat) zijn typografie, net als in
        een hoofdstukkop. Vijf koppen hebben geen nummer maar een `P`
        (`Referentiestelsel` in 2017/2116); die komen er kaal in.
        """
        for ti in titel:
            if ti.tag != "TI":
                self.onbekend("overwegingenkop", ti)
                continue
            # Eén doorloop door `kop_tekst`, zodat de tekst in bronvolgorde staat
            # en niets wegvalt; alleen de scheiding achter het nummer wordt de
            # vorm van het Publicatieblad. Het nummer wordt gelezen zonder
            # `inline()`, want een tweede doorloop zou een noot twee keer tellen.
            regel = self.kop_tekst(ti)
            np = ti.find("NP")
            nr = ws("".join(np.find("NO.P").itertext())) if np is not None and np.find("NO.P") is not None else ""
            if nr and regel.startswith(f"{nr} "):
                regel = f"{nr}{NBSP * 3}{regel[len(nr) + 1:]}"
            if regel:
                self.u.blok(regel)

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
        elif txt_el is None:
            # Eén enkele overweging is ongenummerd: `<CONSID><P>…</P></CONSID>`
            # zonder NP (32011R1042, de dagelijkse forfaitaire invoerwaarden).
            # `inline(doel)` heeft dan alle kinderen al geschreven; ze hieronder
            # nog eens als vervolg schrijven gaf de overweging twee keer. Het is
            # wél een overweging van de bron, maar zonder nummer heeft ze geen
            # anker (het profiel herkent een overweging aan haar nummer).
            self.u.eenheid("", "overweging", regel)
            return
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
            # Het Europees wetboek voor elektronische communicatie (32018L1972)
            # begint in elk DEEL opnieuw bij TITEL I; zonder het deel gaf dat twee
            # keer `tit-1` en `hfd-1-1`. Het profiel doet hetzelfde (`tit-3-1`).
            nr = f"{pad['deel']}-{nr}" if "deel" in pad else nr
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

    def artikel(self, el, ouder: str = "") -> None:
        """`ouder`: het anker van de bijlage waarin het artikel staat (`annex-o1-art-3`)."""
        ti = self.kop_tekst(el.find("TI.ART")) if el.find("TI.ART") is not None else "Artikel"
        sti = self.kop_tekst(el.find("STI.ART"), "ITALIC") if el.find("STI.ART") is not None else ""
        m = re.match(r"Artikel\s+(.+)$", ti, re.I)
        anker = f"art-{nummer_anker(m.group(1))}" if m else f"art-{int(el.get('IDENTIFIER', '0'))}"
        if ouder:
            anker = f"{ouder}-{anker}"
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
            if TABELMARKER in tekst:
                # Een geciteerde tabel (zie `geciteerde_tabel`): de tekst ervóór,
                # de tabel als blok op haar plek, en de tekst erna als alinea.
                for index, deel in enumerate(tekst.split(TABELMARKER)):
                    if index % 2 == 0:
                        schrijf(ws(deel))
                        continue
                    if isinstance(prefix, list) and prefix[0]:
                        # Het lidnummer hoort op de eerste regel tekst; een lid
                        # dat met een geciteerde tabel begint, is niet gemeten.
                        raise _xml_fout("een lid dat met een geciteerde tabel begint, is niet gemeten")
                    for blok in self.citaattabellen[int(deel)]:
                        self.u.blok(blok)
                    self.citaattabellen[int(deel)] = None
                return
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
            if not any(c.tag in ("LIST", "TBL", "GR.TBL", "P", "NP", "DLIST") + ANNOTATIES for c in el):
                schrijf(ws(self.inline(el)))
                return
            tekst = el.text or ""
            for kind in el:
                if kind.tag in ("LIST", "TBL", "GR.TBL", "P", "NP", "DLIST") + ANNOTATIES:
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
                self.geciteerde_inclusies(*inclusie)
            elif (len(el) == 1 and el[0].tag == "ADDR.S" and not ws(el.text or "")
                  and not ws(el[0].tail or "")):
                # Een adresblok als enige inhoud van een P: de brieven in de bijlagen
                # van het Privacyschildbesluit (32016D1250) zetten het adres van de
                # geadresseerde zo onder de datum (`PL.DATE/P/ADDR.S`). Het blok is
                # dan de alinea; met tekst ernaast blijft het een weigering.
                self.inhoud(el[0], basis, teller, prefix, lid_anker, geankerd)
            elif (el.find("LIST") is not None or el.find("TBL") is not None or el.find("DLIST") is not None
                  or any(c.tag in ANNOTATIES for c in el)):
                # Een P die een annotatie draagt, is in de bron een omhulsel: 57 van
                # de 58 annotaties in een P zijn er het enige kind (de opmerkingen in
                # de milieukeurcriteria 32005D0338 en in 32024D2627), de 58e staat
                # naast een afbeelding (32005L0066).
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
            schrijf(_geen_opsomming(f"{nr}{scheiding}{txt}".strip()) if nr else txt)
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
            # Eén teller voor het hele punt: punt 4 van bijlage V bij 2012/27 heeft
            # twee opsommingen a) …, elk na een eigen inleidende P. Met een teller
            # per kind begon de tweede niet als `al2` en weigerde de zelfcontrole op
            # dubbele ankers (`annex-5-4-a` tot en met `-e`).
            binnen = {"lijsten": 0}
            for kind in el:
                if kind.tag not in ("NO.P", "TXT"):
                    self.inhoud(kind, anker or basis, binnen)
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
                # een eigen regel, zoals het Publicatieblad het zet, mét de drie
                # harde spaties: alleen daaraan herkent het profiel een kaal
                # lidnummer (patronen.md, "Een lid dat alleen een lijst is").
                # Hier stond `schrijf(prefix[0].rstrip())`, en `schrijf()` zet het
                # voorvoegsel er zelf nog eens voor: artikel 6, lid 3 van Rome II
                # (32007R0864, het enige geval in de meetlat) werd `3.   3.`. Het
                # blok gaat buiten `blok()` om, want dat haalt witruimte achteraan
                # weg, harde spaties inbegrepen.
                kaal, prefix[0] = prefix[0], ""
                self.u.blokken.append(kaal)
                if lid_anker and geankerd is not None and not geankerd[0]:
                    self.u.eenheid(lid_anker, "lid", kaal)
                    geankerd[0] = True
            self.lijst(el, basis if genummerd else "", extra)
        elif tag == "TBL":
            self.tabel(el)
        elif tag in ANNOTATIES:
            if isinstance(prefix, list) and prefix[0]:
                # Niet gemeten: in de meetlat staat geen annotatie in een lid. Het
                # nummer eraan vastplakken zou de noot tot lidtekst maken.
                raise _xml_fout("een annotatie aan het begin van een lid is niet gemeten")
            self.annotatie(el)
        elif tag == "ADDR.S":
            # Een adresblok is geen eenheid maar een reeks regels: elke P een eigen
            # alinea, zoals het Publicatieblad hem onder elkaar zet. De bijlagen
            # van het Data Privacy Framework (2023/1795) zijn brieven, en zes
            # daarvan hebben zo'n blok onder de datum; dat weigerde het besluit.
            # Tekst los tussen de P's is niet gemeten en zou hier wegvallen.
            if ws(el.text or "") or any(ws(kind.tail or "") for kind in el):
                self.u.markeer_onbekend("inhoud:ADDR.S")
            for kind in el:
                self.inhoud(kind, basis, teller, prefix, lid_anker, geankerd)
        elif tag in ("FINAL", "SIGNATURE", "SIGNATORY"):
            # De slotformule van een brief in een bijlage: `Hoogachtend,`, de
            # handtekening (een TIFF, die `afbeelding()` weglaat en vastlegt) en
            # de naam, elk een eigen alinea. Zes bijlagen van het Data Privacy
            # Framework (2023/1795) zijn brieven; het is het enige document in de
            # meetlat met een FINAL in een bijlage. De FINAL van de handeling
            # zelf loopt via `handeling()`, met het notenblok erna.
            if ws(el.text or "") or any(ws(kind.tail or "") for kind in el):
                self.u.markeer_onbekend(f"inhoud:{tag}")
            for kind in el:
                self.inhoud(kind, basis, teller)
        elif tag == "GR.TBL":
            self.tabelgroep(el)
        elif tag == "INCL.ELEMENT":
            # Leeg, dus `onbekend()` zou hem stil laten vallen.
            bijschrift = self.afbeelding(el, blok=True)
            if bijschrift is None:
                raise _xml_fout("een inclusie (INCL.ELEMENT) als los blok is alleen voor een afbeelding gemeten")
            if bijschrift:
                self.u.blok(bijschrift)
        elif tag in METADATA:
            return
        else:
            self.onbekend("inhoud", el)

    @staticmethod
    def _inclusie_in(p) -> tuple[list, ET.Element | None, ET.Element | None, str] | None:
        """De inclusies van een `P` die uit niets anders bestaat, met hun aanhalingstekens.

        Geeft `(inclusies, QUOT.START of None, QUOT.END of None, staart)`. Gemeten
        vormen: `<P><QUOT.S><INCL.ELEMENT/></QUOT.S></P>` (punt 43 van 32026R1744);
        `<P><INCL.ELEMENT/></P>` onder `Bijlage IV wordt vervangen door:`
        (32013R0390); en binnen een `QUOT.S` van een bijlage de aanhalingstekens in
        dezelfde `P`: `<QUOT.START/><INCL.ELEMENT/><QUOT.END/>` (32018D0187,
        32022L1648, 32026D0816), met een punt erachter (32020D1402), met twee
        inclusies tussen één paar tekens (32018L0100), of met het paar over twee
        `P`'s verdeeld (32019L0114). Staat er tekst naast, dan valt de `P` door
        naar de gewone inline-weg en wordt ze daar geweigerd; alleen een staart
        zonder woorden achter `QUOT.END` hoort bij het citaat.
        """
        if ws(p.text or ""):
            return None
        kinderen = list(p)
        if len(kinderen) == 1 and kinderen[0].tag == "QUOT.S":
            if ws(kinderen[0].tail or "") or ws(kinderen[0].text or ""):
                return None
            kinderen = list(kinderen[0])
        begin = kinderen.pop(0) if kinderen and kinderen[0].tag == "QUOT.START" else None
        einde = kinderen.pop() if kinderen and kinderen[-1].tag == "QUOT.END" else None
        if not kinderen or any(k.tag != "INCL.ELEMENT" or ws(k.tail or "")
                               or (k.get("TYPE") or "").upper() != "FORMEX.DOC" for k in kinderen):
            return None
        if begin is not None and ws(begin.tail or ""):
            return None
        staart = ws(einde.tail or "") if einde is not None else ""
        if re.search(r"\w", staart):
            return None
        return kinderen, begin, einde, staart

    def begint_met_aanhaling(self, incl) -> bool:
        """Of de tekst van een ingesloten document met een aanhalingsteken begint."""
        root = self.inclusies.get((incl.get("FILEREF") or "").rsplit("/", 1)[-1])

        def eerste(el) -> str | None:
            if el.tag in METADATA:
                return None
            if el.tag == "QUOT.START":
                return "aanhaling"
            if ws(el.text or ""):
                return "tekst"
            for kind in el:
                gevonden = eerste(kind)
                if gevonden:
                    return gevonden
                if ws(kind.tail or ""):
                    return "tekst"
            return None

        return root is not None and eerste(root) == "aanhaling"

    def geciteerde_inclusies(self, inclusies: list, begin, einde, staart: str) -> None:
        """Eén of meer geciteerde bijlagen, met de aanhalingstekens eromheen.

        Het openingsteken komt vóór het eerste blok, het sluitteken (met de punt
        erachter) achter het laatste, zoals ook `“Bijlage XIV` en `….”.` uit
        32026R1744 komen, waar de tekens in de inclusie zelf staan. Een tabel is
        geen tabel meer met een teken aan haar eerste of laatste regel; is dat
        blok een tabel, dan staat het teken als eigen alinea (32018D0187).
        """
        tekens = []
        for teken in (begin, einde):
            code = (teken.get("CODE") or "").upper() if teken is not None else None
            if code is not None and code not in AANHALING:
                raise _xml_fout(f"een inclusie staat tussen aanhalingstekens met de onbekende code {code!r}")
            tekens.append(AANHALING[code] if code is not None else "")
        voor, na = tekens[0], tekens[1] + staart
        blokken = self.u.blokken
        eerste = len(blokken)
        for incl in inclusies:
            self.geciteerde_inclusie(incl)
        if voor:
            if eerste < len(blokken) and not blokken[eerste].startswith("|"):
                blokken[eerste] = voor + blokken[eerste]
            else:
                blokken.insert(eerste, voor)
        if na:
            if eerste < len(blokken) and not blokken[-1].startswith("|"):
                blokken[-1] += na
            else:
                self.u.blok(na)

    def afbeelding(self, incl, blok: bool = False) -> str | None:
        """Een TIFF-inclusie: niet overnemen, wel vastleggen. None als het geen afbeelding is;
        anders de tekst van haar bijschrift (CAPTION), of "" zonder bijschrift.

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
            return None
        naam = (incl.get("FILEREF") or "").rsplit("/", 1)[-1]
        if naam not in self.afbeeldingen:
            raise _xml_fout(f"de tekst roept afbeelding {naam or '?'} aan, maar de handeling noemt haar niet")
        inhoud = incl.find("IMG.CNT")
        met_tekst = inhoud is not None and bool(ws(" ".join(inhoud.itertext())))
        bijschrift = incl.find("CAPTION")
        # Het bijschrift is brontekst bij het beeld en komt op zijn plek: in een
        # tabelcel wordt het de celtekst, als los blok een eigen alinea.
        onderschrift = ws(" ".join(ws(self.inline(p)) for p in bijschrift.iter("P"))) if bijschrift is not None else ""
        self.afbeeldingen_weggelaten.append({"fileref": naam, "format": AFBEELDINGSTYPE,
                                             "tekst_overgenomen": met_tekst,
                                             **({"bijschrift": onderschrift} if onderschrift else {})})
        if met_tekst:
            # IMG.CNT is de tekst van het beeld (een formulier van Brussel I bis,
            # van de beschermingsbevelrichtlijn 2011/99). Die is brontekst en
            # komt als alinea's mee, zonder eenheden: het is een formulier.
            if not blok:
                raise _xml_fout("een afbeelding met tekst (IMG.CNT) midden in een zin is niet gemeten")
            for kind in inhoud:
                self.inhoud(kind, basis="", teller={"lijsten": 0})
        return onderschrift

    def let_op_aaneen(self, el, stuk: str, ervoor: str, erna: str) -> None:
        """Onthoud een datum of getal die zonder spatie aan een woord vastzit (`2016betreffende`)."""
        if el.tag not in ("DATE", "FT") or not stuk:
            return
        voor = re.search(r"\w*$", ervoor).group(0) if stuk[0].isalnum() else ""
        na = re.match(r"\w*", erna).group(0) if stuk[-1].isalnum() else ""
        if voor or na:
            woorden = re.findall(r"\w+", stuk)
            self.aaneen[id(el)] = (voor + stuk if voor else "") + (woorden[-1] + na if na and not voor else na)

    def meld_aaneen(self) -> None:
        """Een datum of getal die de bron aan een woord vastschrijft: overgenomen, en gemeld."""
        woorden = list(dict.fromkeys(self.aaneen.values()))
        voorbeeld = ", ".join(f"'{w}'" for w in woorden[:5]) + (f" en {len(woorden) - 5} meer" if len(woorden) > 5 else "")
        self.metadata.setdefault("waarschuwingen", []).append(
            f"De Formex-bron schrijft {len(self.aaneen)} keer een datum of getal aaneen met het woord "
            f"ervoor of erna ({voorbeeld}); de omzetter neemt dat ongewijzigd over.")

    def meld_vast_na_noot(self) -> None:
        """Een nootverwijzing die de bron aan het woord erna vastschrijft: gescheiden, en gemeld."""
        woorden = list(dict.fromkeys(self.vast_na_noot.values()))
        voorbeeld = ", ".join(f"'{w}'" for w in woorden[:5]) + (f" en {len(woorden) - 5} meer" if len(woorden) > 5 else "")
        self.metadata.setdefault("waarschuwingen", []).append(
            f"De Formex-bron schrijft {len(self.vast_na_noot)} keer een nootverwijzing vast aan het woord "
            f"erna ({voorbeeld}); de omzetter zet er een spatie tussen, want de bron leest een noot als "
            f"woordgrens.")

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

    def annotatie(self, el) -> None:
        """Een annotatie (`GR.ANNOTATION`/`ANNOTATION`): gewone alinea's, zonder eenheden.

        Formex zet elke noot die geen voetnoot is in een `ANNOTATION`: een NB, een
        opmerking, een technische noot, een legenda onder een tabel. De tekst staat
        waar de bron hem zet, dus in documentvolgorde; de titel (`Noot 1`,
        `Technische noot:`) is een eigen alinea, zoals de HTML-route hem als
        `<p class="oj-ti-annotation">` levert, en geen `##`-kop. Een eenheid krijgt
        de noot niet: haar `a)` of `NB:` is geen onderdeel van de handeling, en een
        anker zou botsen met het onderdeel waar ze onder staat. Tot 23 september
        2026 weigerde een annotatie het hele document; 20 van de 347 documenten in
        de meetlat noemden haar in hun weigering, waaronder het Europees wetboek
        voor elektronische communicatie (32018L1972, `Noot 1` en `Noot 2` onder
        bijlage X).
        """
        for kind in el:
            if kind.tag == "TITLE":
                for deel in kind:
                    self.u.blok(ws(self.inline(deel)))
            elif kind.tag in METADATA:
                continue
            else:
                self.inhoud(kind, basis="", teller={"lijsten": 0})

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
        # Een NOTE.ID is uniek binnen één bestand, niet binnen de zip: de vier
        # geciteerde bijlagen van 32018L0100 en 32019L0114 heten elk `E0001`, net
        # als de eerste noot van de handeling. Op de kale sleutel kregen ze
        # allemaal hetzelfde nummer, met vier definities `(1)` in één notenblok.
        eerder, self.nootruimte = self.nootruimte, f"{naam}#"
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
        self.nootruimte = eerder

    def geciteerde_tabel(self, el) -> str:
        """Een tabel binnen een citaat dat inline loopt: een marker op haar plek in de tekst.

        Een wijzigingshandeling voegt een rij toe aan een tabel van een andere
        handeling (`<P><QUOT.S><TBL>`, 28 keer in 12 documenten van de meetlat,
        de PIC-verordening 32014R0167 zes keer) of zet een alinea met een tabel
        erin. Inline kan een tabel niet, en een tabel blijft een tabel (patronen.md
        §7: de ingevoegde tekst krijgt geen eigen ankers). Daarom breekt de
        alinea daar: `schrijf()` in `inhoud()` schrijft de tekst ervóór, dan deze
        blokken, dan de tekst erna. Een marker die niet door `schrijf()` gaat,
        blijft in `citaattabellen` staan, en dan weigert `omzetten()`.

        De tabel wordt hier al opgebouwd, niet pas bij het schrijven: zo krijgen
        haar noten hun nummer in documentvolgorde, vóór een noot in de tekst erna.
        """
        eerder, self.u.blokken = self.u.blokken, []
        try:
            self.tabel(el)
            blokken = self.u.blokken
        finally:
            self.u.blokken = eerder
        self.citaattabellen.append(blokken)
        return f" {TABELMARKER}{len(self.citaattabellen) - 1}{TABELMARKER} "

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
            regel = _geen_opsomming(" ".join(x for x in (prefix, term, aanhef) if x))
            self.u.blok(regel)
            if anker:
                self.u.eenheid(anker, "onderdeel", regel)

        structureel = ("LIST", "DLIST", "TBL")
        delen = _definitie_delen(definitie, structureel) if definitie is not None else []
        if definitie is None or not any(k is not None and k.tag in structureel for _, k in delen):
            schrijf_kop(ws(self.inline(definitie)) if definitie is not None else "")
            return

        # Eén doorloop, zodat tekst ná een opsomming niet stil wegvalt: alles
        # tot het eerste structurele kind hoort bij de kopregel, wat erna komt
        # wordt een eigen alinea zonder eigen anker (zoals ALINEA het doet).
        lopend, kop_geschreven = "", False
        for voor, kind in delen:
            lopend += voor
            if kind is not None and kind.tag not in structureel:
                lopend += self.inline_el(kind)
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
            regel = _geen_opsomming(f"{nr} {txt}".strip()) if genummerd or nr else f"— {txt}".strip()
            if regel:
                self.u.blok(regel)
            if anker:
                self.u.eenheid(anker, "onderdeel", regel)
            # Eén teller voor het hele onderdeel: artikel 2, lid 2, onder h) van
            # de consumentenkredietrichtlijn (32023L2225) heeft twee reeksen
            # i)–iii) met een alinea ertussen, elk in een eigen P. Met een verse
            # teller per P kregen beide `art-2-2-h-i`; de tweede reeks is `al2`,
            # zoals een tweede opsomming in een lid (patronen.md).
            teller = {"lijsten": 0}
            for sub in binnen:
                if sub.tag in ("LIST", "DLIST"):
                    self.lijst(sub, anker, "")
                else:
                    self.inhoud(sub, anker or basis, teller)

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

        def punt(np) -> str:
            """Nummer, tekst en wat het punt daarna nog draagt, in bronvolgorde.

            Een NP in een cel draagt na zijn TXT soms nog alinea's of een geneste
            opsomming: bijlage I van de batterijverordening (32023R1542) zet
            `CAS-nr. …` en `EG-nr. …` als P's onder `1. Kwik`, en de normentabel
            van 32021D1402 hangt een lijst i)–xxviii) onder punt a). Alleen NO.P en
            TXT lezen liet die tekst stil vallen (6 en 25 bladalinea's).
            """
            nr = ws(self.inline(np.find("NO.P"))) if np.find("NO.P") is not None else ""
            txt = ws(self.inline(np.find("TXT"))) if np.find("TXT") is not None else ""
            rest = ET.Element("x")
            rest.extend(c for c in np if c.tag not in ("NO.P", "TXT"))
            return ws(f"{nr} {txt} {self.cel_tekst(rest)}")

        for kind in cel:
            if kind.tag == "LIST":
                sluit()
                genummerd = kind.get("TYPE", "").upper() not in ONGENUMMERD
                for item in kind.findall("ITEM"):
                    np = item.find("NP")
                    if np is not None:
                        delen.append(punt(np))
                    else:
                        delen.append(("" if genummerd else "- ") + ws(self.inline(item)))
            elif kind.tag == "NP":
                sluit()
                delen.append(punt(kind))
            elif kind.tag in ("P", "ALINEA", "ADDR.S", "GR.SEQ", "TITLE", "TI") + ANNOTATIES:
                # Een cel met onderdelen: de lijst van goedgekeurde werkzame
                # stoffen zet de specifieke bepalingen als `DEEL A` en `DEEL B`
                # (GR.SEQ met TITLE/TI/P) in één cel (32011R0704, en geciteerd
                # in 32008L0044 twaalf keer). Een cel kan geen structuur dragen;
                # net als een lijst in een cel wordt het tekst, kop voorop. Een
                # annotatie in een cel (`Noot:` in 32026L0706, negen in
                # 32013L0052) is een blok in die cel, net als een alinea.
                # Een adresblok in een cel (44 keer in 2020/1675, de Europese lijst
                # van scheepsrecyclinginrichtingen) is een blok als een P: zijn
                # regels staan door een spatie gescheiden, niet aan elkaar.
                sluit()
                delen.append(self.cel_tekst(kind))
            else:
                stuk = self.inline_el(kind)
                self.let_op_aaneen(kind, stuk, "".join(lopend), kind.tail or "")
                lopend.append(stuk)
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
        for row in el.iter():
            if row.tag == "TI.BLK":
                rijen.append([self.rijgroeptitel(row)])
                continue
            if row.tag != "ROW":
                continue
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
        # Een annotatie tussen de tabelnoten (`Aantekeningen bij de tabel:` in
        # 32023L0544, `Bron: Eurostat.` in 32012L0027) is geen noot met een
        # nummer maar tekst onder de tabel; de HTML-route zet haar als laatste
        # tabelrij. Hier komt ze als alinea direct onder de tabel, en de noten
        # gaan zoals altijd naar het notenblok.
        for annotatie in (gr.findall("GR.ANNOTATION") if gr is not None else []):
            self.annotatie(annotatie)
        for noot in definities:
            sleutel = noot.get("NOTE.ID")
            if sleutel:
                sleutel = self.nootruimte + sleutel
            nummer = self.nootlabels.get(sleutel)
            if nummer is None:
                self.nootnummer += 1
                nummer = self.nootlabels[sleutel] = self.nootnummer
            self.noten.append((nummer, ws(self.inline(noot))))

    def rijgroeptitel(self, ti) -> dict:
        """De titel van een groep tabelrijen (`BLK/TI.BLK`), als cel over zijn kolommen.

        De PRODCOM-lijst (32010R0860) deelt haar tabel in 1.727 geneste groepen in
        (`NACE 07.10: Winning van ijzererts`, daaronder `CPA 07.10.10: IJzererts`),
        elk met een titel over `COL.START` tot en met `COL.END`. De HTML-route
        maakt daar een rij met één cel en `colspan` van. Hier is het een rij met
        één samengevoegde cel, die dus net als elke andere samengevoegde cel op
        elke bezette plek staat en in de brontelling herhaald wordt. De rijen van
        de groep volgen in documentvolgorde; een BLK zonder titel (32019R0089)
        is alleen een groepering. Overspant de titel niet de hele breedte, dan
        weigert het raster op ontbrekende cellen.
        """
        start, eind = ti.get("COL.START") or "", ti.get("COL.END") or ""
        if not (start.isdigit() and eind.isdigit() and 0 < int(start) <= int(eind)):
            raise _xml_fout("de titel van een groep tabelrijen (TI.BLK) noemt geen geldige kolommen")
        return {"tekst": self.cel_tekst(ti), "kol": int(start) - 1,
                "colspan": int(eind) - int(start) + 1, "rowspan": 1}

    def tabelgroep(self, el) -> None:
        """Een groep tabellen (`GR.TBL`): de gezamenlijke titel als alinea, dan elke tabel.

        Formex bundelt tabellen die één geheel vormen onder één `GR.TBL`, met een
        eigen `TITLE` en per tabel ook weer een titel. Gemeten op 23 september 2026
        in vier documenten van de meetlat: artikel 224 van de CRR (32013R0575 en
        02013R0575-20270101: `VOLATILITEITSAANPASSINGEN` boven `Tabel 1` tot en met
        `Tabel 4`), de correlatietabel van bijlage XV van de energie-
        efficiëntierichtlijn (32012L0027) en de concordantietabel van bijlage II van
        de consumentenrichtlijn (32011L0083, twee tabellen, de tweede met een
        tabelnoot). De titel krijgt de vorm van een tabeltitel (`TBL/TITLE`): een
        losse alinea zonder opmaak. Elke tabel gaat door `tabel()`, dus een
        geneste tabel blijft een weigering; iets anders dan een titel of een tabel
        is niet gemeten en wordt geweigerd.
        """
        for kind in el:
            if kind.tag == "TITLE":
                for deel in kind:
                    tekst = self.kop_tekst(deel)
                    if tekst:
                        self.u.blok(tekst)
            elif kind.tag == "TBL":
                self.tabel(kind)
            elif kind.tag in METADATA:
                continue
            else:
                self.onbekend("tabelgroep", kind)

    # ------------------------------------------------------------ algemene documenten

    def algemeen(self, root) -> None:
        """Een algemeen document (`GENERAL`): titelregels, inleiding en inhoud, zonder eenheden.

        Formex gebruikt `GENERAL` voor een publicatie die geen handeling en geen
        bijlage is. Gemeten op 23 september 2026 in vier documenten van de meetlat,
        in twee rollen. Meestal is het een verklaring die het manifest als
        `DOC.SUB.PUB TYPE="ASSOCIATION"` achter de handeling zet: van de Commissie
        bij Rome II (32007R0864) en bij de geoblockingverordening (32018R0302), en de
        gezamenlijke verklaring over het Galileo-panel na de bijlage van 32008R0683.
        Bij 32006D0857 (de samenvatting van de AstraZeneca-beschikking) is het het
        hele document, met een `PROLOG` tussen titel en inhoud.

        De titel krijgt de vorm van de titel van een handeling: elke `P` een eigen
        regel, kapitalen waar de bron `HT TYPE="UC"` zet. De inhoud gaat door
        `bijlage_inhoud()` zonder eenheden, zoals een geciteerd blok: een verklaring
        is geen artikel of bijlage van de handeling, en het profiel kent er geen
        anker voor. De noten tellen opnieuw vanaf (1) en staan achter het document,
        zoals bij een bijlage. Een ander kind dan titel, inleiding of inhoud is niet
        gemeten en wordt geweigerd.
        """
        self.nieuwe_nootreeks()
        for kind in root:
            if kind.tag == "TITLE":
                for deel in kind:
                    for p in (deel if deel.tag in ("TI", "STI") else [deel]):
                        if p.tag == "P":
                            self.u.blok(ws(self.inline(p)))
                        else:
                            self.onbekend("algemeen", p)
            elif kind.tag == "PROLOG":
                for sub in kind:
                    self.inhoud(sub, basis="", teller={"lijsten": 0})
            elif kind.tag == "CONTENTS":
                self.bijlage_inhoud(kind, "", geciteerd=True)
            elif kind.tag in METADATA:
                continue
            else:
                self.onbekend("algemeen", kind)
        self.notenblok()

    # ------------------------------------------------------------ bijlagen

    def bijlage(self, root) -> None:
        self.bijlagen += 1
        if self.noten:
            # Zelfcontrole: een noot die hier nog wacht, zou onder de reeks van de
            # bijlage een tweede `(1)` krijgen. Elke aanroeper schrijft het blok
            # eerst (`handeling()`, `omzetten()`); dit is de grens die dat bewaakt.
            raise _xml_fout(f"{len(self.noten)} noot(en) van de tekst vóór deze bijlage zijn nog niet "
                            "geschreven; ze zouden met de bijlagenoten dubbel genummerd raken")
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
        # De geconsolideerde MDR (02017R0745-20260719) opent haar bijlagen met een
        # CONS.ANNEX `BIJLAGEN` die alleen een TOC draagt, zonder CONTENTS. Hier
        # las de omzetter alleen CONTENTS, en viel zo'n inhoudsopgave weg.
        opgaven = root.findall("TOC")
        if len(opgaven) > 1 or (opgaven and inhoud is not None):
            raise _xml_fout("een bijlage met meer dan een inhoudsopgave (TOC), of met een TOC naast "
                            "CONTENTS, is niet gemeten")
        for toc in opgaven:
            self.inhoudsopgave(toc)
        if inhoud is not None:
            self.bijlage_inhoud(inhoud, anker)
        self.notenblok()

    def brief(self, el, anker: str, geciteerd: bool) -> None:
        """Een brief in een bijlage (`LETTER`): titel, plaats en datum, inhoud, ondertekening.

        De zeven bijlagen van het Privacyschildbesluit (32016D1250) zijn brieven van
        Amerikaanse bewindslieden, elk als `LETTER` met `TITLE`, `PL.DATE`, `CONTENTS`
        en `SIGNATORY` (gemeten: acht brieven, geen andere kinderen). De titel wordt
        een gewone alinea, want de bron noemt haar geen bijlageonderdeel; de inhoud
        gaat door `bijlage_inhoud()` onder het anker van de bijlage, zodat een
        genummerd onderdeel in de brief hetzelfde anker krijgt als buiten een brief.
        Tot 25 september 2026 was `LETTER` een weigering (T1-F16, kb WP-20).
        """
        for kind in el:
            if kind.tag == "TITLE":
                for p in kind.iter("P"):
                    tekst = ws(self.inline(p))
                    if tekst:
                        self.u.blok(tekst)
            elif kind.tag == "PL.DATE":
                for sub in kind:
                    self.inhoud(sub, basis="", teller={"lijsten": 0})
            elif kind.tag == "CONTENTS":
                self.bijlage_inhoud(kind, anker, geciteerd)
            elif kind.tag == "SIGNATORY":
                self.inhoud(kind, basis="", teller={"lijsten": 0})
            elif kind.tag in METADATA:
                continue
            else:
                self.onbekend("brief", kind)

    def inhoudsopgave(self, toc) -> None:
        """Een inhoudsopgave (TOC) in een bijlage: tekst, geen structuur.

        Elk `TOC.ITEM` wordt één alinea `nummer tekst`, zonder kop, eenheid of
        Markdown-lijst: de koppen en ankers horen bij de bijlagen zelf, en een
        tweede `BIJLAGE I` hier zou ermee botsen. Dat is de regel die de
        Publicatiebladversie van de MDR (32017R0745) al krijgt, waar dezelfde
        opgave als losse punten (NP) staat: `I Algemene veiligheids- en
        prestatie-eisen`. Geneste blokken (`TOC.BLK`, de aanhangsels van bijlage
        II in 2005/66) volgen in bronvolgorde. Opmaak valt weg, zoals in een kop.
        Een paginaverwijzing (`ITEM.REF`) is het bladzijdenummer van het
        Publicatieblad en valt weg als metadata; de titel van de opgave
        (`Inhoudsopgave`) is een gewone alinea.
        """
        for kind in toc:
            if kind.tag == "TOC.BLK":
                self.inhoudsopgave(kind)
            elif kind.tag == "TITLE":
                # `Inhoudsopgave` boven de opgave in de bijlagen van de
                # adequaatheidsbesluiten voor Japan (32019D0419) en Korea
                # (32022D0254): tekst, geen kop, net als de regels eronder.
                for p in kind.iter("P"):
                    tekst = self.kop_tekst(p)
                    if tekst:
                        self.u.blok(tekst)
            elif kind.tag == "TOC.ITEM":
                delen = [d for d in kind if d.tag in ("NO.ITEM", "ITEM.CONT")]
                rest = [d for d in kind if d.tag not in ("NO.ITEM", "ITEM.CONT") and d.tag not in METADATA]
                if rest or ws(kind.text or "") or any(ws(d.tail or "") for d in kind):
                    raise _xml_fout("een regel van een inhoudsopgave (TOC.ITEM) bevat meer dan "
                                    "NO.ITEM en ITEM.CONT; dat is niet gemeten")
                self.u.blok(" ".join(t for t in (self.kop_tekst(d) for d in delen) if t))
            elif kind.tag in METADATA:
                continue
            else:
                self.onbekend("inhoudsopgave", kind)

    def bijlage_inhoud(self, el, anker: str, geciteerd: bool = False, nummer: str = "") -> None:
        """`geciteerd`: een ingesloten bijlage van een andere handeling — geen eenheden.

        `nummer`: het nummer van dit onderdeel (`NO.GR.SEQ`) met zijn scheiding; het
        komt vóór de eerste tekst, zoals het nummer van een lid.
        """
        teller = {"lijsten": 0}
        voorvoegsel = [nummer]
        eerste_eenheid = len(self.u.eenheden)
        onderdelen = [k for k in el if k.tag == "GR.SEQ"]
        for kind in el:
            if voorvoegsel[0] and kind.tag not in ("P", "LIST"):
                # Gemeten volgt op NO.GR.SEQ 901 keer een P en één keer een LIST
                # (punt 6.4. van bijlage I bij 32008L0001). Voor een tabel of een
                # onderdeel is niet bewezen waar het nummer hoort.
                raise _xml_fout(f"het nummer {nummer.strip()} van een bijlageonderdeel (NO.GR.SEQ) "
                                f"staat vóór een {kind.tag}; alleen een alinea of opsomming is gemeten")
            if kind.tag == "GR.SEQ":
                titel = kind.find("TITLE")
                nr = kind.find("NO.GR.SEQ")
                # De kop van een bijlageonderdeel staat als NP: het letterteken
                # in NO.P, de tekst in TXT. Zonder de scheiding ertussen leest
                # `A.“Algemeen”` niet als onderdeel (RE_ANNEX_PART in het profiel).
                np = titel.find(".//NP") if titel is not None else None
                onder_de_kop = []
                if nr is not None:
                    # Een onderdeel zonder kop draagt zijn nummer in NO.GR.SEQ, met
                    # de tekst in de P erna: `1.1.` en `Stookinstallaties …` (bijlage
                    # I bij de IPPC-richtlijn 2008/1), 294 keer in de MDR. Tot 23
                    # september 2026 weigerden 12 van de 347 documenten (mede) daarop.
                    # Het Publicatieblad drukt het als het nummer van een onderdeel
                    # met een kop, en zo komt het in raw: nummer plus drie harde
                    # spaties plus de tekst, zoals `10.` (kop) en `10.1.` (NO.GR.SEQ)
                    # in bijlage I van de MDR. Met een gewone spatie is `2) …`
                    # (2025/2205) een Markdown-lijst. Het nummer is ook het ankersegment,
                    # zoals bij een onderdeel met een kop `3. Inhoud …`: in bijlage VI
                    # van 2025/2205 zijn 1. en 2. NO.GR.SEQ en 3. en 4. een kop, en
                    # samen één reeks.
                    kop = ws(self.inline(nr))
                    if titel is not None or kind[0] is not nr or not ONDERDEELNUMMER.fullmatch(kop):
                        raise _xml_fout(
                            f"een bijlageonderdeel met nummer {kop!r} (NO.GR.SEQ) heeft niet de gemeten "
                            "vorm: het nummer vooraan, geen kop ernaast, en `1.`, `1.1.`, `2)` of `d)`")
                    ti = ""
                elif np is not None and np.find("NO.P") is not None:
                    letter = ws(self.inline(np.find("NO.P")))
                    rest = ws(self.inline(np.find("TXT"))) if np.find("TXT") is not None else ""
                    ti = f"{letter}{NBSP * 3}{rest}".strip()
                    # De kop dient hier alleen om het nummer te lezen, dus uit de
                    # bron en niet met een tweede `inline()`: die schreef de noot in
                    # een kop een tweede keer (`14.1. … Richtlijn 90/220/EEG (3)` in
                    # bijlage I bij 32013R0503). Over 2159 onderdeelkoppen in de
                    # meetlat geeft dat hetzelfde nummer als `kop_tekst`.
                    kop = ws(_plat_bron(np))
                    # Een NP-kop kan na TXT nog een P dragen, een aanwijzing onder de
                    # kop: `B. Modelformulier voor herroeping` met `(dit formulier
                    # alleen invullen …)` in bijlage I van 32011L0083, en `2. MASSA'S
                    # EN AFMETINGEN` met `(eventueel naar tekeningen verwijzen)` in
                    # 32005L0066. Die P viel weg; ze komt als alinea onder de kopregel.
                    onder_de_kop = [k for k in np if k.tag not in ("NO.P", "TXT")]
                else:
                    ti = ws(self.inline(titel)) if titel is not None else ""
                    # Het nummer staat in de eerste P: `Deel II` en het opschrift
                    # `VERBODSBEPALINGEN` zijn aparte P's, en aan elkaar
                    # (`Deel IIVERBODSBEPALINGEN`) is het nummer niet meer te lezen.
                    # Zonder opmaak: `*Bepaling 8*` (cursief in de SCC's) leest anders
                    # niet als nummer. Uit de bron, net als hierboven: `ti` heeft de
                    # noten van de kop al geschreven (drie koppen met een noot in
                    # 32023L2225, vier in 32018L0100); 1348 koppen, hetzelfde nummer.
                    eerste = titel.find(".//P") if titel is not None else None
                    kop = ws(_plat_bron(eerste)) if eerste is not None else ti
                m = ONDERDEELKOP.match(kop)
                if geciteerd:
                    sub = anker
                elif m:
                    segment = nummer_anker(m.group(1))
                    sub = f"{anker}-{segment}"
                    # Een tweede reeks in hetzelfde blok draagt `al<k>`, dezelfde
                    # afspraak en dezelfde teller als bij losse punten (NP-tak van
                    # `inhoud`): punten, onderdelen en de opsomming ertussen zijn één
                    # nummering. Bijlage I bij 2008/1 telt eerst twee inleidende
                    # punten 1. en 2. en dan de categorieën 1. tot en met 6.; bijlage
                    # III bij 2023/1230 de punten 1. tot en met 5. en dan de delen 1.
                    # en 2.; bijlage V bij 2025/2205 de opsomming a) en b) en dan
                    # `Titel A` en `Titel B`. Zonder onderscheid weigerde de
                    # zelfcontrole op dubbele ankers. Alleen een reeks die opnieuw
                    # begint (1, a, i) is een tweede reeks; een dubbel nummer midden in
                    # een reeks is een bronfout en blijft een weigering.
                    reeks = teller.get("reeks", 1)
                    huidig = f"{anker}-al{reeks}-{segment}" if reeks > 1 else sub
                    if segment in ("1", "a", "i") and huidig in {e.anker for e in self.u.eenheden[eerste_eenheid:]}:
                        reeks = teller["reeks"] = reeks + 1
                        teller.setdefault("punten", set()).clear()
                    teller.setdefault("punten", set()).add(sub)
                    if reeks > 1:
                        sub = f"{anker}-al{reeks}-{segment}"
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
                for aanwijzing in onder_de_kop:
                    self.inhoud(aanwijzing, basis="", teller={"lijsten": 0})
                wrapper = ET.Element("x")
                wrapper.extend([c for c in kind if c.tag != "TITLE" and c is not nr])
                self.bijlage_inhoud(wrapper, sub, geciteerd, f"{kop}{NBSP * 3}" if nr is not None else "")
            elif kind.tag == "QUOT.S":
                # Een bijlage die (een deel van) een bijlage van een andere
                # handeling vervangt, citeert die als blok: een tabel, een
                # onderdeel of alinea's binnen QUOT.S (16 van de 347 documenten
                # in de meetlat, meest wijzigingsrichtlijnen). Zoals bij een
                # ingesloten bijlage: wel brontekst, geen eenheden.
                self.bijlage_inhoud(kind, "", geciteerd=True)
            elif kind.tag == "ARTICLE" and not geciteerd:
                # Een bijlage kan een compleet reglement zijn met eigen artikelen:
                # de statuten van een ERIC (32022D0289, elf artikelen), of de
                # artikelen van de Internationale Gezondheidsregeling die een
                # besluit per onderdeel wijzigt (32022D0830). Het profiel geeft ze
                # `annex-<n>-art-<k>` (patronen.md §6); de kop is die van een
                # artikel, en de structuurcontrole telt ze als artikelen mee.
                self.artikel(kind, ouder=anker)
            elif kind.tag == "TOC":
                # `LIJST VAN BIJLAGEN` in 2005/66: de inhoudsopgave staat in CONTENTS.
                self.inhoudsopgave(kind)
            elif kind.tag == "LETTER":
                self.brief(kind, anker, geciteerd)
            elif (kind.tag == "INCL.ELEMENT" and geciteerd
                  and (kind.get("TYPE") or "").upper() == "FORMEX.DOC"):
                # Een QUOT.S van een bijlage die alleen een inclusie draagt, zonder P
                # en zonder aanhalingstekens: `<CONTENTS><QUOT.S><INCL.ELEMENT/>`
                # (32013D0287, 32016R2390). Buiten een citaat blijft een losse
                # inclusie een weigering in `inhoud()`.
                self.geciteerde_inclusies([kind], None, None, "")
            elif (kind.tag == "INCL.ELEMENT" and (kind.get("TYPE") or "").upper() == "FORMEX.DOC"
                  and self.begint_met_aanhaling(kind)):
                # Een eigen bijlage die niets draagt dan een inclusie, zonder QUOT.S
                # eromheen: bijlage V, VI en VII van eIDAS 2 (32024R1183) bevatten
                # elk alleen de nieuwe bijlage die ze aan 910/2014 toevoegen. Dat die
                # tekst geciteerd is, zegt de inclusie zelf: ze begint met het
                # aanhalingsteken (`“BIJLAGE V`). Alleen dan is het een citaat;
                # een losse inclusie zonder dat teken blijft een weigering.
                self.geciteerde_inclusies([kind], None, None, "")
            elif kind.tag in METADATA:
                continue
            elif voorvoegsel[0]:
                begin = len(self.u.blokken)
                self.inhoud(kind, basis=anker, teller=teller, prefix=voorvoegsel)
                if voorvoegsel[0] or not self.u.blokken[begin:] or \
                        not self.u.blokken[begin].startswith(nummer.rstrip()):
                    # Een P die alleen een inclusie draagt, of een alinea die
                    # met een tabel begint, schrijft het wachtende nummer niet
                    # (of pas later); dan staat het niet vóór zijn tekst.
                    raise _xml_fout(f"het nummer {nummer.strip()} van een bijlageonderdeel (NO.GR.SEQ) "
                                    "staat in de Markdown niet vóór zijn tekst")
                if anker and not geciteerd:
                    self.u.eenheid(anker, "bijlagedeel", self.u.blokken[begin])
            else:
                self.inhoud(kind, basis=anker, teller=teller)
        if voorvoegsel[0]:
            raise _xml_fout(f"een bijlageonderdeel heeft een nummer ({nummer.strip()}, NO.GR.SEQ) maar geen tekst")


def _geen_opsomming(regel: str) -> str:
    """Een markering `*`, `+` of `-` vooraan de regel krijgt een harde spatie achter zich.

    Met een gewone spatie is zo'n regel in Markdown een opsomming: het teken
    verdwijnt als tekst en de regel wordt een lijst. De bron gebruikt het als
    gedrukte markering: `*` als lijstteken (NO.P) in 32025D2554, en als term in
    de legenda onder de PRODCOM-lijst (32010R0860, `* Rubriek die wijziging
    bevat`). Het profiel doet voor zulke markeercellen uit de HTML-route
    hetzelfde (patronen.md, paragraaf 6 en 8): harde spatie, "zodat Markdown er
    geen lijst van maakt".
    """
    return re.sub(r"^([*+-]) ", r"\1" + NBSP, regel)


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
