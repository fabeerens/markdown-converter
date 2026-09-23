"""Arresten en beschikkingen van het Hof van Justitie en het Gerecht, uit Formex.

De Cellar levert rechtspraak van de Unie als Formex-zip
(`Accept: application/zip;mtype=fmx4`, `Accept-Language: nld`), maar in een
andere vorm dan wetgeving: geen `.doc.xml`, één XML met als wortel `JUDGMENT` of
`ORDER`, en een inhoud die uit genummerde alinea's (`NP.ECR`) bestaat in plaats
van uit artikelen. Daarom staat deze route los van `formex_xml.py`, dat aan
`ACT` gebonden is; gedeeld zijn alleen `xml_gedeeld.Uitvoer` en `tabel_markdown`.

**De raw-vorm is die waar `md-clean-jurisprudentie` (variant `hvj`) al op leunt**,
niet een mooiere (AGENTS.md, regel 3):

- de titel op de eerste regel in kapitalen (`HT TYPE="UC"`), gevolgd door de datum;
- een sectietitel als kale regel, zonder `#` - de bron toont de diepte niet en het
  profiel wil ze op één niveau;
- de kale regel `Arrest` (of `Beschikking`) als begin van het lichaam;
- een overweging als `63.` plus één gewone spatie, een geciteerd lid uit een andere
  handeling als `2.` plus **drie harde spaties**. Dat ene verschil is wat een
  citaat van een rechtsoverweging onderscheidt;
- het dictum als alinea's in plaats van als lay-outtabel: dat is de bron, en het
  laat `table-beslissing` vervallen;
- de procestaalnoot als echte definitie (`[^procestaal]: Procestaal: Engels.`).

Wat deze route weigert in plaats van raadt: een wortel die geen arrest of
beschikking is (conclusies, adviezen en vergaderverslagen hebben een andere
opbouw), een zip met meer dan één onderdeel of met een afbeelding, een element
zonder eigen behandeling, een nootvorm die hier niet bekend is, en een
genummerde lijst waarvan de nummers niet in de bron staan.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

from ..errors import ConversionError
from . import xml_gedeeld as xg

NBSP = " "

ONDERSTEUND = {"JUDGMENT", "ORDER"}
# Paginakop en bibliografische velden: ze staan in de bron maar horen niet in de
# tekst van de uitspraak. Ze gaan naar de herkomst, zodat er niets stil verdwijnt.
NIET_UITGEVOERD = {"BIB.JUDGMENT", "BIB.ORDER", "CURR.TITLE"}

# Inline: de tekst loopt door in de alinea eromheen.
INLINE = {"HT", "DATE", "REF.DOC.OJ", "REF.DOC.ECR", "NO.CASE", "NAME.CASE",
          "REF.NP.ECR", "FT", "QUOT.START", "QUOT.END", "NOTE", "SUP", "NO.ECLI",
          "IE"}

# De aanhalingstekens die Formex met een code aanduidt. Een andere code is een
# weigering: een verkeerd teken is een veranderde tekst.
AANHALING = {"201E": "„", "201D": "”", "201C": "“", "201A": "‚",
             "2018": "‘", "2019": "’", "00AB": "«", "00BB": "»"}

# Een lijst waarvan het teken uit de soort volgt. Andere soorten dragen hun nummers
# in `NP/NO.P`; staan die er niet, dan zou de renderer nummeren.
LIJSTTEKEN = {"DASH": "- ", "NDASH": "- ", "BULLET": "- ", "DISC": "- ", "NONE": ""}

_ROMEINS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]

# Lange bladalinea's die in de uitvoer aaneengesloten moeten voorkomen.
BLADALINEA = {"P", "TXT", "VISA", "KEYWORD", "AGAINST", "PARTY.STATUS", "PREAMBLE.FINAL",
              "INTRO", "ITEM.CONT", "ITEM.REF", "TI.ART", "STI.ART", "NO.P"}
BLOKKEN = {"P", "LIST", "QUOT.S", "NP", "NP.ECR", "ALINEA", "PARAG", "ARTICLE", "TBL",
           "TITLE", "GR.SEQ", "ITEM", "TOC", "INDEX", "SIGNATORY", "JURISDICTION"}


def _kort(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else "?"


def _norm(tekst: str) -> str:
    """Witruimte plat, maar de harde spaties blijven: `Artikel 3` is brontekst.

    `str.split()` zou ook U+00A0 als witruimte behandelen en dus elke harde spatie
    uit de tekst halen; het profiel leunt juist op het verschil tussen een gewone en
    een harde spatie.
    """
    return re.sub(r"[ \t\r\n]+", " ", tekst).strip(" \t\r\n")


def _woorden(tekst: str) -> list[str]:
    tekst = re.sub(r"\[\^[^\]]+\]:?", " ", tekst)
    return re.findall(r"\w+", tekst.lower(), re.UNICODE)


def _fout(boodschap: str) -> ConversionError:
    return ConversionError(f"Formex-uitspraak geweigerd: {boodschap}")


# --------------------------------------------------------------------------
# De zip openen
# --------------------------------------------------------------------------

def openen(data: bytes) -> tuple[str, ET.Element]:
    """Precies één XML-onderdeel, met een ondersteunde wortel.

    Anders dan bij wetgeving is er geen inhoudsopgave die de onderdelen noemt, dus
    de eis is strenger: een tweede onderdeel (bij oudere arresten het verslag ter
    terechtzitting) of een afbeelding is een weigering, want dat zou tekst buiten
    de omzetting laten.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise _fout(f"de download is geen leesbare zip ({exc})") from exc
    with zf:
        onderdelen = [i for i in zf.infolist() if not i.is_dir()]
        xmls = [i for i in onderdelen if i.filename.lower().endswith(".xml")]
        overig = [i.filename for i in onderdelen if not i.filename.lower().endswith(".xml")]
        if overig:
            raise _fout("de zip bevat naast de XML ook " + ", ".join(sorted(overig))
                        + "; een afbeelding of bijlage zou buiten de omzetting blijven")
        if len(xmls) != 1:
            raise _fout(f"de zip bevat {len(xmls)} XML-onderdelen; precies één is vereist "
                        "(bij oudere arresten hoort het verslag ter terechtzitting erbij)")
        naam = xmls[0].filename.rsplit("/", 1)[-1]
        try:
            root = ET.fromstring(zf.read(xmls[0]))
        except ET.ParseError as exc:
            raise _fout(f"{naam} is niet leesbaar ({exc})") from exc
    soort = _kort(root)
    if soort not in ONDERSTEUND:
        raise _fout(f"een document van de soort {soort} wordt niet ondersteund; "
                    "alleen arresten (JUDGMENT) en beschikkingen (ORDER)")
    return naam, root


# --------------------------------------------------------------------------
# Metadata en identiteit
# --------------------------------------------------------------------------

def metadata(root: ET.Element, naam: str) -> dict:
    bib = root.find("BIB.JUDGMENT") if _kort(root) == "JUDGMENT" else root.find("BIB.ORDER")
    if bib is None:
        raise _fout("het document heeft geen bibliografisch blok")
    celex = (bib.findtext("NO.CELEX") or "").strip()
    ecli_el = bib.find("NO.ECLI")
    ecli = (ecli_el.get("ECLI") or "").strip() if ecli_el is not None else ""
    if not celex:
        raise _fout("het bibliografische blok noemt geen CELEX-nummer")
    zaken = [(n.text or "").strip() for n in bib.iter("NO.CASE") if (n.text or "").strip()]
    taal = re.search(r"_([A-Z]{2})_\d+\.xml$", naam)
    kop = root.find("CURR.TITLE")
    paginakop = [_norm("".join(p.itertext())) for p in kop.iter("P")] if kop is not None else []
    return {
        "celex": celex,
        "ecli": ecli or None,
        "zaaknummers": zaken,
        "auteur": (bib.findtext("AUTHOR") or "").strip() or None,
        "taal": taal.group(1).lower() if taal else None,
        "paginakop": [p for p in paginakop if p],
        "soort": _kort(root),
    }


# --------------------------------------------------------------------------
# De omzetting
# --------------------------------------------------------------------------

class _Omzetter:
    def __init__(self) -> None:
        self.uit = xg.Uitvoer()
        self.tellers: Counter = Counter()
        self.labels: set[str] = set()
        self.noten: list[tuple[str, str]] = []
        self.titels: list[str] = []
        self.sectietitels: list[str] = []
        self.titelregels: list[str] = []
        self.dictum_inleiding: str | None = None
        self.citaatdiepte = 0
        self.lijstdiepte = 0
        self.opmaak = 0
        self.gegenereerd: Counter = Counter()

    # -- inline ---------------------------------------------------------

    def inline(self, el) -> str:
        naam = _kort(el)
        if naam in ("QUOT.START", "QUOT.END"):
            code = (el.get("CODE") or "").upper()
            if code not in AANHALING:
                raise _fout(f"onbekende aanhalingscode {code!r}")
            return AANHALING[code]
        if naam == "NOTE":
            return self.noot(el)
        if naam == "FT" and (el.get("TYPE") or "").upper() == "NUMBER":
            cijfers = "".join(el.itertext())
            if cijfers.isdigit() and len(cijfers) > 4:
                groepen = []
                while cijfers:
                    groepen.insert(0, cijfers[-3:])
                    cijfers = cijfers[:-3]
                return NBSP.join(groepen)
        if naam == "IE" and not len(el) and not (el.text or "").strip():
            return ""
        tekst = self.inhoud(el)
        if naam == "HT":
            soort = (el.get("TYPE") or "").upper()
            if soort == "UC":
                return tekst.upper()
            self.opmaak += 1 if soort in ("BOLD", "ITALIC") else 0
        return tekst

    def inhoud(self, el) -> str:
        """De lopende tekst van een element: tekst, inline-kinderen en hun staart."""
        delen = [el.text or ""]
        for kind in el:
            naam = _kort(kind)
            if naam not in INLINE:
                raise _fout(f"element zonder eigen behandeling binnen tekst ({naam})")
            delen.append(self.inline(kind))
            delen.append(kind.tail or "")
        return "".join(delen)

    def noot(self, el) -> str:
        """Een noot als `[^label]`; de definitie komt onderaan.

        Formex zet de noot op de plek van de marker, met haar eigen tekst erin.
        Alleen de vormen die in arresten voorkomen: ster (de procestaal), Arabisch
        en Romeins. Een `NOTE.REF` verwijst naar een noot elders en is een ander
        geval; dat hier raden zou een verkeerde relatie schrijven.
        """
        if el.get("NOTE.REF"):
            raise _fout("een noot met NOTE.REF (verwijzing naar een andere noot) wordt niet ondersteund")
        nummering = (el.get("NUMBERING") or "").upper()
        tekst = _norm(" ".join(_norm(self.inhoud(p)) for p in el.iter("P") if p is not el)
                      or self.inhoud(el))
        if not tekst:
            raise _fout("een noot zonder tekst")
        self.tellers[nummering] += 1
        n = self.tellers[nummering]
        if nummering == "STAR":
            label = "procestaal" if re.match(r"(?i)procestaal\b", tekst) else f"ster{n}"
        elif nummering == "ARAB":
            label = str(n)
        elif nummering == "ROMAN":
            if n > len(_ROMEINS):
                raise _fout("meer Romeinse noten dan de omzetter kent")
            label = _ROMEINS[n - 1]
        else:
            raise _fout(f"onbekende nootnummering {el.get('NUMBERING')!r}")
        if label in self.labels:
            raise _fout(f"dubbel nootlabel {label!r}")
        self.labels.add(label)
        self.noten.append((label, tekst))
        return f"[^{label}]"

    # -- blokken --------------------------------------------------------

    def gemengd(self, el) -> list[str]:
        """Tekst en inline-kinderen vormen een alinea; een blokkind is een eigen blok."""
        blokken: list[str] = []
        buf = [el.text or ""]

        def leeg() -> None:
            tekst = _norm("".join(buf))
            buf.clear()
            if tekst:
                blokken.append(tekst)

        for kind in el:
            if _kort(kind) in INLINE:
                buf.append(self.inline(kind))
            else:
                leeg()
                blokken.extend(self.blok(kind))
            buf.append(kind.tail or "")
        leeg()
        return blokken

    def blok(self, el) -> list[str]:
        naam = _kort(el)
        if naam in NIET_UITGEVOERD:
            return []
        if naam in ("INCL.ELEMENT",):
            raise _fout("het document bevat een afbeelding (INCL.ELEMENT); de keten heeft "
                        "daar geen route voor en stil weglaten zou inhoud verliezen")
        handler = {
            "TITLE": self.titel,
            "GR.SEQ": self.reeks,
            "NP.ECR": self.genummerd,
            "NP": self.nummerpunt,
            "LIST": self.lijst,
            "QUOT.S": self.citaat,
            "PARAG": self.lid,
            "ARTICLE": self.artikel,
            "INDEX": self.trefwoorden,
            "TOC": self.inhoudsopgave,
            "JURISDICTION": self.dictum,
            "SIGNATURE.CASE": self.ondertekening,
            "TBL": self.tabel,
        }.get(naam)
        if handler is not None:
            return handler(el)
        if naam in _CONTAINERS or naam in ("P", "TXT", "TI", "STI", "ALINEA", "ITEM",
                                            "SIGNATORY"):
            return self.gemengd(el)
        self.uit.markeer_onbekend(naam)
        return []

    def titel(self, el) -> list[str]:
        """De titel van het document: de soort en het gerecht, dan de datum."""
        blokken = self.gemengd(el)
        self.titels.extend(blokken)
        self.titelregels.extend(_norm(re.sub(r"\[\^[^\]]+\]", "", b)) for b in blokken)
        return blokken

    def reeks(self, el) -> list[str]:
        """Een sectie: haar titel wordt een kale regel, en telt als kop."""
        uit: list[str] = []
        for kind in el:
            if _kort(kind) == "TITLE":
                regels = self.gemengd(kind)
                if regels:
                    self.sectietitels.append(" ".join(regels))
                uit.extend(regels)
            else:
                uit.extend(self.blok(kind))
        return uit

    def _nummer(self, el) -> str:
        no = el.find("NO.P")
        if no is None:
            return ""
        return _norm(self.inhoud(no))

    def _met_voorvoegsel(self, voorvoegsel: str, el, sla_over=("NO.P",)) -> list[str]:
        blokken: list[str] = []
        buf = [el.text or ""]
        eerste = [True]

        def leeg() -> None:
            tekst = _norm("".join(buf))
            buf.clear()
            if tekst:
                blokken.append((voorvoegsel + tekst) if eerste[0] else tekst)
                eerste[0] = False

        for kind in el:
            naam = _kort(kind)
            if naam in sla_over:
                buf.append(kind.tail or "")
                continue
            if naam in INLINE:
                buf.append(self.inline(kind))
            else:
                leeg()
                for b in self.blok(kind):
                    blokken.append((voorvoegsel + b) if eerste[0] else b)
                    eerste[0] = False
            buf.append(kind.tail or "")
        leeg()
        if eerste[0] and voorvoegsel.strip():
            blokken.append(voorvoegsel.strip())
        return blokken

    def genummerd(self, el) -> list[str]:
        """`NP.ECR`: een overweging, `63.` plus één gewone spatie."""
        nr = self._nummer(el)
        if not nr.isdigit():
            raise _fout(f"een overweging met een niet-numeriek nummer ({nr!r})")
        return self._met_voorvoegsel(f"{nr}. ", el)

    def nummerpunt(self, el) -> list[str]:
        """`NP` in een lijst of het dictum: het nummer staat in de bron, met leesteken.

        Staat het punt **in een citaat of in een lijst** (een opsomming met de nummers
        van een bijlage: `12. Wijzigingen in projecten van bijlage I`), dan is het geen
        rechtsoverweging en krijgt het dezelfde vorm als een geciteerd lid: het
        nummer, een punt en drie harde spaties. Zonder dat
        is het niet van een rechtsoverweging te onderscheiden, en het profiel kende
        `ro-12` toe aan het citaat in plaats van aan de echte overweging 12 (gemeten
        op 61995CJ0072, 61991TJ0092 en 61997CO0151).
        """
        nr = self._nummer(el)
        if not nr:
            return self.gemengd(el)
        # Binnen een citaat schrijft de bron het nummer al met punt (`12.`) en soms met
        # het aanhalingsteken erin (`„10.`); in de lopende tekst staat het kaal (`12`).
        m = re.fullmatch(r"([\u201e\u201c\u201a\u2018\u00ab]?)(\d+)\.?", nr)
        if m and (self.citaatdiepte or self.lijstdiepte):
            return self._met_voorvoegsel(f"{m.group(1)}{m.group(2)}.{NBSP * 3}", el)
        marker = nr + "." if nr.isdigit() else nr
        return self._met_voorvoegsel(marker + " ", el)

    def lijst(self, el) -> list[str]:
        self.lijstdiepte += 1
        try:
            return self._lijst(el)
        finally:
            self.lijstdiepte -= 1

    def _lijst(self, el) -> list[str]:
        soort = (el.get("TYPE") or "").upper()
        teken = LIJSTTEKEN.get(soort)
        uit: list[str] = []
        for item in el:
            if _kort(item) != "ITEM":
                raise _fout(f"onverwacht element in een lijst ({_kort(item)})")
            blokken = self.gemengd(item)
            if not blokken:
                continue
            if teken is None:
                if not any(_kort(k) == "NP" for k in item):
                    raise _fout(f"een genummerde lijst ({soort}) zonder nummers in de bron; "
                                "de nummering zou van de renderer komen")
                uit.extend(blokken)
                continue
            eerste, rest = blokken[0], blokken[1:]
            uit.append(teken + eerste)
            for b in rest:
                uit.append("\n".join("  " + r if r else r for r in b.split("\n")))
        return uit

    def citaat(self, el) -> list[str]:
        self.citaatdiepte += 1
        try:
            return self.gemengd(el)
        finally:
            self.citaatdiepte -= 1

    def lid(self, el) -> list[str]:
        """`PARAG` in een citaat: `2.` plus **drie harde spaties**."""
        no = el.find("NO.PARAG")
        nr = _norm(self.inhoud(no)) if no is not None else ""
        blokken = []
        eerste = True
        for kind in el:
            naam = _kort(kind)
            if naam == "NO.PARAG":
                continue
            for b in (self.blok(kind) if naam not in INLINE else [self.inline(kind)]):
                if eerste and nr:
                    b = nr + NBSP * 3 + b
                eerste = False
                blokken.append(b)
        if eerste and nr:
            blokken.append(nr)
        return blokken

    def artikel(self, el) -> list[str]:
        return self.gemengd(el)

    def trefwoorden(self, el) -> list[str]:
        woorden = [_norm(self.inhoud(k)) for k in el if _kort(k) == "KEYWORD"]
        if len(woorden) != len([k for k in el]):
            raise _fout("een trefwoordenlijst met iets anders dan KEYWORD")
        scheiding = el.get("SEPARATOR") or " – "
        return [(el.get("IDX.OPEN") or "") + scheiding.join(w for w in woorden if w)
                + (el.get("IDX.CLOSE") or "")]

    def inhoudsopgave(self, el) -> list[str]:
        """Een inhoudsopgave als geneste opsomming: de nesting van de bron blijft staan."""
        uit: list[str] = []

        def blk(blok, diepte: int) -> None:
            for kind in blok:
                naam = _kort(kind)
                if naam == "TOC.ITEM":
                    delen = [_norm(self.inhoud(d)) for d in kind
                             if _kort(d) in ("NO.ITEM", "ITEM.CONT", "ITEM.REF")]
                    if len(delen) != len(list(kind)):
                        raise _fout("een inhoudsopgave-item met iets anders dan nummer, "
                                    "tekst en pagina")
                    uit.append("  " * diepte + "- " + " ".join(d for d in delen if d))
                elif naam == "TOC.BLK":
                    blk(kind, diepte + 1)
                else:
                    raise _fout(f"onbekend element in een inhoudsopgave ({naam})")

        for kind in el:
            naam = _kort(kind)
            if naam == "TITLE":
                uit.extend(self.gemengd(kind))
            elif naam == "TOC.BLK":
                blk(kind, 0)
            else:
                raise _fout(f"onbekend element in een inhoudsopgave ({naam})")
        return uit

    def ondertekening(self, el) -> list[str]:
        """De ondertekeningsgroep is in de bron één blok en wordt één alinea.

        Bij het Gerecht staan daar de namen van de rechters, de regel `Uitgesproken
        ter openbare terechtzitting …` en tot slot `ondertekeningen`. Dat hoort bij
        de uitspraak maar niet bij de beslissing. Als één alinea (regels met enkel
        regeleinden, geen lege regel ertussen) is de grens voor `outcome` in de
        kennisbank eenduidig: de alinea waarin `ondertekeningen` staat valt erbuiten.
        """
        regels: list[str] = []
        for kind in el:
            for b in self.blok(kind):
                if b.strip():
                    regels.append(b)
        return ["\n".join(regels)] if regels else []

    def dictum(self, el) -> list[str]:
        uit: list[str] = []
        for kind in el:
            naam = _kort(kind)
            if naam == "INTRO":
                # De inleiding is één regel; het profiel herkent het dictum eraan.
                regel = _norm(" ".join(self.gemengd(kind)))
                self.dictum_inleiding = regel
                uit.append(regel)
            else:
                uit.extend(self.blok(kind))
        return uit

    def tabel(self, el) -> list[str]:
        corpus = [c for c in el if _kort(c) == "CORPUS"]
        if len(corpus) != 1:
            raise _fout("een tabel zonder precies één CORPUS")
        rijen = []
        for rij in corpus[0]:
            if _kort(rij) != "ROW":
                raise _fout(f"onverwacht element in een tabel ({_kort(rij)})")
            cellen = []
            for cel in rij:
                if _kort(cel) != "CELL":
                    raise _fout(f"onverwacht element in een tabelrij ({_kort(cel)})")
                tekst = _norm(" ".join(self.gemengd(cel)))
                kol = cel.get("COL")
                rs, cs = int(cel.get("ROWSPAN") or 1), int(cel.get("COLSPAN") or 1)
                if rs * cs > 1:
                    self.gegenereerd.update(_woorden(tekst) * (rs * cs - 1))
                cellen.append({"tekst": tekst, "kol": int(kol) - 1 if kol else None,
                               "rowspan": rs, "colspan": cs})
            rijen.append(cellen)
        markdown, _ = xg.tabel_markdown(rijen, 0)
        return [markdown]


# Elementen die alleen blokken bevatten of een alinea vormen; de gemengde
# afhandeling regelt de rest.
_CONTAINERS = {
    "JUDGMENT", "ORDER", "JUDGMENT.INIT", "ORDER.INIT", "PARTIES", "PLAINTIFS", "DEFENDANTS",
    "INTERVENERS", "APPELANT", "AGAINST", "PARTY.STATUS", "SUBJECT", "PREAMBLE",
    "PRESENCE", "PRESENCE.INIT",
    "PREAMBLE.GEN", "PREAMBLE.INIT", "PREAMBLE.FINAL", "GR.VISA", "GR.CONSID", "CONSID",
    "VISA", "CONTENTS.JUDGMENT", "CONTENTS.ORDER", "INTERMEDIATE", "SIGNATURE.CASE",
    "TI.ART", "STI.ART", "INTRO",
}


# --------------------------------------------------------------------------
# De bron zelf tellen, los van de omzetter
# --------------------------------------------------------------------------

def _plat(el) -> str:
    """Zichtbare tekst van een element, zonder de omzetter te gebruiken.

    Een `NOTE` telt niet mee in de alinea waar hij staat: in de uitvoer is dat een
    marker en staat zijn tekst in de definitie onderaan. Zijn eigen tekst wordt
    apart als bladalinea gecontroleerd.
    """
    naam = _kort(el)
    if naam in NIET_UITGEVOERD:
        return ""
    uit = el.text or ""
    for kind in el:
        n = _kort(kind)
        if n == "NOTE":
            uit += " " + (kind.tail or "")
            continue
        if n in ("QUOT.START", "QUOT.END"):
            stuk = AANHALING.get((kind.get("CODE") or "").upper(), "")
        elif n == "FT" and (kind.get("TYPE") or "").upper() == "NUMBER":
            cijfers = "".join(kind.itertext())
            if cijfers.isdigit() and len(cijfers) > 4:
                groepen = []
                while cijfers:
                    groepen.insert(0, cijfers[-3:])
                    cijfers = cijfers[:-3]
                stuk = NBSP.join(groepen)
            else:
                stuk = _plat(kind)
        else:
            stuk = _plat(kind)
        # Een inline-element (HT, DATE, REF.DOC.ECR, ...) is opmaak of een verwijzing
        # midden in de zin, en de omzetting schrijft zijn tekst er zonder spatie
        # omheen. Alleen een blok (P, TXT, NO.P, ...) is een woordgrens. Gemeten op
        # 61986CO0279: de bron schrijft `zaak 1<REF.DOC.ECR>18/77</REF.DOC.ECR>`, een
        # zaaknummer dat midden door een element loopt, en `118/77` is de juiste lezing.
        uit += stuk if n in INLINE else f" {stuk} "
        uit += kind.tail or ""
    return uit


def _bronwoorden(root: ET.Element) -> Counter:
    teller: Counter = Counter()
    teller.update(_woorden(_plat(root)))
    for note in root.iter("NOTE"):
        teller.update(_woorden(_plat(note)))
    return teller


def _bladalineas(root: ET.Element) -> list[list[str]]:
    uit = []

    def loop(el) -> None:
        if _kort(el) in NIET_UITGEVOERD:
            return
        if _kort(el) in BLADALINEA and not any(k is not el and _kort(k) in BLOKKEN
                                               for k in el.iter()):
            w = _woorden(_plat(el))
            if w:
                uit.append(w)
        for kind in el:
            loop(kind)

    loop(root)
    return uit


def _bevat(reeks: list[str], deel: list[str]) -> bool:
    eerste = deel[0]
    for i, w in enumerate(reeks):
        if w == eerste and reeks[i:i + len(deel)] == deel:
            return True
    return False


def _zelfcontrole(root: ET.Element, omzetter: _Omzetter, markdown: str) -> None:
    """Weigeren als de omzetting tekst heeft verloren, verweven of verdubbeld.

    Dezelfde twee controles als bij wetgeving, en om dezelfde reden: elke
    tekstdragende bladalinea uit de bron staat **aaneengesloten** in de uitvoer
    (dat vangt afbreken en verweven), en de woordverzameling is als multiset gelijk
    (dat vangt verlies en verdubbeling). De bron wordt daarvoor met een eigen
    traversal geteld, niet met de code die de Markdown bouwde.
    """
    uit = _woorden(markdown)
    uit_teller = Counter(uit)
    bron = _bronwoorden(root)
    bron.update(omzetter.gegenereerd)
    if bron != uit_teller:
        tekort, teveel = bron - uit_teller, uit_teller - bron
        raise _fout("de omzetting mist of verdubbelt tekst ten opzichte van de bron "
                    f"(ontbreekt: {dict(list(tekort.items())[:6])}; te veel: "
                    f"{dict(list(teveel.items())[:6])})")
    for deel in _bladalineas(root):
        if not _bevat(uit, deel):
            raise _fout("een alinea uit de bron staat niet aaneengesloten in de uitvoer: "
                        + " ".join(deel[:8]))


def _oud_celex(celex: str) -> str:
    """`62003CJ0436` in de schrijfwijze van vóór de aparte gerechtsletter: `62003J0436`.

    De Cellar antwoordt op beide, en een arrest uit die tijd noemt zichzelf in de
    oude vorm. Alleen de `C` van het Hof valt weg; het Gerecht (`T`) heeft in de
    oude vorm een eigen letter en wordt hier niet aangeraakt.
    """
    return re.sub(r"^(6\d{4})C([A-Z]\d{4}.*)$", r"\1\2", celex)


def omzetten(data: bytes, verwacht: str | None = None) -> tuple[str, dict]:
    """De uitspraak als Markdown, plus wat de herkomst erover vastlegt.

    `verwacht` is de CELEX of ECLI die de gebruiker vroeg; de bron moet zeggen dat
    zij die is. Een document dat binnenkomt maar iemand anders is, is een weigering
    en geen terugval: anders levert een identiteitsfout stilletjes een ander
    arrest op.
    """
    naam, root = openen(data)
    meta = metadata(root, naam)
    if verwacht:
        v = verwacht.strip().upper()
        if v not in {meta["celex"].upper(), (meta["ecli"] or "").upper()} \
                and _oud_celex(v) != meta["celex"].upper():
            raise _fout(f"de bron is {meta['celex']} ({meta['ecli'] or 'zonder ECLI'}) en niet {verwacht}")

    omzetter = _Omzetter()
    for kind in root:
        if _kort(kind) in NIET_UITGEVOERD:
            continue
        for b in omzetter.blok(kind):
            omzetter.uit.blok(b)
    for label, tekst in omzetter.noten:
        omzetter.uit.noten.append((label, tekst))
    markdown = omzetter.uit.markdown()
    _zelfcontrole(root, omzetter, markdown)

    # Elke sectie met een titel levert precies één kopregel op.
    gr = [g for g in root.iter("GR.SEQ") if g.find("TITLE") is not None]
    if len(gr) != len(omzetter.sectietitels):
        raise _fout(f"{len(gr)} secties met een titel in de bron, maar "
                    f"{len(omzetter.sectietitels)} kopregels in de uitvoer")

    procestaal = next((re.sub(r"(?i)^procestaal:\s*", "", t).strip(" .") for label, t in omzetter.noten
                       if label == "procestaal"), None)
    titel = root.find("TITLE")
    datum = next((d.get("ISO") for d in (titel.iter("DATE") if titel is not None else ())
                  if re.fullmatch(r"\d{8}", d.get("ISO") or "")), None)
    hoofd, overig = _partijen(root)
    meta.update({
        "partijen": hoofd,
        "overige_partijen": overig,
        "titelregels": omzetter.titelregels,
        "datum": f"{datum[:4]}-{datum[4:6]}-{datum[6:]}" if datum else None,
        "procestaal": procestaal,
        "dictum_inleiding": omzetter.dictum_inleiding,
        "titelregel": omzetter.titels[0] if omzetter.titels else None,
        "secties": omzetter.sectietitels,
        "noten": len(omzetter.noten),
        "opmaak_weggelaten": omzetter.opmaak,
        "bronbestand": naam,
    })
    return markdown, meta


def _partijen(root: ET.Element) -> tuple[list[str], list[str]]:
    """De partijen zoals de opmaak van de bron ze aanwijst: het vetgedrukte deel.

    Formex zet de naam van een partij in `HT TYPE="BOLD"` en de gemachtigden er in
    gewone tekst achter. Een alinea zonder vetgedrukte naam (`in tegenwoordigheid
    van:`) is geen partij maar een scheiding: alles wat erna komt zijn de overige
    partijen, de intervenienten. Dat is opmaak en volgorde in de bron en geen
    ontleding van proza; de tekst van de scheiding zelf wordt niet gelezen.

    Een nieuwe `PLAINTIFS`-, `DEFENDANTS`- of `APPELANT`-groep begint weer bij de
    hoofdpartijen: de scheiding geldt alleen binnen de eigen groep, en na
    `INTERVENERS` volgen de overige partijen tot de volgende hoofdgroep. In een
    hogere voorziening (C-413/23 P, ECLI:EU:C:2025:645) staat een interveniënt
    tussen rekwirant en verweerder (`PLAINTIFS, INTERVENERS, AGAINST, DEFENDANTS,
    INTERVENERS`); zonder de terugzet belandde de GAR, de verweerder, bij de
    overige partijen. Op de 8 Formex-zips van 23 september 2026 verandert alleen
    die uitspraak.
    """
    hoofd: list[str] = []
    overig: list[str] = []
    partijen = root.find("PARTIES")
    if partijen is None:
        return hoofd, overig
    doel = hoofd
    for groep in partijen:
        if _kort(groep) not in ("PLAINTIFS", "DEFENDANTS", "INTERVENERS", "APPELANT"):
            continue
        if _kort(groep) != "INTERVENERS":
            doel = hoofd
        for p in groep.iter("P"):
            namen = [_norm("".join(ht.itertext())).rstrip(" ,;") for ht in p.iter("HT")
                     if (ht.get("TYPE") or "").upper() == "BOLD"]
            namen = [n for n in namen if n]
            if not namen:
                if hoofd:
                    doel = overig
                continue
            for n in namen:
                if n not in hoofd and n not in overig:
                    doel.append(n)
        if _kort(groep) == "INTERVENERS":
            doel = overig
    return hoofd, overig
