"""Word-bestanden (DOCX) als bron voor de kennisbank.

De lezer die eerst alleen voor HUDOC bestond (`hudoc_docx.py`), zonder de kennis van
één uitgever. Een DOCX draagt wat een PDF- of MarkItDown-afgeleide kwijtraakt: koppen
als **stijl** of als `w:outlineLvl`, echte voetnoten in `word/footnotes.xml` en
tabellen als `w:tbl`. Gemeten op het Word-bestand van het EHRM (18 verschillende
`w:pStyle`, een `footnotes.xml`): MarkItDown levert 10.881 woorden met **0** koppen,
de lezer 10.934 woorden met **43** koppen.

**Een `Kaart` zegt wat een stijl betekent.** De lezer (`Docx`, `Lezer`) en de
zelfcontrole zijn voor elk bestand gelijk; alleen de indeling van stijlen verschilt.
`HUDOC_KAART` (in `hudoc_docx.py`) kent de `Ju*`-, `ECHR*`-, `Opi*`- en `Dec*`-stijlen
van het EHRM. `WORD_KAART` (hier) kent geen enkele naam van een uitgever: een kop is
een alinea met een `w:outlineLvl` of een stijl die `Heading N`/`Titel` heet, een gewone
alinea is een stijl die (via `w:basedOn`) op `Normal` teruggaat, en al het andere is
een weigering.

**Weigeren in plaats van raden.** Een stijl die de kaart niet kent, automatische
nummering (het nummer staat niet in de tekst en zou van de renderer komen), een
afbeelding of tekstvak, een eindnoot, een bijgehouden wijziging, een tabel met
samengevoegde cellen of een tabel in een tabel, en een bron waarvan de tekst na
omzetting niet meer woord voor woord klopt: elk is een `ConversionError` met de reden.
Dat is dezelfde lijn als bij Formex en HUDOC.

**Noten native.** Een voetnoot komt als `[^1]` in de tekst en `[^1]: …` onderaan,
doorgenummerd in de volgorde van de verwijzingen zoals Word ze telt. Dat is de vorm
die de kern al aankan (de rechtspraak-route levert hem); het documenten-profiel zelf
koppelt alleen `(n)`-markers als iemand het notenblok aanwijst, en dat is voor een
bron die de relatie kent onnodig raden.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from ..errors import ConversionError
from . import xml_gedeeld as xg

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
NBSP = "\u00a0"

# Symbolen in een symboollettertype: het teken is geen tekst maar een glyph.
SYMBOOL_STERRETJE = ("Symbol", "F02A")
SYMBOOL_BULLET = ("Wingdings", "F09F")

# Elementen die geen tekst dragen en dus overgeslagen mogen worden.
STIL = {"pPr", "bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd", "rPr",
        "lastRenderedPageBreak", "footnoteRef", "sectPr", "tblPr", "tblGrid", "trPr", "tcPr",
        "smartTagPr"}
# Wrappers waarvan de kinderen gewoon doorlopen.
DOORLOPEND = {"hyperlink", "smartTag", "fldSimple", "ins"}
# Wat inhoud kan dragen zonder tekst te zijn. HUDOC-bestanden bevatten dit niet (gemeten
# over 35 uitspraken); een willekeurig Word-document wel, en dan hoort het een weigering
# te zijn en geen stil verlies.
INHOUD_ZONDER_ROUTE = {"drawing", "pict", "object", "AlternateContent"}


def _fout(naam: str, boodschap: str) -> ConversionError:
    return ConversionError(f"{naam} geweigerd: {boodschap}")


def _k(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else "?"


def _norm(tekst: str) -> str:
    """Witruimte plat, maar de harde spaties blijven (`Article 3` is brontekst)."""
    return re.sub(r"[ \t\r\n]+", " ", tekst).strip(" \t\r\n")


_MARKER = re.compile(r"\[\^[^\]]+\]:?")


def _woorden(tekst: str) -> list[str]:
    return re.findall(r"\w+", _MARKER.sub(" ", tekst).lower(), re.UNICODE)


# --------------------------------------------------------------------------
# De kaart: wat een stijl betekent
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Kaart:
    """Hoe stijlen worden gelezen, en welke bijzonderheden van een uitgever meedoen."""

    naam: str
    # ('kop' | 'toc' | 'alinea' | None) voor een alinea; None is een weigering.
    classificeer: Callable[["Docx", ET.Element, str], str | None]
    # De opmaak voor een kop, bijvoorbeeld `## `.
    kopprefix: Callable[["Docx", ET.Element, str], str]
    # Het inspringniveau van een inhoudsopgaveregel.
    toc_niveau: Callable[[str], int]
    randnummer: frozenset = frozenset()
    # Een reden om een stijl in zijn geheel te weigeren, of None.
    weiger: Callable[[str], str | None] = lambda stijl: None
    # De controle op een bestand waarin elke gedrukte regel een alinea is (alleen gemeten
    # en ingesteld voor HUDOC).
    regelbestand: bool = False


# --------------------------------------------------------------------------
# Het bestand openen
# --------------------------------------------------------------------------

class Docx:
    def __init__(self, data: bytes, naam: str) -> None:
        self.naam = naam
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise _fout(self.naam, f"het bestand is geen leesbare DOCX ({exc})") from exc
        with zf:
            namen = set(zf.namelist())
            if "word/document.xml" not in namen:
                raise _fout(self.naam, "het bestand bevat geen word/document.xml")
            try:
                self.root = ET.fromstring(zf.read("word/document.xml"))
                self.noten = (ET.fromstring(zf.read("word/footnotes.xml"))
                              if "word/footnotes.xml" in namen else None)
                self.nummering = (ET.fromstring(zf.read("word/numbering.xml"))
                                  if "word/numbering.xml" in namen else None)
                self.stijlen = (ET.fromstring(zf.read("word/styles.xml"))
                                if "word/styles.xml" in namen else None)
            except ET.ParseError as exc:
                raise _fout(self.naam, f"een onderdeel van de DOCX is niet leesbaar ({exc})") from exc
        self.body = self.root.find(W + "body")
        if self.body is None:
            raise _fout(self.naam, "de DOCX heeft geen body")
        self._nootdefinities = self._lees_noten()
        self._numfmt = self._lees_numfmt()
        self._stijl_num = self._lees_stijl_numpr()
        self._stijldefs = self._lees_stijldefs()
        self.nummering_def = self._lees_nummering()

    def _lees_noten(self) -> dict[str, ET.Element]:
        uit: dict[str, ET.Element] = {}
        if self.noten is None:
            return uit
        for n in self.noten.findall(W + "footnote"):
            soort = n.get(W + "type")
            if soort in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            uit[n.get(W + "id")] = n
        return uit

    def _lees_numfmt(self) -> dict[tuple[str, str], str]:
        """(numId, ilvl) -> numFmt, uit numbering.xml."""
        uit: dict[tuple[str, str], str] = {}
        if self.nummering is None:
            return uit
        abstract: dict[tuple[str, str], str] = {}
        for a in self.nummering.findall(W + "abstractNum"):
            for lvl in a.findall(W + "lvl"):
                fmt = lvl.find(W + "numFmt")
                abstract[(a.get(W + "abstractNumId"), lvl.get(W + "ilvl"))] = (
                    fmt.get(W + "val") if fmt is not None else "?")
        for num in self.nummering.findall(W + "num"):
            a = num.find(W + "abstractNumId")
            if a is None:
                continue
            for (aid, ilvl), fmt in abstract.items():
                if aid == a.get(W + "val"):
                    uit[(num.get(W + "numId"), ilvl)] = fmt
        return uit

    def _lees_nummering(self) -> dict:
        """Wat `numbering.xml` per lijst zegt: de niveaus van elke `abstractNum` en per
        `num` de `abstractNum` met haar overschrijvingen.

        `{"abstract": {(abstractId, ilvl): {fmt, text, start}}, "num": {numId: (abstractId,
        {ilvl: {"start": int | None, "lvl": {fmt, text, start} | None}})}}`. Een
        `lvlRestart` is in de twaalf gemeten EHRM-bestanden nergens gezien en blijft een
        weigering in `Teller`.
        """
        uit: dict = {"abstract": {}, "num": {}, "lvlRestart": False, "numStyleLink": {}, "styleLink": {}}
        if self.nummering is None:
            return uit

        def niveau(lvl) -> dict:
            fmt, tekst, start = lvl.find(W + "numFmt"), lvl.find(W + "lvlText"), lvl.find(W + "start")
            if lvl.find(W + "lvlRestart") is not None:
                uit["lvlRestart"] = True
            return {"fmt": fmt.get(W + "val") if fmt is not None else None,
                    "text": tekst.get(W + "val") if tekst is not None else None,
                    "start": int(start.get(W + "val")) if start is not None
                    and (start.get(W + "val") or "").lstrip("-").isdigit() else 1}

        for a in self.nummering.findall(W + "abstractNum"):
            # Een abstractNum die naar een nummeringsstijl verwijst (`numStyleLink`) heeft
            # zelf geen niveaus; die staan bij de abstractNum met de `styleLink` van
            # dezelfde naam. Zo hangt het dictum van Glukhin, Hurbain en Podchasov aan
            # `ECHRA1StyleList` (gemeten, kb WP-20).
            koppeling, definitie = a.find(W + "numStyleLink"), a.find(W + "styleLink")
            if koppeling is not None:
                uit["numStyleLink"][a.get(W + "abstractNumId")] = koppeling.get(W + "val")
            if definitie is not None:
                uit["styleLink"][definitie.get(W + "val")] = a.get(W + "abstractNumId")
            for lvl in a.findall(W + "lvl"):
                uit["abstract"][(a.get(W + "abstractNumId"), lvl.get(W + "ilvl"))] = niveau(lvl)
        for num in self.nummering.findall(W + "num"):
            a = num.find(W + "abstractNumId")
            if a is None:
                continue
            overschrijvingen = {}
            for o in num.findall(W + "lvlOverride"):
                so, lvl = o.find(W + "startOverride"), o.find(W + "lvl")
                overschrijvingen[o.get(W + "ilvl")] = {
                    "start": int(so.get(W + "val")) if so is not None and (so.get(W + "val") or "").isdigit() else None,
                    "lvl": niveau(lvl) if lvl is not None else None}
            uit["num"][num.get(W + "numId")] = (a.get(W + "val"), overschrijvingen)
        return uit

    def nummering_van(self, p, stijl: str) -> tuple[str, str] | None:
        """`(numId, ilvl)` van een alinea met automatische nummering, of None."""
        np_ = p.find(W + "pPr/" + W + "numPr")
        if np_ is not None:
            nid, il = np_.find(W + "numId"), np_.find(W + "ilvl")
            num, lvl = (nid.get(W + "val") if nid is not None else None,
                        il.get(W + "val") if il is not None else "0")
        elif stijl in self._stijl_num:
            num, lvl = self._stijl_num[stijl]
        else:
            return None
        if num in (None, "0"):
            return None  # numId 0 schakelt de nummering uit
        return num, lvl

    def _lees_stijl_numpr(self) -> dict[str, tuple[str, str]]:
        """Stijlen die zelf een nummering dragen (dan staat er geen numPr in de alinea)."""
        uit: dict[str, tuple[str, str]] = {}
        if self.stijlen is None:
            return uit
        for s in self.stijlen.findall(W + "style"):
            np_ = s.find(W + "pPr/" + W + "numPr")
            if np_ is None:
                continue
            nid = np_.find(W + "numId")
            il = np_.find(W + "ilvl")
            if nid is not None:
                uit[s.get(W + "styleId")] = (nid.get(W + "val"), il.get(W + "val") if il is not None else "0")
        return uit

    def _lees_stijldefs(self) -> dict[str, dict]:
        """Per alineastijl: naam, `basedOn`, `outlineLvl` en of het de standaard is."""
        uit: dict[str, dict] = {}
        if self.stijlen is None:
            return uit
        for s in self.stijlen.findall(W + "style"):
            if s.get(W + "type") != "paragraph":
                continue
            naam = s.find(W + "name")
            basis = s.find(W + "basedOn")
            niveau = s.find(W + "pPr/" + W + "outlineLvl")
            uit[s.get(W + "styleId")] = {
                "naam": (naam.get(W + "val") if naam is not None else "").lower(),
                "basis": basis.get(W + "val") if basis is not None else None,
                "niveau": int(niveau.get(W + "val")) if niveau is not None
                and (niveau.get(W + "val") or "").isdigit() else None,
                "standaard": s.get(W + "default") in ("1", "true"),
            }
        return uit

    def stijlketen(self, stijl: str) -> list[dict]:
        """De stijl en zijn voorouders via `basedOn`; een lus of een onbekende basis is een weigering."""
        keten, gezien, huidig = [], set(), stijl
        while huidig is not None:
            if huidig in gezien:
                raise _fout(self.naam, f"de stijl {stijl} verwijst via basedOn naar zichzelf")
            gezien.add(huidig)
            d = self._stijldefs.get(huidig)
            if d is None:
                break
            keten.append(d)
            huidig = d["basis"]
        return keten

    def stijl(self, p) -> str:
        st = p.find(W + "pPr/" + W + "pStyle")
        return st.get(W + "val") if st is not None else "Normal"

    def numfmt(self, p, stijl: str) -> str | None:
        """Het soort automatische nummering van een alinea, of None."""
        np_ = p.find(W + "pPr/" + W + "numPr")
        if np_ is not None:
            nid = np_.find(W + "numId")
            il = np_.find(W + "ilvl")
            num, lvl = (nid.get(W + "val") if nid is not None else None,
                        il.get(W + "val") if il is not None else "0")
        elif stijl in self._stijl_num:
            num, lvl = self._stijl_num[stijl]
        else:
            return None
        if num in (None, "0"):
            return None  # numId 0 schakelt de nummering uit
        return self._numfmt.get((num, lvl), "?")


# --------------------------------------------------------------------------
# De automatische nummering
# --------------------------------------------------------------------------

def _romeins(n: int) -> str:
    uit = ""
    for waarde, teken in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= waarde:
            uit += teken
            n -= waarde
    return uit


def _letter(n: int) -> str:
    uit = ""
    while n > 0:
        n, rest = divmod(n - 1, 26)
        uit = chr(65 + rest) + uit
    return uit


# De nummervormen die in de twaalf EHRM-bestanden van 25 september 2026 voorkomen, plus
# `decimalZero` uit het Word-sjabloon `ArticleSection`. Wat hier niet staat is niet gemeten.
_VORMEN = {
    "decimal": str,
    "decimalZero": lambda n: f"{n:02d}",
    "upperRoman": _romeins,
    "lowerRoman": lambda n: _romeins(n).lower(),
    "upperLetter": _letter,
    "lowerLetter": lambda n: _letter(n).lower(),
    "none": lambda n: "",
}


class Teller:
    """Rekent het nummer uit dat Word vóór een alinea met `numPr` zou tonen.

    Waarom hier en niet in de tekst: bij HUDOC staat het nummer van een kop (`I.`, `A.`,
    `1.`, `(a)`) sinds 2019 niet meer in de tekst maar in de nummering van de stijl
    (`JuHIRoman` en zusters); vijf van de twaalf arresten van test 2 weigerden erop
    (T2-F19, kb WP-20). De regels zijn die van Word, gemeten op die bestanden:

    - de tellers horen bij de `abstractNum`, niet bij de `num`: twee `num`-instanties op
      dezelfde `abstractNum` tellen door (Glukhin: `I.` en `II.` van THE LAW staan op een
      andere `num` dan `III.` tot en met `VI.`, en de reeks loopt);
    - een `startOverride` herstart dat niveau bij het eerste gebruik van die `num`
      (Big Brother Watch: `I. RELEVANT DOMESTIC LAW` na `III. DOMESTIC PROCEEDINGS`);
    - een alinea op niveau L zet de tellers dieper dan L terug (de `A.` onder een nieuwe
      `I.` begint opnieuw), ook als dat niveau zelf niets toont (`JuHHead`, `numFmt none`);
    - `%n` in `lvlText` is de teller van niveau n, in de vorm van dat niveau.

    De zelfcontrole (`controleer`): binnen één `abstractNum` sluit de reeks per niveau
    aan (I, II, III …) tenzij een hoger niveau ertussen kwam of een `startOverride`
    het nummer zet; anders is de nummering niet begrepen en weigert de omzetting.
    """

    def __init__(self, docx: Docx) -> None:
        self.docx = docx
        self.defs = docx.nummering_def
        self.tellers: dict[str, dict[int, int]] = {}
        self.gezien: set[str] = set()
        self.vorige: dict[tuple[str, int], int] = {}     # (abstract, niveau) -> laatste nummer

    def _definitie(self, abstract: str, ilvl: str) -> dict | None:
        d = self.defs["abstract"].get((abstract, ilvl))
        if d is None and abstract in self.defs["numStyleLink"]:
            doel = self.defs["styleLink"].get(self.defs["numStyleLink"][abstract])
            if doel is not None and doel != abstract:
                d = self.defs["abstract"].get((doel, ilvl))
        return d

    def _niveau(self, num: str, abstract: str, ilvl: str, overschrijvingen: dict) -> dict:
        o = overschrijvingen.get(ilvl) or {}
        d = (o.get("lvl") or self._definitie(abstract, ilvl))
        if d is None:
            raise _fout(self.docx.naam, f"nummering {num} niveau {ilvl} is niet gedefinieerd in numbering.xml")
        if o.get("start") is not None:
            d = dict(d, start=o["start"])
        return d

    def label(self, num: str, ilvl: str) -> str | None:
        """Het getoonde nummer, of None als de lijst een opsommingsteken is."""
        if self.defs["lvlRestart"]:
            raise _fout(self.docx.naam, "een lvlRestart in numbering.xml is niet gemeten")
        if num not in self.defs["num"]:
            raise _fout(self.docx.naam, f"de alinea verwijst naar nummering {num}, die numbering.xml niet kent")
        abstract, overschrijvingen = self.defs["num"][num]
        niveau = int(ilvl)
        d = self._niveau(num, abstract, ilvl, overschrijvingen)
        if d["fmt"] == "bullet":
            return None
        if d["fmt"] not in _VORMEN:
            # Zonder numFmt is de vorm niet te weten: in Big Brother Watch en Podchasov
            # zijn de koppen van niveau 6 (`JuHalpha`) Griekse letters ((α), (β)). Raden
            # (decimaal) zou daar een verkeerd nummer in de tekst zetten.
            raise _fout(self.docx.naam, f"automatische nummering van de vorm {d['fmt']!r} (numbering.xml, "
                        f"niveau {ilvl}) is niet gemeten")
        c = self.tellers.setdefault(abstract, {})
        herstart = None
        if num not in self.gezien:
            self.gezien.add(num)
            for lvl, o in overschrijvingen.items():
                if o.get("start") is not None:
                    c[int(lvl)] = o["start"] - 1
                    if int(lvl) == niveau:
                        herstart = o["start"]
        c[niveau] = c.get(niveau, d["start"] - 1) + 1
        for k in [k for k in c if k > niveau]:
            del c[k]
        # Een niveau dat niets toont (`JuHHead`, numFmt none) telt alleen om de diepere
        # niveaus te herstarten; zijn eigen reeks is onzichtbaar en een `startOverride`
        # erop (Hurbain: 6 na 3) verandert geen enkel getoond nummer.
        if d["fmt"] != "none":
            self.controleer(abstract, niveau, c[niveau], d["start"], herstart)
        else:
            self.controleer(abstract, niveau, None, d["start"], herstart)

        def vorm(m) -> str:
            n = int(m.group(1)) - 1
            dn = self._niveau(num, abstract, str(n), overschrijvingen)
            return _VORMEN.get(dn["fmt"], str)(c.get(n, dn["start"]))

        return re.sub(r"%(\d)", vorm, d["text"] or "")

    def controleer(self, abstract: str, niveau: int, nummer: int | None, start: int,
                   herstart: int | None) -> None:
        """`nummer` None: een onzichtbaar niveau, dat alleen de diepere reeksen afsluit."""
        if nummer is not None:
            vorig = self.vorige.get((abstract, niveau))
            toegestaan = {vorig + 1} if vorig is not None else {start}
            toegestaan.add(start)            # na een hoger niveau begint de reeks opnieuw
            if herstart is not None:
                toegestaan.add(herstart)
            if nummer not in toegestaan:
                raise _fout(self.docx.naam, f"de automatische nummering sluit niet aan: op niveau {niveau} volgt "
                            f"{nummer} op {vorig}")
            self.vorige[(abstract, niveau)] = nummer
        for k in [k for k in self.vorige if k[0] == abstract and k[1] > niveau]:
            del self.vorige[k]


# --------------------------------------------------------------------------
# De tekst van een alinea
# --------------------------------------------------------------------------

class Lezer:
    def __init__(self, docx: Docx) -> None:
        self.docx = docx
        self.noten: list[tuple[str, str]] = []
        self.verwezen: set[str] = set()
        self.teller = 0
        self.regelvallen = 0
        self.velden = 0
        self.opmaak = 0
        self.stack: list[str] = []

    def tekst(self, p, *, eenregelig: bool = False, stack: list | None = None) -> str:
        """De zichtbare tekst van een alinea, met voetnootmarkers en regelvallen.

        Veldcodes (`SEQ`, `REF`, `PAGEREF`) hebben een instructie en een opgeslagen
        resultaat; alleen dat resultaat is tekst. De tabs worden een spatie en een
        `w:br` een regeleinde; een paginaeinde is witruimte.
        """
        # Per open veld: "instructie" of "resultaat". Een veld kan over alinea's heen
        # lopen (de inhoudsopgave is één veld met alle regels als resultaat), dus de
        # toestand hoort bij de lezer en niet bij de alinea. Een voetnootdefinitie krijgt
        # een eigen stapel: zij staat in een ander onderdeel.
        stack = self.stack if stack is None else stack
        delen: list[str] = []

        def zichtbaar() -> bool:
            return all(s == "resultaat" for s in stack)

        def loop(el) -> None:
            for kind in el:
                n = _k(kind)
                if n in STIL:
                    continue
                if n == "r":
                    run(kind)
                elif n in DOORLOPEND:
                    if n == "ins" and "".join(t.text or "" for t in kind.iter(W + "t")).strip():
                        raise _fout(self.docx.naam, "een bijgehouden wijziging (w:ins) met tekst; het document "
                                    "is niet definitief")
                    loop(kind)
                elif n == "del":
                    raise _fout(self.docx.naam, "een bijgehouden verwijdering (w:del); het document is niet definitief")
                elif n in ("t", "sym", "br", "tab", "noBreakHyphen", "fldChar", "instrText",
                           "footnoteReference", "endnoteReference", "delText"):
                    onderdeel(kind)
                else:
                    raise _fout(self.docx.naam, f"element zonder eigen behandeling in een alinea (w:{n})")

        def run(r) -> None:
            for c in r:
                n = _k(c)
                if n in STIL:
                    continue
                onderdeel(c)

        def onderdeel(c) -> None:
            n = _k(c)
            if n == "fldChar":
                soort = c.get(W + "fldCharType")
                if soort == "begin":
                    stack.append("instructie")
                    self.velden += 1
                elif soort == "separate":
                    if not stack:
                        raise _fout(self.docx.naam, "een veldscheiding zonder begin")
                    stack[-1] = "resultaat"
                elif soort == "end":
                    if not stack:
                        raise _fout(self.docx.naam, "een veldeinde zonder begin")
                    stack.pop()
                return
            if n == "instrText" or n == "delText":
                return
            if not zichtbaar():
                return
            if n == "t":
                delen.append(c.text or "")
            elif n == "tab":
                delen.append(" ")
            elif n == "noBreakHyphen":
                delen.append("‑")
            elif n == "br":
                if (c.get(W + "type") or "textWrapping") == "page":
                    delen.append(" ")
                else:
                    delen.append("\n")
                    self.regelvallen += 1
            elif n == "sym":
                sleutel = (c.get(W + "font"), (c.get(W + "char") or "").upper())
                if sleutel == SYMBOOL_STERRETJE:
                    delen.append("*")
                elif sleutel == SYMBOOL_BULLET:
                    # Een lijstbullet uit het lettertype; HUDOC toont hem als U+F09F.
                    if "".join(delen).strip():
                        raise _fout(self.docx.naam, "een opsommingsteken (Wingdings) midden in een alinea")
                    delen.append("@@BULLET@@")
                else:
                    raise _fout(self.docx.naam, f"onbekend symbool {sleutel[0]} {sleutel[1]}")
            elif n == "footnoteReference":
                delen.append(self.noot(c.get(W + "id")))
            elif n == "endnoteReference":
                raise _fout(self.docx.naam, "een eindnoot; die route bestaat hier niet")
            elif n in INHOUD_ZONDER_ROUTE:
                raise _fout(self.docx.naam, f"een afbeelding, object of tekstvak (w:{n}); de keten "
                            "heeft daar geen route voor en het stil weglaten zou inhoud verliezen")

        loop(p)
        if stack is not self.stack and stack:
            raise _fout(self.docx.naam, "een veld in een voetnoot dat niet wordt afgesloten")
        tekst = "".join(delen)
        regels = [re.sub(r"[ \t]+", " ", r).strip(" \t") for r in tekst.split("\n")]
        regels = [r for r in regels if r]
        if eenregelig:
            return " ".join(regels)
        return "\n".join(regels)

    def noot(self, ident: str | None) -> str:
        if ident is None or ident not in self.docx._nootdefinities:
            raise _fout(self.docx.naam, f"een voetnootverwijzing zonder noot ({ident})")
        if ident in self.verwezen:
            raise _fout(self.docx.naam, f"een voetnoot ({ident}) waarnaar twee keer wordt verwezen")
        self.verwezen.add(ident)
        self.teller += 1
        label = str(self.teller)
        definitie = self.docx._nootdefinities[ident]
        regels = []
        for p in definitie.findall(W + "p"):
            t = self.tekst(p, eenregelig=True, stack=[])
            if t:
                regels.append(t)
        tekst = " ".join(regels)
        if not tekst:
            raise _fout(self.docx.naam, f"voetnoot {label} zonder tekst")
        self.noten.append((label, tekst))
        return f"[^{label}]"


# --------------------------------------------------------------------------
# De omzetting
# --------------------------------------------------------------------------

_RANDNUMMER = re.compile(r"^(\d{1,3})\.[\u00a0 \t]+(?=\S)")


def _bron_tekst(alineas) -> list[str]:
    """De tekst van een reeks alinea's zoals de bron hem heeft, los van de omzetter.

    Een veld heeft een instructie en een resultaat; alleen het resultaat telt, en het
    veld loopt over alinea's heen (de inhoudsopgave). Dat wordt hier met een teller per
    veldniveau bijgehouden in plaats van met een stapel van toestanden, zodat de telling
    niet dezelfde fout kan maken als de omzetter. Een tab en een regelval zijn een
    woordgrens: `Westerdiek` en `Peer` staan in de bron onder elkaar. Een woord kan wel
    over runs lopen (`f` en `r` in twee runs is `fr`), dus de tekst wordt per alinea
    samengevoegd voordat er woorden uit komen.
    """
    uit: list[str] = []
    resultaat_vanaf: list[int] = []
    for p in alineas:
        delen: list[str] = []
        for el in p.iter():
            n = _k(el)
            if n == "fldChar":
                soort = el.get(W + "fldCharType")
                if soort == "begin":
                    resultaat_vanaf.append(0)
                elif soort == "separate" and resultaat_vanaf:
                    resultaat_vanaf[-1] = 1
                elif soort == "end" and resultaat_vanaf:
                    resultaat_vanaf.pop()
            elif not all(resultaat_vanaf):
                continue
            elif n == "t":
                delen.append(el.text or "")
            elif n == "noBreakHyphen":
                delen.append("-")
            elif n in ("tab", "br"):
                delen.append(" ")
            elif n == "sym" and (el.get(W + "char") or "").upper() == "F02A":
                delen.append("*")
        uit.append("".join(delen))
    return uit


def _bron_alineas(docx: Docx) -> list[str]:
    return _bron_tekst(docx.body.iter(W + "p"))


# Een bestand waarin elke gedrukte regel een eigen alinea is (de oudste uitspraken, ooit
# uit een regelgeorganiseerde tekst overgezet) is woord voor woord goed om te zetten,
# maar de alinea's zijn dan stukken van zinnen en de randnummers staan midden in de tekst.
# Zonder de alinea's te raden is dat niet te herstellen, dus dat is een weigering. Gemeten
# over 35 uitspraken: in een gewoon bestand eindigt 81% tot 97% van de alinea's op een
# leesteken (mediaan 26 tot 71 woorden); in dat ene regelbestand 18% (mediaan 11).
_REGEL_MIN_ALINEAS = 100
_REGEL_MAX_AANDEEL = 0.5
_ZINSEINDE = re.compile(r"[.;:?!\u201d\u2019\"')]$")


def _regelbestand(alineas: list[str]) -> tuple[bool, float]:
    lang = [a.strip() for a in alineas if len(a.split()) >= 3]
    if len(lang) < _REGEL_MIN_ALINEAS:
        return False, 1.0
    aandeel = sum(1 for a in lang if _ZINSEINDE.search(a)) / len(lang)
    return aandeel < _REGEL_MAX_AANDEEL, aandeel



def omzetten(data: bytes, kaart: Kaart, *, titel: str | None = None) -> tuple[str, dict]:
    """Het bestand als Markdown, plus wat de herkomst erover vastlegt.

    `titel` is een titel die niet uit de tekst van het bestand komt (HUDOC: de `docname`
    uit de record, omdat de omslag hem soms over twee regels verdeelt); zijn woorden
    tellen als gegenereerd zodat de omslag ongemoeid blijft.
    """
    docx = Docx(data, kaart.naam)
    lezer = Lezer(docx)
    teller = Teller(docx)
    blokken: list[str] = []
    koppen = 0
    onbekend: Counter = Counter()
    gegenereerd: Counter = Counter(_woorden(titel)) if titel else Counter()
    randnummers = 0
    bullets = 0
    tabellen = 0
    lichaam: list[str] = []

    if titel:
        blokken.append(f"# {_norm(titel)}")

    for kind in docx.body:
        n = _k(kind)
        if n in ("sectPr", "bookmarkStart", "bookmarkEnd"):
            continue
        if n == "tbl":
            tabellen += 1
            blokken.append(_tabel(kind, lezer, gegenereerd))
            continue
        if n != "p":
            raise _fout(docx.naam, f"element zonder eigen behandeling in de body (w:{n})")

        stijl = docx.stijl(kind)
        reden = kaart.weiger(stijl)
        if reden:
            raise _fout(docx.naam, reden)
        soort = kaart.classificeer(docx, kind, stijl)
        if soort is None:
            onbekend[stijl] += 1
            continue

        nummering = docx.nummering_van(kind, stijl)
        tekst = lezer.tekst(kind, eenregelig=(soort in ("kop", "toc")))
        bullet = "@@BULLET@@" in tekst
        tekst = tekst.replace("@@BULLET@@", "").strip(" \t" + NBSP)
        if not tekst:
            continue
        # Het nummer dat Word vóór de alinea zet (`Teller`); None bij een opsommingsteken,
        # "" bij een niveau dat niets toont. Zijn woorden zijn gegenereerd, niet brontekst.
        label = teller.label(*nummering) if nummering is not None else ""
        if label is None:
            bullet = True
            label = ""
        if label and _RANDNUMMER.match(tekst) or label and tekst.startswith(label):
            raise _fout(docx.naam, f"de alinea draagt het nummer {label!r} al in haar tekst en ook als "
                        "automatische nummering; welke van de twee geldt is niet te bewijzen")
        if label:
            gegenereerd.update(_woorden(label))
        if soort == "alinea" and stijl in kaart.randnummer:
            lichaam.append(tekst)
        if bullet:
            bullets += 1
            blokken.append("- " + tekst.replace("\n", "\n  "))
            continue

        if soort == "kop":
            koppen += 1
            blokken.append(kaart.kopprefix(docx, kind, stijl) + (f"{label} " if label else "") + tekst)
        elif soort == "toc":
            blokken.append("  " * kaart.toc_niveau(stijl) + "- " + (f"{label} " if label else "") + tekst)
        elif label:
            # Een gerekend nummer vóór een gewone alinea (de lijst van verzoekers in López
            # Ribalda, het dictum): in de vorm die de bron zelf voor een getypt lijstnummer
            # gebruikt, nummer plus twee harde spaties (`1.  Holds`), en geen randnummer.
            blokken.append(f"{label}{NBSP}{NBSP}{tekst}")
        else:
            m = _RANDNUMMER.match(tekst) if stijl in kaart.randnummer else None
            if m:
                randnummers += 1
                tekst = f"{m.group(1)}. " + tekst[m.end():]
            blokken.append(tekst)

    if lezer.stack:
        raise _fout(docx.naam, "een veld dat aan het eind van het document niet is afgesloten")
    if kaart.regelbestand:
        regels, aandeel = _regelbestand(lichaam)
        if regels:
            raise _fout(docx.naam, f"in dit bestand is elke gedrukte regel een alinea (slechts {aandeel:.0%} van de "
                        "alinea's eindigt op een leesteken); de zinnen zijn niet te herstellen zonder "
                        "alinea's te raden")
    if onbekend:
        raise _fout(docx.naam, "stijlen zonder eigen behandeling: "
                    + ", ".join(f"{s} ({n}×)" for s, n in sorted(onbekend.items())))

    markdown = "\n\n".join(b for b in blokken if b.strip())
    for label, tekst in lezer.noten:
        markdown += f"\n\n[^{label}]: {tekst}"
    markdown = markdown.rstrip() + "\n"

    _zelfcontrole(docx, lezer, markdown, gegenereerd)
    meta = {
        "koppen": koppen,
        "randnummers": randnummers,
        "noten": len(lezer.noten),
        "velden": lezer.velden,
        "regelvallen": lezer.regelvallen,
        "opsommingen": bullets,
        "tabellen": tabellen,
    }
    return markdown, meta


def _tabel(tbl, lezer: Lezer, gegenereerd: Counter) -> str:
    rijen = []
    kop_rijen = 0
    for tr in tbl.findall(W + "tr"):
        cellen = []
        for tc in tr.findall(W + "tc"):
            tcpr = tc.find(W + "tcPr")
            if tcpr is not None and (tcpr.find(W + "gridSpan") is not None
                                     or tcpr.find(W + "vMerge") is not None):
                raise _fout(lezer.docx.naam, "een tabelcel met gridSpan of vMerge; de rasterrelatie is dan niet te bewijzen")
            if tc.find(W + "tbl") is not None:
                raise _fout(lezer.docx.naam, "een tabel in een tabelcel; de inhoud is dan niet als "
                            "rechthoekig raster te bewijzen")
            tekst = " ".join(t for t in (lezer.tekst(p, eenregelig=True) for p in tc.findall(W + "p")) if t)
            cellen.append({"tekst": tekst, "kol": None, "colspan": 1, "rowspan": 1})
        rijen.append(cellen)
        if tr.find(W + "trPr/" + W + "tblHeader") is not None:
            if len(rijen) != kop_rijen + 1:
                raise _fout(lezer.docx.naam, "een kopregel (w:tblHeader) die niet vooraan staat; "
                            "Markdown kent alleen een kopregel bovenaan")
            kop_rijen += 1
    if kop_rijen > 1:
        raise _fout(lezer.docx.naam, "meer dan één kopregel; één Markdown-koprij is onvoldoende")
    # Zonder `w:tblHeader` markeert de bron geen kopregel, en dan is er ook geen: de tabel
    # krijgt een lege kop en het profiel vlagt hem voor een blik.
    markdown, _ = xg.tabel_markdown(rijen, kop_rijen)
    return markdown


def _zelfcontrole(docx: Docx, lezer: Lezer, markdown: str, gegenereerd: Counter) -> None:
    """Weigeren als de omzetting tekst heeft verloren, verweven of verdubbeld.

    Dezelfde twee controles als bij de andere XML-routes: de woordverzameling is als
    multiset gelijk (dat vangt verlies en verdubbeling), en elke tekstdragende
    bronalinea staat aaneengesloten in de uitvoer (dat vangt afbreken en verweven).
    De bron wordt met een eigen traversal geteld, niet met de code die de Markdown bouwde.
    """
    alineas = _bron_alineas(docx)
    bron: Counter = Counter(gegenereerd)
    for a in alineas:
        bron.update(_woorden(a.replace("‑", "-")))
    # De noten staan in footnotes.xml, niet in document.xml; tel alleen de verwezen noten.
    for ident in sorted(lezer.verwezen):
        for a in _bron_tekst(docx._nootdefinities[ident].findall(W + "p")):
            bron.update(_woorden(a.replace("\u2011", "-")))
    uit = _woorden(markdown.replace("‑", "-"))
    uit_teller = Counter(uit)
    # Een gegenereerde opsommingsmarker (`- `) en tabelrand zijn geen woorden.
    if bron != uit_teller:
        tekort, teveel = bron - uit_teller, uit_teller - bron
        raise _fout(docx.naam, "de omzetting mist of verdubbelt tekst ten opzichte van de bron "
                    f"(ontbreekt: {dict(list(tekort.items())[:6])}; te veel: "
                    f"{dict(list(teveel.items())[:6])})")
    for a in alineas:
        deel = _woorden(a.replace("‑", "-"))
        if deel and not _bevat(uit, deel):
            raise _fout(docx.naam, "een alinea uit de bron staat niet aaneengesloten in de uitvoer: "
                        + " ".join(deel[:8]))


def _bevat(reeks: list[str], deel: list[str]) -> bool:
    eerste = deel[0]
    for i, w in enumerate(reeks):
        if w == eerste and reeks[i:i + len(deel)] == deel:
            return True
    return False

# --------------------------------------------------------------------------
# De kaart voor een willekeurig Word-bestand
# --------------------------------------------------------------------------

_KOPNAAM = re.compile(r"^(?:heading|kop)\s*(\d)$")
_TITELNAAM = {"title", "titel"}
_TOCNAAM = re.compile(r"^toc\s*(\d)$")


def _word_soort(docx: Docx, p, stijl: str) -> str | None:
    """`kop`, `toc` of `alinea` uit de stijl (of een `outlineLvl` in de alinea zelf)."""
    niveau = p.find(W + "pPr/" + W + "outlineLvl")
    if niveau is not None and (niveau.get(W + "val") or "").isdigit() and int(niveau.get(W + "val")) < 9:
        return "kop"
    keten = docx.stijlketen(stijl)
    if not keten:
        # Zonder definitie is de naam alles wat er is. `Normal` is de standaard van Word.
        return "alinea" if stijl == "Normal" else None
    for d in keten:
        if d["niveau"] is not None and d["niveau"] < 9:
            return "kop"
        if d["naam"] in _TITELNAAM or _KOPNAAM.match(d["naam"]):
            return "kop"
        if _TOCNAAM.match(d["naam"]):
            return "toc"
    # Elke stijl die (via basedOn) op de standaardstijl teruggaat is een gewone alinea. Wat
    # daar niet op teruggaat kan alles zijn, en wordt niet geraden.
    return "alinea" if any(d["standaard"] for d in keten) else None


def _word_kopprefix(docx: Docx, p, stijl: str) -> str:
    """Een titel is `#`; `Heading N` is `#` × (N + 1), zodat de titel de enige `#` blijft."""
    niveau = p.find(W + "pPr/" + W + "outlineLvl")
    if niveau is not None and (niveau.get(W + "val") or "").isdigit():
        return "#" * min(int(niveau.get(W + "val")) + 2, 6) + " "
    for d in docx.stijlketen(stijl):
        if d["naam"] in _TITELNAAM:
            return "# "
        m = _KOPNAAM.match(d["naam"])
        if m:
            return "#" * min(int(m.group(1)) + 1, 6) + " "
        if d["niveau"] is not None:
            return "#" * min(d["niveau"] + 2, 6) + " "
    return "## "


def _word_toc_niveau(stijl: str) -> int:
    m = re.search(r"(\d)$", stijl)
    return max(int(m.group(1)) - 1, 0) if m else 0


WORD_KAART = Kaart(naam="Word-bestand", classificeer=_word_soort, kopprefix=_word_kopprefix,
                   toc_niveau=_word_toc_niveau)


def convert(data: bytes) -> tuple[str, dict]:
    """Een willekeurig Word-bestand als Markdown; weigert met de reden als het niet betrouwbaar kan."""
    return omzetten(data, WORD_KAART)
