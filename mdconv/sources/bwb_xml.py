"""BWB-XML (toestand-schema 2016-1) -> Markdown + structuureenheden.

De ankers komen uit de elementen zelf: `<hoofdstuk>`, `<artikel>`, `<lid>`,
`<li>` met hun `<nr>`, `<lidnr>` en `<li.nr>`. Niets wordt uit opmaak
afgeleid. Waar de bron geen nummer geeft (een ongemarkeerde lijst, zoals de
definities in artikel 1), komt er geen anker.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..errors import ConversionError
from .xml_gedeeld import Uitvoer, nummer_anker, tabel_markdown, ws

# Structuurcontainers met hun ankervoorvoegsel.
CONTAINERS = {"boek": "boek", "deel": "deel", "titeldeel": "tit", "hoofdstuk": "hfd",
              "afdeling": "afd", "paragraaf": "par", "subparagraaf": "subpar",
              "sub-paragraaf": "subpar"}
OVERSLAAN = {"meta-data", "jcis", "jci", "bwb-inputbestand", "bwb-wijzigingen",
             "redactionele-correcties", "kop", "lidnr", "li.nr", "citeertitel"}
INLINE = {"al", "nadruk", "sup", "extref", "intref", "redactie", "sub", "unl", "inf", "meta-data"}
ONGEMARKEERD = re.compile(r"^[\-–—•·*]$")


class BwbOmzetter:
    def __init__(self) -> None:
        self.u = Uitvoer()
        self.bijlage_ankers: list[str] = []
        self.herhaalde_cellen = 0
        self.expired: dict[str, str] = {}

    # ---------- inline ----------
    def inline(self, el, noot_prefix: str = "") -> str:
        delen = [el.text or ""]
        for kind in el:
            delen.append(self.inline_el(kind, noot_prefix))
            delen.append(kind.tail or "")
        return "".join(delen)

    def inline_el(self, el, noot_prefix: str) -> str:
        tag = el.tag
        if tag in OVERSLAAN:
            return ""
        if tag == "nadruk":
            binnen = ws(self.inline(el, noot_prefix))
            soort = el.get("type", "")
            if not binnen:
                return ""
            if soort == "cur":
                return f"*{binnen}*"
            if soort in ("vet", "halfvet"):
                return f"**{binnen}**"
            return binnen
        if tag == "sup":
            tekst = ws("".join(el.itertext()))
            if tekst.isdigit():
                return f"[^{noot_prefix}{tekst}]"
            return f"^{tekst}^" if tekst else ""
        if tag == "redactie":
            return f"[Red: {ws(self.inline(el, noot_prefix))}]"
        if tag in ("al", "extref", "intref", "datum", "naam", "voornaam", "achternaam",
                   "functie", "plaats", "sub", "unl", "inf"):
            return self.inline(el, noot_prefix)
        self.u.markeer_onbekend(f"inline:{tag}")
        return self.inline(el, noot_prefix)

    # ---------- blokken ----------
    def omzetten(self, root) -> Uitvoer:
        # Tekst die nog niet geldt mag nooit als geldend recht lezen. Bij een
        # artikel zet `artikel()` er een regel onder de kop; voor elk ander
        # onderdeel is er geen vorm, en dan weigeren we liever dan te raden.
        for el in root.iter():
            if el.tag != "artikel" and (el.get("status") or "").lower() == "nogniet":
                raise ConversionError(
                    f"<{el.tag}> heeft status nogniet; alleen bij een artikel weet de "
                    "omzetter hoe een nog niet geldend onderdeel wordt getoond."
                )
        wet = root.find("wetgeving")
        titel = wet.findtext("citeertitel") or ""
        self.u.blok(f"# {ws(titel)}")
        for kind in wet:
            if kind.tag == "intitule":
                self.u.blok(ws(self.inline(kind)))
            elif kind.tag == "wet-besluit":
                for deel in kind:
                    if deel.tag == "wettekst":
                        self.container_inhoud(deel, niveau=2, pad={})
                    elif deel.tag == "bijlage":
                        self.bijlage(deel, niveau=2)
                    else:
                        self.plat(deel)
            elif kind.tag == "bijlage":
                self.bijlage(kind, niveau=2)
            elif kind.tag not in OVERSLAAN:
                self.plat(kind)
        return self.u

    def plat(self, el) -> None:
        """Aanhef, wetsluiting en dergelijke: alinea's in volgorde."""
        if el.tag in OVERSLAAN:
            return
        alleen_inline = all(c.tag in INLINE for c in el)
        if el.tag in ("al", "considerans.al", "wij", "slotformulering", "afkondiging") or alleen_inline:
            self.u.blok(ws(self.inline(el)))
            return
        for kind in el:
            self.plat(kind)

    def kop(self, el) -> tuple[str, str, str]:
        k = el.find("kop")
        if k is None:
            return "", "", ""
        return (ws(self.inline(k.find("label"))) if k.find("label") is not None else "",
                ws(self.inline(k.find("nr"))) if k.find("nr") is not None else "",
                ws(self.inline(k.find("titel"))) if k.find("titel") is not None else "")

    def kopregel(self, label: str, nr: str, titel: str) -> str:
        eerste = " ".join(x for x in (label, nr) if x)
        return f"{eerste}. {titel}" if titel else eerste

    def container_anker(self, tag: str, nr: str, pad: dict) -> str:
        n = nummer_anker(nr, romeins_omrekenen=True)
        voor = pad.get("annex", "")
        if tag == "hoofdstuk":
            a = f"hfd-{pad['tit']}-{n}" if "tit" in pad else f"hfd-{n}"
        elif tag == "afdeling":
            ouder = pad.get("hfd") or pad.get("tit")
            a = f"afd-{ouder}-{n}" if ouder else f"afd-{n}"
        elif tag == "paragraaf":
            if "." in nr:
                a = f"par-{n}"
            else:
                delen = [pad[k] for k in ("hfd", "afd") if k in pad]
                a = "-".join(["par", *delen, n])
        else:
            a = f"{CONTAINERS[tag]}-{n}"
        return f"{voor}-{a}" if voor else a

    def container_inhoud(self, el, niveau: int, pad: dict) -> None:
        for kind in el:
            if kind.tag in CONTAINERS:
                label, nr, titel = self.kop(kind)
                anker = self.container_anker(kind.tag, nr, pad)
                self.u.blok(f"{'#' * min(niveau, 6)} {self.kopregel(label, nr, titel)}")
                self.u.eenheid(anker, kind.tag, self.kopregel(label, nr, titel))
                eigen = anker.split("-", 1)[1] if not pad.get("annex") else anker.split(f"{CONTAINERS[kind.tag]}-", 1)[1]
                sleutel = {"hoofdstuk": "hfd", "afdeling": "afd", "titeldeel": "tit"}.get(kind.tag)
                nieuw = dict(pad)
                if sleutel:
                    nieuw[sleutel] = eigen.split("-")[-1] if sleutel != "tit" else eigen
                self.container_inhoud(kind, niveau + 1, nieuw)
            elif kind.tag == "artikel":
                self.artikel(kind, niveau, pad)
            elif kind.tag in OVERSLAAN:
                continue
            elif kind.tag in ("al", "lijst", "table"):
                self.inhoud(kind, basis="", prefix_noot="")
            else:
                self.u.markeer_onbekend(f"blok:{kind.tag}")
                self.container_inhoud(kind, niveau, pad)

    def artikel(self, el, niveau: int, pad: dict) -> None:
        label, nr, titel = self.kop(el)
        naam = nummer_anker(nr)
        anker = f"{pad['annex']}-art-{naam}" if pad.get("annex") else f"art-{naam}"
        regel = self.kopregel(label or "Artikel", nr, titel)
        self.u.blok(f"{'#' * min(niveau, 6)} {regel}")
        self.u.eenheid(anker, "artikel", regel)
        status = (el.get("status") or "").lower()
        inwerking = el.get("inwerking")
        if status == "vervallen":
            if not inwerking or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", inwerking):
                raise ConversionError(
                    f"Vervallen artikel {nr} heeft geen geldige @inwerking-datum."
                )
            jaar, maand, dag = inwerking.split("-")
            self.u.blok(f"[Vervallen per {dag}-{maand}-{jaar}]")
            self.expired[anker] = inwerking
        elif status == "nogniet":
            # De portal liet de tekst weg en zei dat dit onderdeel nog niet in
            # werking is. Wij houden de tekst (het anker moet blijven bestaan),
            # dus de melding moet er wel staan, in de vorm van `[Vervallen per …]`.
            self.u.blok("[Nog niet in werking getreden.]")
        teller = {"lijsten": 0}
        for kind in el:
            if kind.tag == "lid":
                lidnr = ws(self.inline(kind.find("lidnr"))) if kind.find("lidnr") is not None else ""
                lidanker = f"{anker}-{nummer_anker(lidnr)}" if lidnr else None
                if status == "nogniet":
                    self.lid_nog_niet(kind, lidnr)
                else:
                    self.lid(kind, lidnr, lidanker)
            elif kind.tag in OVERSLAAN:
                continue
            else:
                self.inhoud(kind, basis=anker, prefix_noot="", teller=teller)

    def lid_nog_niet(self, el, lidnr: str) -> None:
        """Behoud nog niet geldende tekst zonder nieuwe citeerankers te creëren.

        BWBR0040940 bevat twee toekomstige leden die de portaltekst nog niet
        adresseert. De corpuspoort eist daarom dezelfde bestaande ankerlijst;
        nummer en woorden blijven wel zichtbaar als gewone bronalinea.
        """
        eerste = True
        for kind in el:
            if kind.tag == "lidnr" or kind.tag in OVERSLAAN:
                continue
            if kind.tag == "al" and eerste:
                tekst = ws(self.inline(kind))
                self.u.blok(f"{lidnr}. {tekst}" if lidnr else tekst)
                eerste = False
            else:
                self.inhoud(kind, basis="", prefix_noot="")

    def lid(self, el, lidnr: str, anker: str | None) -> None:
        eerste = True
        teller = {"lijsten": 0}
        for kind in el:
            if kind.tag == "lidnr":
                continue
            if kind.tag == "al" and eerste:
                tekst = ws(self.inline(kind))
                self.u.blok(f"- {lidnr} {tekst}" if lidnr else tekst)
                if anker:
                    self.u.eenheid(anker, "lid", f"{lidnr} {tekst}")
                eerste = False
                continue
            if eerste and anker:
                # Een lid dat met een lijst of tabel begint: het anker hangt aan het nummer.
                self.u.blok(f"- {lidnr}")
                self.u.eenheid(anker, "lid", lidnr)
                eerste = False
            self.inhoud(kind, basis=anker or "", prefix_noot="", teller=teller, diepte=1)

    def inhoud(self, el, basis: str, prefix_noot: str, teller: dict | None = None, diepte: int = 0) -> None:
        inspring = "  " * diepte
        if el.tag == "al":
            tekst = ws(self.inline(el, prefix_noot))
            if tekst:
                self.u.blok(f"{inspring}{tekst}")
        elif el.tag == "lijst":
            teller = teller if teller is not None else {"lijsten": 0}
            gemarkeerd = any(li.find("li.nr") is not None and not ONGEMARKEERD.match(ws(li.findtext("li.nr") or ""))
                             for li in el.findall("li"))
            if gemarkeerd:
                teller["lijsten"] += 1
            extra = f"al{teller['lijsten']}-" if gemarkeerd and teller["lijsten"] > 1 else ""
            self.u.blok(self.lijst(el, basis, extra, diepte, prefix_noot))
        elif el.tag == "table":
            self.tabel(el, prefix_noot)
        elif el.tag in OVERSLAAN:
            return
        else:
            self.u.markeer_onbekend(f"inhoud:{el.tag}")
            tekst = ws(self.inline(el, prefix_noot))
            if tekst:
                self.u.blok(tekst)

    def lijst(self, el, basis: str, extra: str, diepte: int, prefix_noot: str) -> str:
        regels = []
        inspring = "  " * diepte
        for li in el.findall("li"):
            nr = ws(li.findtext("li.nr") or "")
            anker = None
            if nr and not ONGEMARKEERD.match(nr) and basis:
                anker = f"{basis}-{extra}{nummer_anker(nr)}"
            eerste = True
            for kind in li:
                if kind.tag == "li.nr":
                    continue
                if kind.tag == "al" and eerste:
                    tekst = ws(self.inline(kind, prefix_noot))
                    regels.append(f"{inspring}- {nr + ' ' if nr else ''}{tekst}")
                    if anker:
                        self.u.eenheid(anker, "onderdeel", f"{nr} {tekst}")
                    eerste = False
                elif kind.tag == "lijst":
                    if eerste:
                        regels.append(f"{inspring}- {nr}")
                        if anker:
                            self.u.eenheid(anker, "onderdeel", nr)
                        eerste = False
                    # Onder een item zonder nummer is een sublijst niet adresseerbaar.
                    regels.append(self.lijst(kind, anker or "", "", diepte + 1, prefix_noot))
                elif kind.tag == "al":
                    regels.append(f"{inspring}  {ws(self.inline(kind, prefix_noot))}")
                elif kind.tag == "table":
                    self.tabel(kind, prefix_noot)
                elif kind.tag in OVERSLAAN:
                    continue
                else:
                    self.u.markeer_onbekend(f"li:{kind.tag}")
                    regels.append(f"{inspring}  {ws(self.inline(kind, prefix_noot))}")
        return "\n".join(r for r in regels if r.strip())

    def tabel(self, el, prefix_noot: str) -> None:
        for tgroup in el.findall("tgroup"):
            kolommen = {c.get("colname"): i for i, c in enumerate(tgroup.findall("colspec"))}
            rijen, koprijen = [], 0
            for sectie in ("thead", "tbody"):
                for s in tgroup.findall(sectie):
                    for row in s.findall("row"):
                        cellen = []
                        for entry in row.findall("entry"):
                            tekst = " ".join(ws(self.inline(a, prefix_noot)) for a in entry
                                             if a.tag not in OVERSLAAN) or ws(self.inline(entry, prefix_noot))
                            begin = entry.get("namest") or entry.get("colname")
                            eind = entry.get("nameend") or begin
                            kol = kolommen.get(begin)
                            span = (kolommen.get(eind, kol) - kol + 1) if kol is not None else 1
                            cellen.append({"tekst": ws(tekst), "kol": kol, "colspan": max(span, 1),
                                           "rowspan": int(entry.get("morerows") or 0) + 1})
                        rijen.append(cellen)
                        if sectie == "thead":
                            koprijen += 1
            md, herhaald = tabel_markdown(rijen, koprijen)
            self.herhaalde_cellen += herhaald
            self.u.blok(md)

    def bijlage(self, el, niveau: int) -> None:
        label, nr, titel = self.kop(el)
        n = nummer_anker(nr, romeins_omrekenen=True) if nr else str(len(self.bijlage_ankers) + 1)
        anker = f"annex-{n}"
        self.bijlage_ankers.append(anker)
        regel = self.kopregel(label or "Bijlage", nr, titel)
        self.u.blok(f"{'#' * niveau} {regel}")
        self.u.eenheid(anker, "bijlage", regel)
        prefix = f"{anker}-"
        for kind in el:
            if kind.tag == "kop" or kind.tag in OVERSLAAN:
                continue
            # Een alinea die met een los noot-cijfer begint is een voetnootdefinitie.
            if kind.tag == "al" and not (kind.text or "").strip() and len(kind) and kind[0].tag == "sup":
                cijfer = ws("".join(kind[0].itertext()))
                if cijfer.isdigit():
                    rest = (kind[0].tail or "") + "".join(self.inline_el(c, prefix) + (c.tail or "") for c in list(kind)[1:])
                    # Direct na de bijlage, zoals in de bron; niet achteraan het document.
                    self.u.blok(f"[^{prefix}{cijfer}]: {ws(rest)}")
                    continue
            if kind.tag in CONTAINERS or kind.tag == "artikel":
                wrapper = ET.Element("x")
                wrapper.append(kind)
                self.container_inhoud(wrapper, niveau + 1, {"annex": anker})
            else:
                self.inhoud(kind, basis=anker, prefix_noot=prefix)


def omzetten(data: bytes | str) -> tuple[str, list, dict, dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConversionError(f"BWB-XML is niet leesbaar: {exc}") from exc
    o = BwbOmzetter()
    u = o.omzetten(root)
    return u.markdown(), u.eenheden, u.onbekend, {
        "herhaalde_cellen": o.herhaalde_cellen,
        "expired": o.expired,
    }
