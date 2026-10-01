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
# Een lijstteken is geen nummer en geeft geen anker. `−` (U+2212), `○` en `□` kwamen erbij met de
# Regeling register onderwijsdeelnemers (BWBR0043632) en de Regeling Bibob-formulieren 2024
# (BWBR0049314): `nummer_anker("−")` is leeg, en `eenheid()` weigerde `annex-1-` (T6-F4, kb WP-43).
ONGEMARKEERD = re.compile(r"^[\-–—−•·*○□]$")


class _BwbUitvoer(Uitvoer):
    """`Uitvoer`, met de definitie van een `<noot>` direct onder het blok van zijn marker.

    Aan het eind van het document kwamen de twee noten van artikel 1.3 van BWBR0051654
    onder de kop van bijlage 5 te staan, en lazen ze als tekst van die bijlage.
    `conventions.md` §5 van de kennisbank noemt "direct onder de alinea" als eerste vorm.
    Een lijst is hier één blok, dus een noot in een onderdeel komt onder de hele lijst
    en breekt die niet.
    """

    def __init__(self) -> None:
        super().__init__()
        self.wachtend: list[tuple[str, str]] = []

    def blok(self, tekst: str) -> None:
        super().blok(tekst)
        if self.wachtend:
            self.blokken.append("\n".join(f"[^{label}]: {t}" for label, t in self.wachtend))
            self.wachtend.clear()

    def eenheid(self, anker: str, soort: str, tekst: str) -> None:
        # Een artikel, lid of onderdeel zonder nummer (`art-`, `art-1-`) mag de
        # omzetter nooit uitgeven: het nummer is de identiteit, en de kennisbank
        # weigerde tot dan het hele zijbestand zonder dat de reden hier te lezen
        # was (Wet RO, T3-F3, kb WP-20). Gemeten over de 55 BWB-bronnen van de
        # kennisbank (25 september 2026): alleen de twee `art-` van de Wet RO.
        # Bewust niet strenger: `hfd-` voor het hoofdstuk met alleen een titel
        # (Wbp BES, BWBR0028067, kb WP-04) en een dubbel anker in drie bronnen
        # (Awb 8:36c in twee varianten, `art-1-1-1-i` in BWBR0040635, `art-10-2f-2`
        # in BWBR0045754) staan in clean/ en zijn daar met bronbewijs beoordeeld.
        if soort in ("artikel", "lid", "onderdeel") and (not anker or anker.endswith("-") or "--" in anker):
            raise ConversionError(f"Een leeg ankersegment ({anker!r}, {soort}); omzetting geweigerd.")
        super().eenheid(anker, soort, tekst)


class BwbOmzetter:
    def __init__(self) -> None:
        self.u = _BwbUitvoer()
        self.bijlage_ankers: list[str] = []
        self.herhaalde_cellen = 0
        self.expired: dict[str, str] = {}
        self.omgedraaid: list[str] = []
        # Het anker van de bijlage die nu wordt geschreven; een `<noot>` daarin hoort
        # bij de reeks van die bijlage, net als een `sup`-noot (`[^annex-1-5]`).
        self.huidige_bijlage = ""
        self.nootlabels: set[str] = set()
        self.noten: list[tuple[str, str]] = []
        self.afbeeldingen_weggelaten: list[dict] = []
        # De labels van de `sup`-noten die een definitie hebben, per bijlage vooraf gelezen
        # (`bijlage()`): een marker staat meestal vóór zijn definitie.
        self.sup_definities: set[str] = set()

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
            ruw = self.inline(el, noot_prefix)
            binnen = ws(ruw)
            soort = el.get("type", "")
            if not binnen:
                return " " if ruw else ""
            # Witruimte aan de rand van de nadruk hoort bij de zin, niet bij de opmaak: de
            # Regeling ggz en fz 2026 (BWBR0051654) schrijft `zorgverlener </nadruk>die`, en
            # `ws()` maakte daar `zorgverlenerdie` van (kb WP-30, verklaard; kb WP-43). De
            # spatie komt buiten de markering, want `*tekst *` is geen Markdown-nadruk.
            voor = " " if ruw[:1].isspace() else ""
            na = " " if ruw[-1:].isspace() else ""
            if soort == "cur":
                return f"{voor}*{binnen}*{na}"
            if soort in ("vet", "halfvet"):
                return f"{voor}**{binnen}**{na}"
            return f"{voor}{binnen}{na}"
        if tag == "sup":
            tekst = ws("".join(el.itertext()))
            # Een `<sup>` met cijfers is alleen een nootmarker als dezelfde bijlage een
            # definitie met dat nummer heeft (`<al><sup>n</sup>…`, zie `nootdefinitie()`).
            # Anders is het een macht: de Archiefregeling (BWBR0027041) schrijft
            # `kg/m<sup>3</sup>` en heeft geen enkele noot, en `[^3]` wees daar naar niets
            # (T6-F3, kb WP-28 en WP-43).
            if tekst.isdigit() and f"{noot_prefix}{tekst}" in self.sup_definities:
                return f"[^{noot_prefix}{tekst}]"
            return f"^{tekst}^" if tekst else ""
        if tag == "naam":
            # `<naam><voornaam>J. P. H.</voornaam><achternaam>Donner</achternaam></naam>`
            # heeft in de bron geen witruimte tussen de delen; zonder spatie las het als
            # `H.Donner` (kb WP-43, bij de ondertekening).
            return self.aaneen(el, noot_prefix)
        if tag == "redactie":
            return f"[Red: {ws(self.inline(el, noot_prefix))}]"
        if tag == "noot":
            return self.noot(el)
        if tag == "plaatje":
            # In een tabelcel (Opiumwet, bijlage): het bijschrift is de celtekst.
            return self.plaatje(el)
        # `<afk>` is een aangehaalde aanduiding in de lopende tekst (`in <afk>artikel 14 van
        # de Wet op de medische hulpmiddelen</afk> voor «…»`, BWBR0042755); tot kb WP-43 een
        # weigering (T6-F4). `<organisatie>` staat in een ondertekening (`De <functie>Minister
        # </functie> van <organisatie>Justitie</organisatie>`).
        if tag in ("al", "extref", "intref", "datum", "voornaam", "achternaam",
                   "functie", "organisatie", "plaats", "sub", "unl", "inf", "afk"):
            return self.inline(el, noot_prefix)
        self.u.markeer_onbekend(f"inline:{tag}")
        return self.inline(el, noot_prefix)

    def aaneen(self, el, noot_prefix: str = "") -> str:
        """De inline tekst van `el`, met een spatie waar twee kinderen zonder witruimte aansluiten.

        Voor een ondertekening en een naam: hun delen zijn aparte elementen, die de portal
        op aparte regels of met een spatie toont. Tekst tussen de delen (`De`, ` van `, `, `)
        blijft zoals de bron hem geeft.
        """
        delen = [el.text or ""]
        for kind in el:
            delen.append(self.inline_el(kind, noot_prefix))
            delen.append(kind.tail if (kind.tail or "").strip() else " ")
        return ws("".join(delen))

    def noot(self, el) -> str:
        """Een `<noot>` op de plek van zijn marker: `[^1]` daar, de definitie eronder.

        De Regeling ggz en fz 2026 (BWBR0051654, artikel 1.3) en de nadere regel
        NR/REG-1829 (BWBR0041321) zetten hun voetnoot midden in de alinea:
        `handelingen<noot type="voet"><noot.nr>1</noot.nr><noot.al>…</noot.al></noot>`.
        Tot 25 september 2026 was dat `inline:noot` zonder behandeling, en werd het
        document geweigerd (kb, bronnenronde van 25 september). Het label is het nummer
        uit de bron, zoals de OP-XML-route een voetnoot noemt; in een bijlage de reeks
        van die bijlage, zoals een `sup`-noot daar. De definitie komt direct onder
        het blok waarin de marker staat (zie `_BwbUitvoer`).
        """
        if el.get("type") != "voet":
            raise ConversionError(
                f"Een BWB-noot van type {el.get('type')!r}; alleen `voet` is gemeten.")
        onbekend = [k.tag for k in el if k.tag not in ("noot.nr", "noot.al")]
        if onbekend:
            raise ConversionError(f"Een BWB-noot bevat {onbekend}; alleen noot.nr en noot.al zijn gemeten.")
        nr = ws("".join(el.find("noot.nr").itertext())) if el.find("noot.nr") is not None else ""
        alineas = [ws(self.inline(al)) for al in el.findall("noot.al")]
        tekst = " ".join(a for a in alineas if a)
        if not nr or not tekst:
            raise ConversionError("Een BWB-noot mist zijn nummer of zijn tekst.")
        if not re.fullmatch(r"[0-9A-Za-z]+", nr):
            raise ConversionError(f"Een BWB-nootnummer dat geen label kan zijn ({nr!r}).")
        label = f"{self.huidige_bijlage}-{nr}" if self.huidige_bijlage else nr
        self.definieer(label)
        self.noten.append((label, tekst))
        self.u.wachtend.append((label, tekst))
        return f"[^{label}]"

    def definieer(self, label: str) -> None:
        # Twee definities onder één label: welke een marker bedoelt, is dan niet meer te
        # zeggen. Geldt ook tussen een `<noot>` en een `sup`-noot in dezelfde bijlage.
        if label in self.nootlabels:
            raise ConversionError(f"Twee BWB-noten met hetzelfde label ({label}); omzetting geweigerd.")
        self.nootlabels.add(label)

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
        # Een noot in een nummer (`<lidnr>1<noot>…</noot></lidnr>`) werd `- 1[^1]`, en
        # `nummer_anker()` maakte daar stil het lidanker `…-11` van. Waar een nummer
        # een anker wordt, hoort geen marker; in een noot hoort geen tweede noot.
        for el in root.iter():
            if el.tag in ("nr", "lidnr", "li.nr", "label", "noot") and any(
                    n is not el for n in el.iter("noot")):
                raise ConversionError(
                    f"Een BWB-noot staat in <{el.tag}>; daar is geen plek voor een marker "
                    "gemeten, en een nummer met een marker erin geeft een verkeerd anker.")
        wet = root.find("wetgeving")
        titel = wet.findtext("citeertitel") or ""
        self.u.blok(f"# {ws(titel)}")
        for kind in wet:
            if kind.tag == "intitule":
                self.u.blok(ws(self.inline(kind)))
            elif kind.tag in ("wet-besluit", "regeling", "circulaire"):
                # Een wet/AMvB komt binnen als <wet-besluit> met <wettekst> en
                # <wetsluiting>; een ministeriële regeling als <regeling> met
                # <regeling-tekst> en <regeling-sluiting>. Zelfde vorm, andere naam.
                # Een nadere regel van de NZa (NR/REG-1829, BWBR0041321) is een
                # <circulaire> met <circulaire-tekst> en <circulaire-sluiting>; tot 25
                # september 2026 een onbekend hoofdelement en dus een weigering.
                for deel in kind:
                    if deel.tag in ("wettekst", "regeling-tekst", "circulaire-tekst"):
                        self.container_inhoud(deel, niveau=2, pad={})
                    elif deel.tag == "bijlage":
                        self.bijlage(deel, niveau=2)
                    else:
                        self.plat(deel)
            elif kind.tag == "bijlage":
                self.bijlage(kind, niveau=2)
            elif kind.tag not in OVERSLAAN:
                # Weiger liever dan een onbekend hoofdelement stil plat te slaan
                # (zoals <regeling> dat vroeger deed): een stille terugval hier
                # verliest kop, lid, lijst en tabel zonder enige melding.
                raise ConversionError(
                    f"<{kind.tag}> is een onbekend hoofdelement direct onder "
                    "<wetgeving>; de omzetter kent alleen <intitule>, "
                    "<wet-besluit>, <regeling>, <circulaire> en <bijlage>."
                )
        return self.u

    def plat(self, el) -> None:
        """Aanhef, wetsluiting en dergelijke: alinea's in volgorde."""
        if el.tag in OVERSLAAN:
            return
        if el.tag == "ondertekening":
            # Eén regel, zoals de portal hem toont. Kind voor kind plat geslagen verloor de
            # losse tekst ertussen (`De`, ` van `, `, `): `De <functie>Minister</functie> van
            # <organisatie>Justitie</organisatie>` werd `Minister` en `Justitie` (kb WP-30:
            # BWBR0015808, BWBR0022835, BWBR0024926; kb WP-43). Een kind dat
            # `inline_el()` niet kent, blijft een weigering.
            self.u.blok(self.aaneen(el))
            return
        alleen_inline = all(c.tag in INLINE for c in el)
        if el.tag in ("al", "considerans.al", "wij", "slotformulering", "afkondiging") or alleen_inline:
            self.u.blok(ws(self.inline(el)))
            return
        for kind in el:
            self.plat(kind)

    def kop(self, el) -> tuple[str, str, str, bool]:
        """`(label, nr, titel, nr_eerst)`; `nr_eerst` als de bron `<nr>` vóór `<label>` zet.

        Het Wetboek van Koophandel schrijft `<nr>Vierde</nr><label>titel</label>`, en zo
        ook `Eerste Boek` en `Vijfde afdeeling`: het rangtelwoord staat vóór het label.
        Die volgorde is de bron (kb WP-09, klasse G).
        """
        k = el.find("kop")
        if k is None:
            return "", "", "", False
        for kind in k:
            # Wat hier niet genoemd is, viel tot kb WP-43 stil weg: zo de `<subtitel>` van
            # een bijlage (WP-30). Gemeten over de 79 BWB-bronnen van de kennisbank: alleen
            # `nr`, `label`, `titel` en `subtitel`.
            if kind.tag not in ("label", "nr", "titel", "subtitel") and kind.tag not in OVERSLAAN:
                self.u.markeer_onbekend(f"kop:{kind.tag}")
        label = ws(self.inline(k.find("label"))) if k.find("label") is not None else ""
        nr = ws(self.inline(k.find("nr"))) if k.find("nr") is not None else ""
        titel = ws(self.inline(k.find("titel"))) if k.find("titel") is not None else ""
        kinderen = [kind.tag for kind in k]
        nr_eerst = bool(label and nr) and kinderen.index("nr") < kinderen.index("label")
        if nr_eerst:
            self.omgedraaid.append(f"{nr} {label}")
        return label, nr, titel, nr_eerst

    def subtitel(self, el) -> None:
        """De `<subtitel>` van een kop als eigen alinea direct onder de kop.

        Het Besluit burgerservicenummer (BWBR0022829, bijlage 1) en het Besluit
        basisadministraties persoonsgegevens BES (BWBR0028622, bijlagen I en II) geven een
        bijlage een subtitel naast haar titel; `kop()` las hem niet, en hij viel weg (kb WP-30,
        `source-incomplete`). Onder de kop, niet erin: de kopregel is het ankerlabel, en
        een subtitel erin zou een kop maken die de bron niet als één titel geeft.
        """
        k = el.find("kop")
        if k is None:
            return
        for sub in k.findall("subtitel"):
            tekst = ws(self.inline(sub))
            if tekst:
                self.u.blok(tekst)

    def kopregel(self, label: str, nr: str, titel: str, nr_eerst: bool = False) -> str:
        # Zonder label en nummer is de kop alleen de titel. De Wet bescherming
        # persoonsgegevens BES (BWBR0028067) heeft een hoofdstuk met enkel
        # `<titel>Slotbepalingen</titel>`; dat werd `## . Slotbepalingen`, en de
        # kennisbank struikelde over een kop die met een leesteken begint
        # (WP-04, 23 september 2026).
        #
        # Label en nummer staan in de volgorde van de bron. Altijd eerst het label
        # maakte van `<nr>Vierde</nr><label>titel</label>` de regel `titel Vierde.`:
        # een vorm die de bron niet heeft en het profiel van de kennisbank niet kent.
        # Titel en boek kregen daar geen anker, en `§ 1` onder twee titels werd twee
        # keer `par-1` (het Wetboek van Koophandel, kb WP-09).
        paar = (nr, label) if nr_eerst else (label, nr)
        eerste = " ".join(x for x in paar if x)
        if not eerste:
            return titel
        return f"{eerste}. {titel}" if titel else eerste

    def meld_afbeeldingen(self) -> list[str]:
        weg = self.afbeeldingen_weggelaten
        if not weg:
            return []
        namen = [b["naam"] or b["id"] or "?" for b in weg]
        voorbeeld = ", ".join(namen[:5]) + (f" en {len(namen) - 5} meer" if len(namen) > 5 else "")
        return [f"{len(weg)} {'afbeelding' if len(weg) == 1 else 'afbeeldingen'} uit de BWB-XML niet "
                f"overgenomen; de tekst eromheen en een bijschrift staan er wel: {voorbeeld}."]

    def meld_omgedraaid(self) -> str | None:
        """Koppen waarin de bron het nummer vóór het label zet: gevolgd, en gemeld."""
        if not self.omgedraaid:
            return None
        vormen = list(dict.fromkeys(self.omgedraaid))
        voorbeeld = ", ".join(f"'{v}'" for v in vormen[:5]) + (
            f" en {len(vormen) - 5} meer" if len(vormen) > 5 else "")
        return (f"De BWB-XML zet {len(self.omgedraaid)} keer het nummer vóór het label in een "
                f"kop ({voorbeeld}); de omzetter volgt die volgorde.")

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
                label, nr, titel, nr_eerst = self.kop(kind)
                anker = self.container_anker(kind.tag, nr, pad)
                regel = self.kopregel(label, nr, titel, nr_eerst)
                self.u.blok(f"{'#' * min(niveau, 6)} {regel}")
                self.u.eenheid(anker, kind.tag, regel)
                self.subtitel(kind)
                eigen = anker.split("-", 1)[1] if not pad.get("annex") else anker.split(f"{CONTAINERS[kind.tag]}-", 1)[1]
                sleutel = {"hoofdstuk": "hfd", "afdeling": "afd", "titeldeel": "tit"}.get(kind.tag)
                nieuw = dict(pad)
                if sleutel:
                    nieuw[sleutel] = eigen.split("-")[-1] if sleutel != "tit" else eigen
                self.container_inhoud(kind, niveau + 1, nieuw)
            elif kind.tag == "artikel":
                self.artikel(kind, niveau, pad)
            elif kind.tag == "circulaire.divisie":
                self.circulairedivisie(kind, niveau, pad)
            elif kind.tag == "tekst":
                # In een circulaire staat de lopende tekst in een omhulsel zonder eigen
                # tekst (`<tekst><al>…</al><lijst>…</lijst></tekst>`); de kinderen tellen.
                self.container_inhoud(kind, niveau, pad)
            elif kind.tag in OVERSLAAN:
                continue
            elif kind.tag in ("al", "lijst", "table", "tussenkop"):
                self.inhoud(kind, basis="", prefix_noot="")
            else:
                self.u.markeer_onbekend(f"blok:{kind.tag}")
                self.container_inhoud(kind, niveau, pad)

    def circulairedivisie(self, el, niveau: int, pad: dict) -> None:
        """Een genummerd onderdeel van een circulaire: een kop, maar geen artikel.

        NR/REG-1829 (BWBR0041321) deelt zich in tien `circulaire.divisie`s in met
        `<kop><nr>1</nr><titel>Reikwijdte</titel></kop>` en geen label. De tekst
        verwijst ernaar als "artikel 4", maar de bron markeert ze niet zo; een label of
        een artikelanker zou de omzetter erbij bedenken. De kop wordt daarom de
        koptekst die de bron geeft (`1. Reikwijdte`), zonder structuureenheid, zoals
        een `divisie` in een bijlage er ook geen krijgt.
        """
        label, nr, titel, nr_eerst = self.kop(el)
        regel = self.kopregel(label, nr, titel, nr_eerst)
        if regel:
            self.u.blok(f"{'#' * min(niveau, 6)} {regel}")
        self.subtitel(el)
        self.container_inhoud(el, niveau + 1, pad)

    def artikel(self, el, niveau: int, pad: dict) -> None:
        label, nr, titel, nr_eerst = self.kop(el)
        if not nr:
            # De Wet RO (BWBR0001830) schrijft twee koppen zonder `<nr>`:
            # `<label>Artikel 11a</label>` en `<label>Artikel 59i</label>`. Het
            # nummer staat dan in het label; zonder deze lezing gaf de omzetter
            # twee keer het lege anker `art-` uit en weigerde de kennisbank het
            # zijbestand (T3-F3, kb WP-20). Een andere vorm zonder nummer is
            # niet gemeten en blijft een weigering: een artikel zonder anker
            # bestaat niet.
            m = re.fullmatch(r"(Artikel)\s+(\S+)", label)
            if not m:
                raise ConversionError(
                    f"Een artikel zonder <nr> en zonder nummer in het label ({label!r}); "
                    "omzetting geweigerd.")
            label, nr = m.group(1), m.group(2)
        naam = nummer_anker(nr)
        anker = f"{pad['annex']}-art-{naam}" if pad.get("annex") else f"art-{naam}"
        regel = self.kopregel(label or "Artikel", nr, titel, nr_eerst)
        self.u.blok(f"{'#' * min(niveau, 6)} {regel}")
        self.u.eenheid(anker, "artikel", regel)
        self.subtitel(el)
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
                # Ingesprongen onder het lid, zoals de onderdelen van een geldend lid
                # (`lid()`, diepte 1). Plat geschreven hingen de onderdelen van
                # artikel 3.1.3 Wlz aan het artikel (`art-3-1-3-a`); de kennisbank
                # leest beide vormen en geeft ze geen anker (T3-F1, kb WP-19 en
                # WP-20, besluit 8 van 25 september 2026). Nog steeds zonder
                # lidanker, dus zonder basis.
                self.inhoud(kind, basis="", prefix_noot="", diepte=1)

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
            if self.nootdefinitie(el, prefix_noot):
                return
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
        elif el.tag == "divisie":
            self.divisie(el, basis, prefix_noot, teller, diepte)
        elif el.tag == "plaatje":
            bijschrift = self.plaatje(el)
            if bijschrift:
                self.u.blok(f"{inspring}{bijschrift}")
        elif el.tag == "tussenkop":
            # Artikel 8:36c Awb staat er twee keer (digitaal en op papier); de
            # `tussenkop` scheidt beide varianten. Geen kop en geen eenheid: als
            # `######`-kop werd het een tweede `art-8-36c`, en een kale regel
            # "Artikel 8:36c." kan door kopherkenning gepromoveerd worden. De bron
            # zegt zelf cursief (`kopopmaak="cur"`), zoals `nadruk type="cur"`.
            tekst = ws(self.inline(el, prefix_noot))
            if tekst:
                cursief = el.get("kopopmaak") == "cur"
                self.u.blok(f"{inspring}*{tekst}*" if cursief else f"{inspring}{tekst}")
        elif el.tag in OVERSLAAN:
            return
        else:
            self.u.markeer_onbekend(f"inhoud:{el.tag}")
            tekst = ws(self.inline(el, prefix_noot))
            if tekst:
                self.u.blok(tekst)

    def plaatje(self, el) -> str:
        """Een afbeelding (`plaatje`): niet overnemen, wel vastleggen; het bijschrift is tekst.

        Dezelfde afspraak als bij de rechtspraakroute en de Formex-afbeelding: geen
        beeldbytes en geen plaatshouder in de Markdown, een melding in de herkomst en
        een lijst `afbeeldingen_weggelaten` in het zijbestand. Gemeten: de Wet BIG
        (BWBR0006251, één plaatje in een bijlage) en de Opiumwet (BWBR0001941, elf:
        zes in een divisie, vijf in een tabelcel); beide weigerden op `inhoud:plaatje`
        (T3-F16, kb WP-20). Een `bijschrift` (`Figuur 1`) is brontekst en blijft;
        een `illustratie` zonder bijschrift laat niets achter.
        """
        bijschriften = []
        for kind in el:
            if kind.tag == "illustratie":
                self.afbeeldingen_weggelaten.append({
                    "naam": kind.get("naam"), "id": kind.get("id"), "alt": kind.get("alt"),
                    "breedte": kind.get("breedte"), "hoogte": kind.get("hoogte"),
                    "formaat": kind.get("formaat")})
            elif kind.tag == "bijschrift":
                tekst = ws(self.inline(kind))
                if tekst:
                    bijschriften.append(tekst)
            elif kind.tag in OVERSLAAN:
                continue
            else:
                self.u.markeer_onbekend(f"plaatje:{kind.tag}")
        return " ".join(bijschriften)

    def nootdefinitie(self, el, prefix_noot: str) -> bool:
        """Schrijf een bijlagenoot, ook wanneer die onder een divisie staat."""
        if (not prefix_noot or el.tag != "al" or (el.text or "").strip()
                or not len(el) or el[0].tag != "sup"):
            return False
        cijfer = ws("".join(el[0].itertext()))
        if not cijfer.isdigit():
            return False
        rest = (el[0].tail or "") + "".join(
            self.inline_el(kind, prefix_noot) + (kind.tail or "") for kind in list(el)[1:]
        )
        self.definieer(f"{prefix_noot}{cijfer}")
        self.u.blok(f"[^{prefix_noot}{cijfer}]: {ws(rest)}")
        return True

    def divisie(self, el, basis: str, prefix_noot: str,
                teller: dict | None, diepte: int,
                niveau: int | None = None, pad: dict | None = None) -> None:
        """Een benoemd blok binnen een bijlage, gemeten in BWBR0034306.

        De zeven divisies daar bevatten ieder een kop en een CALS-tabel; één
        bevat daarnaast de definitie van een tabelnoot. De kop blijft een
        gewone bronalinea, omdat een divisie geen citeeranker draagt.

        Bijlage 2 Awb (Bevoegdheidsregeling) deelt haar twaalf artikelen in vier
        divisies "Hoofdstuk 1–4" in. Een artikel daarin is een gewoon artikel met
        een citeeranker (`annex-2-art-7`); daarvoor moet `bijlage()` het kopniveau
        en het ankerpad meegeven. Zonder die twee blijft een artikel onbekend.
        """
        label, nr, titel, nr_eerst = self.kop(el)
        regel = self.kopregel(label, nr, titel, nr_eerst) if label or nr else titel
        if regel:
            self.u.blok(regel)
        self.subtitel(el)
        for kind in el:
            if kind.tag == "kop" or kind.tag in OVERSLAAN:
                continue
            if kind.tag == "artikel" and niveau is not None:
                self.artikel(kind, niveau, pad or {})
            else:
                self.inhoud(kind, basis, prefix_noot, teller, diepte)

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
        # Een `<title>` staat als alinea boven de tabel; tot kb WP-43 las `tabel()` alleen de
        # `tgroup`s, en vielen de zes tabeltitels van het Besluit verplichte politiegegevens
        # (BWBR0032083) weg (kb WP-30, `source-incomplete`). Een ander kind viel ook stil
        # weg; gemeten over de 79 BWB-bronnen van de kennisbank zijn er geen andere.
        for kind in el:
            if kind.tag == "title":
                tekst = ws(self.inline(kind, prefix_noot))
                if tekst:
                    self.u.blok(tekst)
            elif kind.tag != "tgroup" and kind.tag not in OVERSLAAN:
                self.u.markeer_onbekend(f"table:{kind.tag}")
        for tgroup in el.findall("tgroup"):
            kolommen = {c.get("colname"): i for i, c in enumerate(tgroup.findall("colspec"))}
            rijen, koprijen = [], 0
            for sectie in ("thead", "tbody"):
                for s in tgroup.findall(sectie):
                    for row in s.findall("row"):
                        cellen = []
                        for entry in row.findall("entry"):
                            kinderen = [a for a in entry if a.tag not in OVERSLAAN]
                            # Een cel met alleen een plaatje zonder bijschrift is leeg;
                            # terugvallen op `inline(entry)` zou het plaatje twee keer tellen.
                            tekst = (" ".join((self.plaatje(a) if a.tag == "plaatje"
                                               else ws(self.inline(a, prefix_noot))) for a in kinderen)
                                     if kinderen else ws(self.inline(entry, prefix_noot)))
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
        label, nr, titel, nr_eerst = self.kop(el)
        n = nummer_anker(nr, romeins_omrekenen=True) if nr else str(len(self.bijlage_ankers) + 1)
        anker = f"annex-{n}"
        self.bijlage_ankers.append(anker)
        self.huidige_bijlage = anker
        prefix = f"{anker}-"
        # Dezelfde vorm als `nootdefinitie()`: een `<al>` zonder tekst vooraf die met
        # `<sup>n</sup>` begint. Een `<sup>` met dat nummer in deze bijlage is een marker.
        self.sup_definities = {
            f"{prefix}{ws(''.join(al[0].itertext()))}" for al in el.iter("al")
            if not (al.text or "").strip() and len(al) and al[0].tag == "sup"
            and ws("".join(al[0].itertext())).isdigit()}
        regel = self.kopregel(label or "Bijlage", nr, titel, nr_eerst)
        self.u.blok(f"{'#' * niveau} {regel}")
        self.u.eenheid(anker, "bijlage", regel)
        self.subtitel(el)
        for kind in el:
            if kind.tag == "kop" or kind.tag in OVERSLAAN:
                continue
            if kind.tag in CONTAINERS or kind.tag == "artikel":
                wrapper = ET.Element("x")
                wrapper.append(kind)
                self.container_inhoud(wrapper, niveau + 1, {"annex": anker})
            elif kind.tag == "divisie":
                self.divisie(kind, anker, prefix, None, 0,
                             niveau=niveau + 1, pad={"annex": anker})
            else:
                self.inhoud(kind, basis=anker, prefix_noot=prefix)
        self.huidige_bijlage = ""
        self.sup_definities = set()


def omzetten(data: bytes | str) -> tuple[str, list, dict, dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConversionError(f"BWB-XML is niet leesbaar: {exc}") from exc
    o = BwbOmzetter()
    u = o.omzetten(root)
    if u.wachtend:
        raise ConversionError("Een BWB-noot kreeg geen blok om onder te staan; omzetting geweigerd.")
    md = u.markdown()
    # Een marker die in een tussenresultaat bleef hangen (een kop die alleen voor een
    # anker wordt gelezen) laat een definitie zonder marker achter: weigeren, niet
    # een losse noot doorlaten.
    tekst = "\n".join(r for r in md.splitlines() if not re.match(r"^\[\^[^\]]+\]:", r))
    zonder = sorted(label for label, _ in o.noten if f"[^{label}]" not in tekst)
    if zonder:
        raise ConversionError(f"Een BWB-noot heeft geen marker in de uitvoer: {zonder}; omzetting geweigerd.")
    melding = o.meld_omgedraaid()
    return md, u.eenheden, u.onbekend, {
        "herhaalde_cellen": o.herhaalde_cellen,
        "expired": o.expired,
        "omgedraaide_koppen": len(o.omgedraaid),
        "noten": len(o.noten),
        "waarschuwingen": ([melding] if melding else []) + o.meld_afbeeldingen(),
        "afbeeldingen_weggelaten": o.afbeeldingen_weggelaten,
    }
