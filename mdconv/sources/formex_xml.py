"""Formex 4 (Publicatieblad) -> de raw-vorm die md-clean-eurlex verwacht.

De Cellar levert een zip met een `.doc.xml` (de inhoudsopgave van de
manifestatie) en per onderdeel een XML: de handeling (`ACT`, of `CONS.ACT` bij
een geconsolideerde tekst) en elke bijlage. De volgorde komt uit de `.doc.xml`,
niet uit de bestandsnamen.

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
from .xml_gedeeld import Uitvoer, nummer_anker, tabel_markdown, ws

NBSP = " "
ONGENUMMERD = {"DASH", "NDASH", "BULLET", "NONE", "DISC"}
METADATA = {"BIB.INSTANCE", "BIB.DOC", "BIB.DATA", "PUBLICATION.REF", "NO.DOC", "INFO.CONSLEG",
            "INFO.PROD", "FAM.COMP", "GR.MOD.ACT", "DOCUMENT.REF", "PAGE.FIRST", "PAGE.LAST",
            "PAGE.SEQ", "PAGE.TOTAL", "LG.DOC", "NO.SEQ", "VOLUME.REF"}
INLINE_TEKST = {"DATE", "REF.DOC.OJ", "FT", "HT", "QUOT.S", "IE", "PERIOD", "REF.DOC", "ACRONYM",
                "ADDR", "PL.DATE", "NO.CELEX", "UNIT", "EXPONENT", "INF", "SUP", "TERM", "DEFINITION"}
INLINE_TRANSPARANT = {"TI", "STI", "NP", "NO.P", "TXT", "ITEM", "PREFIX"}
STRUCTUUR_ELEMENTEN = {
    "ACT", "CONS.ACT", "CONS.DOC", "ANNEX", "CONS.ANNEX", "TITLE", "PREAMBLE",
    "GR.VISA", "GR.CONSID", "CONSID", "ENACTING.TERMS", "FINAL", "DIVISION",
    "ARTICLE", "PARAG", "NO.PARAG", "ALINEA", "P", "LIST", "DLIST", "DLIST.ITEM",
    "ITEM", "NP", "NO.P", "TXT", "TBL", "CORPUS", "ROW", "CELL", "GR.NOTES",
    "NOTE", "CONTENTS", "GR.SEQ", "TI", "STI", "TI.ART", "STI.ART", "PREFIX",
    "TERM", "DEFINITION", "PREAMBLE.INIT", "PREAMBLE.FINAL", "GR.CONSID.INIT",
    "VISA", "SIGNATORY", "SIGNATURE", "COM",
}
BEKENDE_TEKSTELEMENTEN = METADATA | INLINE_TEKST | INLINE_TRANSPARANT | STRUCTUUR_ELEMENTEN


def _xml_fout(boodschap: str, exc: Exception | None = None) -> ConversionError:
    fout = ConversionError(f"Formex-bron geweigerd: {boodschap}")
    if exc is not None:
        fout.__cause__ = exc
    return fout


def _onderdelen(data: bytes) -> tuple[ET.Element, list[tuple[str, ET.Element]]]:
    """Lees één manifestatie, uitsluitend in de volgorde van de `.doc.xml`.

    De omgekeerde controle is bewust: een extra XML-onderdeel dat niet in de
    inhoudsopgave staat mag niet stil buiten de omzetting blijven.
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
        doc_namen = [n for n in korte_namen if n.lower().endswith(".doc.xml")]
        if len(doc_namen) != 1:
            raise _xml_fout(
                f"de zip bevat {len(doc_namen)} inhoudsopgaven (.doc.xml); precies één is vereist"
            )
        try:
            doc = ET.fromstring(zf.read(per_naam[doc_namen[0]]))
        except (ET.ParseError, KeyError) as exc:
            raise _xml_fout(f"de .doc.xml is niet leesbaar ({exc})", exc)
        volgorde = [r.get("FILE") for r in doc.iter("REF.PHYS") if r.get("TYPE") == "DOC.XML"]
        if not volgorde or any(not n for n in volgorde):
            raise _xml_fout("de .doc.xml noemt geen geldige onderdelen (REF.PHYS TYPE=DOC.XML)")
        kort = [n.rsplit("/", 1)[-1] for n in volgorde]
        if len(kort) != len(set(kort)):
            raise _xml_fout("de .doc.xml noemt hetzelfde onderdeel meer dan één keer")
        werkelijk = set(korte_namen) - set(doc_namen)
        genoemd = set(kort)
        ontbreekt = genoemd - werkelijk
        extra = werkelijk - genoemd
        if ontbreekt:
            raise _xml_fout(f"de .doc.xml noemt ontbrekende onderdelen: {', '.join(sorted(ontbreekt))}")
        if extra:
            raise _xml_fout(f"de zip bevat onderdelen buiten de .doc.xml: {', '.join(sorted(extra))}")
        uit = []
        for naam in kort:
            try:
                uit.append((naam, ET.fromstring(zf.read(per_naam[naam]))))
            except (ET.ParseError, KeyError) as exc:
                raise _xml_fout(f"onderdeel {naam} is niet leesbaar ({exc})", exc)
        return doc, uit


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
    _, delen = _onderdelen(basis)
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
        self.basis: ET.Element | None = None       # PREAMBLE van de basishandeling
        self.zonder: frozenset = frozenset()       # opmaaksoorten die nu niet geschreven worden

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
        if tag == "P":
            return " " + self.inline(el) + " "
        if tag in ("LIST", "ITEM"):
            # Een opsomming binnen QUOT.S citeert de structuur van een andere
            # handeling. De eigen planner mag daar geen onderdelen van maken,
            # maar de woorden moeten wel in documentvolgorde blijven staan.
            return " " + self.inline(el) + " "
        if tag in INLINE_TEKST or tag in INLINE_TRANSPARANT:
            return self.inline(el)
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
        doc, onderdelen = _onderdelen(data)
        self.controleer_elementen(onderdelen)
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
        if self.basis is not None:
            # De ingevoegde considerans is brontekst als elke andere: dezelfde
            # controles op verlies, verdubbeling en verweving gelden ook voor haar.
            onderdelen = onderdelen + [("basishandeling:PREAMBLE", self.basis)]
        self.zelfcontrole(onderdelen)
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

    def zelfcontrole(self, onderdelen: list[tuple[str, ET.Element]]) -> None:
        """Bewijs binnen deze route dat tekst en structurele eenheden aankomen."""
        markdown = self.u.markdown()
        controle_markdown = markdown
        if self.metadata.get("format") == "clg":
            # Deze referentieregel komt uit INFO.CONSLEG-attributen en is dus
            # herkomst, geen tekstnode uit de manifestatie.
            controle_markdown = controle_markdown.split("\n\n", 1)[-1]

        verwacht = _bronwoorden(onderdelen)
        gekregen = Counter(_woorden(controle_markdown))
        if verwacht != gekregen:
            ontbreekt = list((verwacht - gekregen).elements())[:12]
            extra = list((gekregen - verwacht).elements())[:12]
            raise ConversionError(
                "Formex-tekstbehoud faalt (woordmultiset verschilt; "
                f"ontbreekt={ontbreekt or 'niets'}, extra={extra or 'niets'})."
            )

        alle_woorden = " " + " ".join(_woorden(controle_markdown)) + " "
        for naam, woorden in _bladalineas(onderdelen):
            if f" {' '.join(woorden)} " not in alle_woorden:
                voorbeeld = " ".join(woorden[:14])
                raise ConversionError(
                    f"Formex-bladalinea uit {naam} staat niet aaneengesloten in de Markdown: "
                    f"{voorbeeld!r}."
                )

        telling = Counter(e.soort for e in self.u.eenheden)
        artikelen = sum(1 for _, root in onderdelen for _ in root.iter("ARTICLE"))
        leden = sum(
            1 for _, root in onderdelen for el in root.iter("PARAG")
            if el.find("NO.PARAG") is not None and ws(_plat_bron(el.find("NO.PARAG")))
        )
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
            fouten.append(f"dubbele structurele ankers: {', '.join(dubbel[:8])}")
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
        datum = f"{int(iso[6:8])}.{int(iso[4:6])}.{iso[0:4]}" if len(iso) == 8 else iso
        self.metadata = {"format": "oj", "language": taal.lower(),
                         "oj_reference": f"PB {coll} {nummer} van {datum}, blz. {bladzijde}"}
        # Geen mastheadtabel in de uitvoer: die is opmaak van de gedrukte
        # bladzijde, geen inhoud, en als pipe-tabel zou hij als inhoudstabel
        # meetellen in het bronbewijs. De vindplaats staat in het zijbestand
        # (`oj_reference`), waar `extract_meta.py` hem ook leest.
        self.u.blok("---")

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
        m = re.match(r"(HOOFDSTUK|AFDELING|TITEL|DEEL|ONDERAFDELING)\s+(\S+)", kop, re.I)
        if not m:
            return "", None, ""
        soort, nr = m.group(1).upper(), nummer_anker(m.group(2), romeins_omrekenen=True)
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
                self.lid(kind, nr, f"{anker}-{nummer_anker(nr)}" if nr else anker)
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
            if el.find("LIST") is not None or el.find("TBL") is not None or el.find("DLIST") is not None:
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
                self.u.eenheid(anker, "punt", f"{nr} {txt}")
            for kind in el:
                if kind.tag not in ("NO.P", "TXT"):
                    self.inhoud(kind, anker or basis, {"lijsten": 0})
        elif tag in ("LIST", "DLIST"):
            genummerd = tag == "LIST" and (el.get("TYPE", "").upper() not in ONGENUMMERD)
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
        elif tag in METADATA:
            return
        else:
            self.onbekend("inhoud", el)

    def lijst(self, el, basis: str, extra: str) -> None:
        """Elk onderdeel is een eigen alinea: `a) tekst`, niet een Markdown-lijst."""
        if el.tag == "DLIST":
            for item in el.findall("DLIST.ITEM"):
                term = ws(self.inline(item.find("TERM"))) if item.find("TERM") is not None else ""
                prefix = ws(self.inline(item.find("PREFIX"))) if item.find("PREFIX") is not None else ""
                definitie = ws(self.inline(item.find("DEFINITION"))) if item.find("DEFINITION") is not None else ""
                regel = " ".join(x for x in (prefix, term, definitie) if x)
                self.u.blok(regel)
                if basis and prefix:
                    self.u.eenheid(f"{basis}-{nummer_anker(prefix)}", "onderdeel", regel)
            return
        genummerd = el.get("TYPE", "").upper() not in ONGENUMMERD
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

    # ------------------------------------------------------------ tabellen

    def cel_tekst(self, cel) -> str:
        delen = []
        if (cel.text or "").strip():
            delen.append(cel.text)
        for kind in cel:
            if kind.tag == "LIST":
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
                nr = ws(self.inline(kind.find("NO.P"))) if kind.find("NO.P") is not None else ""
                txt = ws(self.inline(kind.find("TXT"))) if kind.find("TXT") is not None else ""
                delen.append(f"{nr} {txt}".strip())
            elif kind.tag in ("P", "ALINEA"):
                delen.append(self.cel_tekst(kind))
            else:
                delen.append(self.inline_el(kind))
            if (kind.tail or "").strip():
                delen.append(kind.tail)
        return ws(" ".join(d for d in delen if d))

    def tabel(self, el) -> None:
        if any(kind is not el for kind in el.iter("TBL")):
            raise ConversionError(
                "Geneste inhoudstabel vereist afzonderlijke broncontrole; omzetting geweigerd."
            )
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
        anker = f"annex-{nummer_anker(m.group(1), romeins_omrekenen=True)}" if m else f"annex-{self.bijlagen}"
        self.u.blok(f"## {ti.replace(' ', NBSP)}")
        if sti:
            self.u.blok(sti)
        self.u.eenheid(anker, "bijlage", f"{ti} {sti}".strip())
        inhoud = root.find("CONTENTS")
        if inhoud is not None:
            self.bijlage_inhoud(inhoud, anker)
        self.notenblok()

    def bijlage_inhoud(self, el, anker: str) -> None:
        teller = {"lijsten": 0}
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
                else:
                    ti = ws(self.inline(titel)) if titel is not None else ""
                m = re.match(r"([A-Z]|\d+)\.", ti)
                sub = f"{anker}-{nummer_anker(m.group(1))}" if m else anker
                if ti:
                    self.u.blok(ti)
                    if m:
                        self.u.eenheid(sub, "bijlagedeel", ti)
                wrapper = ET.Element("x")
                wrapper.extend([c for c in kind if c.tag != "TITLE"])
                self.bijlage_inhoud(wrapper, sub)
            elif kind.tag in METADATA:
                continue
            else:
                self.inhoud(kind, basis=anker, teller=teller)


def omzetten(data: bytes, basis: bytes | None = None) -> tuple[str, list, dict, dict]:
    """Zet één ongewijzigde Cellar-zip om naar de EUR-Lex-raw-vorm.

    `basis` is de Cellar-zip van de basishandeling achter een geconsolideerde
    tekst; alleen haar considerans komt mee, vóór de bepalingen.
    """
    o = FormexOmzetter()
    u = o.omzetten(data, basis)
    return u.markdown(), u.eenheden, u.onbekend, {"herhaalde_cellen": o.herhaalde_cellen,
                                                  "metadata": o.metadata}
