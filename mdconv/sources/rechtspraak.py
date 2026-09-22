"""Nederlandse rechtspraak via de officiële Open Data API van de Rechtspraak.

`https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>` geeft de uitspraak in
het schema `rechtspraak-1.0`: `section`/`title`/`nr`/`paragroup`/`parablock`/
`para`, CALS-tabellen, en - anders dan elke HTML- of PDF-afgeleide - de
**nootrelatie zelf**: `<footnote-ref linkend>` wijst naar `<footnote id>`, en die
draagt zijn eigen `label`. De Pandoc-route verloor die relatie en liet kale
regels onder de ondertekening achter; die noten zijn hier gewoon te koppelen.

Twee dingen die deze route bewust **niet** doet, allebei omdat ze de raw-vorm
zouden veranderen die `md-clean-jurisprudentie` vandaag al aankan:

- **Opmaak.** `<emphasis role="bold|italic|underline|…">` levert geen `**` of
  `*` op. De tekst blijft, de opmaak niet. Een kop die als `**De beslissing**`
  in de raw komt, komt niet meer door `SECTION_ANCHORS` van het profiel.
- **Kopniveaus.** `<title>` wordt een kop op de diepte die de bron toont, niet
  op een diepte die uit de inhoud is afgeleid.

Inhoudsafbeeldingen worden niet in de Markdown opgenomen. De omzetting meldt
aantal, afmetingen en bron-id als waarschuwing en legt dezelfde gegevens vast in
het zijbestand. Een `orderedlist` met onbekende nummering, een onbekend element
met tekst en elke tabel die niet rechthoekig te maken is, blijven weigeringen.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import unquote

from lxml import etree

from .. import net
from ..errors import ConversionError
from ..herkomst import Herkomst
from ..source_structure import record_source
from . import xml_gedeeld as xg

ECLI_RE = re.compile(r"ECLI:[A-Z]{2}:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+", re.I)

_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40

# Een afbeelding van hooguit twee pixels hoog is een spacer of de scheidingslijn
# uit het briefhoofd. Gemeten over 300 uitspraken: 66 van de 94 afbeeldingen
# hebben depth 1 of 2, de overige 28 zijn 16 pixels of hoger en zijn foto's,
# kaartjes en schema's. Die tweede groep is inhoud. Op uitdrukkelijk verzoek van
# de gebruiker wordt ze weggelaten, maar wel zichtbaar en machineleesbaar gemeld.
_DECORATIE_MAX_DEPTH = 2

# Containers zonder eigen betekenis in de uitvoer: de kinderen tellen.
_TRANSPARANT = {"uitspraak.info", "conclusie.info", "parablock", "paragroup",
                "mediaobject", "inlinemediaobject", "imageobject"}

# Elementen die alleen binnen een tabel of een inline-context voorkomen en daar
# door hun eigen behandeling worden opgegeten.
_IN_TABEL = {"tgroup", "colspec", "spanspec", "thead", "tfoot", "tbody", "row", "entry"}

_ROMEINS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii",
            "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"]

# De tekens waarmee een `orderedlist` haar items nummert, per `numeration`.
_NUMMERING = {
    "arabic": str,
    "loweralpha": lambda n: chr(ord("a") + n - 1) if 1 <= n <= 26 else str(n),
    "upperalpha": lambda n: chr(ord("A") + n - 1) if 1 <= n <= 26 else str(n),
    "lowerroman": lambda n: _ROMEINS[n - 1] if 1 <= n <= len(_ROMEINS) else str(n),
    "upperroman": lambda n: _ROMEINS[n - 1].upper() if 1 <= n <= len(_ROMEINS) else str(n),
}


def matches(query: str) -> bool:
    return "rechtspraak.nl" in query.lower() or bool(re.search(r"ECLI:NL:", query, re.I))


def fetch(query: str) -> tuple[str, str, Herkomst]:
    """Haal een uitspraak op; geeft (markdown, bronvermelding, herkomst)."""
    # Rechtspraak.nl codeert de dubbele punten in de `id`-queryparameter als
    # `%3A`. De vijf links uit de testronde van 21 september 2026 werden
    # daardoor vóór de netwerkaanroep geweigerd, hoewel hun ECLI geldig was.
    m = ECLI_RE.search(unquote(query))
    if not m:
        raise ConversionError("Geen geldig ECLI-nummer herkend (bv. ECLI:NL:HR:2012:BQ9251).")
    ecli = m.group(0).upper()

    url = f"https://data.rechtspraak.nl/uitspraken/content?id={ecli}"
    r = net.documents().get(url, timeout=_TIMEOUT)
    data = getattr(r, "content", None) or (r.text or "").encode("utf-8")
    if r.status_code != 200 or not data.strip():
        raise ConversionError(f"Kon uitspraak {ecli} niet ophalen (status {r.status_code}).")

    markdown, meta = omzetten(data, ecli)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(
            f"Uitspraak {ecli} bevat geen (open) tekst. Mogelijk is alleen metadata beschikbaar."
        )

    record_source(data, media_type="application/xml", source_format="rechtspraak-xml",
                  source_url=url, identifier=ecli, language=meta.get("taal") or "nl")

    herkomst = Herkomst(
        format="rechtspraak-xml",
        ecli=ecli,
        title=meta.get("titel"),
        language=meta.get("taal") or "nl",
        source_url=meta.get("deeplink") or url,
        requested_url=url,
        koppen_bron=meta["koppen_bron"],
        koppen_markdown=meta["koppen_markdown"],
        waarschuwingen=tuple(meta["waarschuwingen"]),
        extra={k: v for k, v in meta.items()
               if k not in {"titel", "taal", "koppen_bron", "koppen_markdown", "waarschuwingen"}},
    )
    return markdown, f"Rechtspraak.nl • {ecli}", herkomst


# --------------------------------------------------------------------------
# De bron lezen
# --------------------------------------------------------------------------

def _kort(el) -> str:
    """De elementnaam zonder naamruimte; een processing instruction heet `?<doel>`."""
    if isinstance(el.tag, str):
        return el.tag.rsplit("}", 1)[-1]
    if isinstance(el, etree._ProcessingInstruction):
        return "?" + (el.target or "")
    return "?"


def _ws(tekst: str) -> str:
    return " ".join(tekst.split())


def _wortel(data: bytes):
    # `recover` staat bewust uit: een bron die niet heel is, is een weigering.
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ConversionError(f"De uitspraak is geen leesbare XML: {exc}") from exc
    if _kort(root) != "open-rechtspraak":
        raise ConversionError(
            f"Het worteldocument is <{_kort(root)}> en geen <open-rechtspraak>; "
            "dit is geen uitvoer van de Open Data API.")
    return root


def _metadata(root, ecli: str) -> dict:
    """De Dublin Core- en psi-velden, plus de identiteitscontrole.

    De bron moet zeggen wie hij is. Een document dat binnenkomt maar een andere
    ECLI draagt is een weigering en geen terugval: anders levert een
    identiteitsfout stilletjes een ander document op.
    """
    velden: dict[str, list] = {}
    deeplink = None
    for el in root.iter():
        naam = _kort(el)
        if naam in ("uitspraak", "conclusie"):
            break
        waarde = _ws("".join(el.itertext()))
        if naam == "identifier" and waarde.upper().startswith("ECLI:"):
            velden.setdefault("identifier", []).append(waarde.upper())
        elif naam == "identifier" and waarde.startswith("http"):
            deeplink = deeplink or waarde
        elif naam in ("title", "creator", "date", "issued", "modified", "language",
                      "zaaknummer", "procedure", "subject", "type", "coverage", "publisher"):
            velden.setdefault(naam, []).append(waarde)
        elif naam == "relation" and waarde:
            velden.setdefault("relation", []).append(waarde)
        elif naam == "li" and waarde:
            velden.setdefault("vindplaatsen", []).append(waarde)
    gevonden = velden.get("identifier") or []
    if ecli not in gevonden:
        raise ConversionError(
            f"De opgehaalde bron noemt zichzelf {gevonden or ['geen ECLI']} en niet {ecli}.")
    eerst = lambda sleutel: (velden.get(sleutel) or [None])[0]
    inhoud = [el for el in root.iter() if _kort(el) == "inhoudsindicatie"]
    return {
        "titel": eerst("title"),
        "taal": eerst("language"),
        "deeplink": deeplink,
        "instantie": eerst("creator"),
        "uitspraakdatum": eerst("date"),
        "publicatiedatum": eerst("issued"),
        "gewijzigd": eerst("modified"),
        "zaaknummer": eerst("zaaknummer"),
        "procedure": eerst("procedure"),
        "rechtsgebied": eerst("subject"),
        "documentsoort": eerst("type"),
        "relaties": velden.get("relation") or [],
        "vindplaatsen": velden.get("vindplaatsen") or [],
        "inhoudsindicatie": _ws("".join(inhoud[0].itertext())) if inhoud else None,
    }


# --------------------------------------------------------------------------
# De omzetting
# --------------------------------------------------------------------------

class _Lezer:
    """Loopt de uitspraakboom door en vult een `xml_gedeeld.Uitvoer`."""

    def __init__(self) -> None:
        self.uit = xg.Uitvoer()
        self.noot_labels: dict[str, str] = {}
        self.gebruikte_noten: list[str] = []
        self.koppen = 0
        self.opmaak_weggelaten = 0
        self.regelvallen = 0
        self.lijstitems = 0
        self.afbeeldingen_weggelaten: list[dict] = []
        self.gegenereerd: Counter = Counter()
        self.secties: list[dict] = []
        self.bladteksten: list[str] = []

    # -- inline ---------------------------------------------------------

    def inline(self, el, *, eenregelig: bool = False) -> str:
        """De tekst van een element als blok: witruimte plat, randen af.

        Opmaak (`<emphasis>`) levert géén Markdown op; zie de moduledocstring.
        Een `<?linebreak?>` wordt een vervolgregel met één spatie ervoor, de
        vorm die `conventions.md` paragraaf 4 voor een afgebroken alinea kent en
        die `check_anchors` als onderdeel van hetzelfde blok leest. Een lege
        regel draagt geen tekst en verdwijnt, en een kop wordt altijd op één
        regel gezet: `### ` met de tekst een regel lager is geen kop meer.
        """
        regels = [" ".join(regel.split()) for regel in self._ruw(el).split("\n")]
        regels = [regel for regel in regels if regel]
        return " ".join(regels) if eenregelig else "\n ".join(regels)

    def _ruw(self, el) -> str:
        """Dezelfde tekst, maar zonder de randen af te halen.

        Een geneste aanroep mág niet strippen: `Egelie,<emphasis> FED</emphasis>`
        draagt zijn spatie bínnen het opmaakelement, en die weghalen plakt twee
        vindplaatsen aan elkaar. Dat verschil wees de kb-parser aan, die de bron
        met `itertext()` leest en de spatie dus wél had.
        """
        delen = [el.text or ""]
        for kind in el:
            naam = _kort(kind)
            if naam == "?linebreak":
                delen.append("\n ")
                self.regelvallen += 1
            elif naam == "footnote-ref":
                doel = kind.get("linkend")
                label = self.noot_labels.get(doel)
                if label is None:
                    raise ConversionError(
                        f"Een nootmarker verwijst naar een onbekende voetnoot ({doel}).")
                self.gebruikte_noten.append(label)
                delen.append(f"[^{label}]")
            elif naam == "emphasis":
                self.opmaak_weggelaten += 1
                delen.append(self._ruw(kind))
            elif naam in ("nr", "superscript"):
                # `<title><nr>1</nr>De procedure</title>` levert `1De procedure`,
                # precies zoals de bron het aan elkaar zet; H6 in `preclean.py`
                # maakt daar `1. De procedure` van.
                delen.append(self._ruw(kind))
            elif naam in ("para", "parablock", "paragroup"):
                # Een tabelcel draagt haar tekst in alinea's; die horen bij de cel.
                delen.append(self._ruw(kind))
            elif naam in ("mediaobject", "inlinemediaobject", "imageobject"):
                delen.append(self._ruw(kind))
            elif naam == "imagedata":
                self.afbeelding(kind)
            elif naam.startswith("?"):
                pass  # een comment of andere PI draagt geen tekst
            else:
                raise ConversionError(
                    f"XML-element zonder eigen behandeling binnen een alinea ({naam}); "
                    "omzetting geweigerd.")
            delen.append(kind.tail or "")
        return "".join(delen)

    def afbeelding(self, el) -> None:
        try:
            depth = int(el.get("depth") or 0)
        except ValueError:
            depth = 0
        if depth > _DECORATIE_MAX_DEPTH:
            self.afbeeldingen_weggelaten.append({
                "fileref": el.get("fileref"),
                "width": el.get("width"),
                "depth": depth,
                "format": el.get("format"),
            })

    # -- blokken --------------------------------------------------------

    def blad(self, tekst: str) -> None:
        if tekst.strip():
            self.bladteksten.append(tekst)

    def lees(self, el, diepte: int, wacht: list[str]) -> None:
        for kind in el:
            naam = _kort(kind)
            if naam == "title":
                tekst = self.inline(kind, eenregelig=True)
                if tekst:
                    niveau = min(diepte + 2, 6)
                    self.uit.blok(f"{'#' * niveau} {tekst}")
                    self.koppen += 1
                    self.blad(tekst)
                    rol = el.get("role") if _kort(el) == "section" else None
                    self.secties.append({"kop": tekst, "niveau": niveau, "rol": rol})
            elif naam == "nr":
                wacht[0] = self.inline(kind)
            elif naam in ("para", "bridgehead"):
                tekst = self.inline(kind)
                if wacht[0] and tekst:
                    tekst = f"{wacht[0]} {tekst}".strip()
                    wacht[0] = ""
                if tekst:
                    self.uit.blok(tekst)
                    self.blad(tekst)
            elif naam == "section":
                self.lees(kind, diepte + 1, wacht)
            elif naam in _TRANSPARANT:
                self.lees(kind, diepte, wacht)
            elif naam in ("itemizedlist", "orderedlist"):
                self.lijst(kind, wacht)
            elif naam in ("informaltable", "table"):
                self.tabel(kind)
            elif naam == "footnote":
                pass  # de definities worden vooraf verzameld
            elif naam == "imagedata":
                self.afbeelding(kind)
            elif naam.startswith("?"):
                pass
            elif len(kind) or (kind.text or "").strip():
                self.uit.markeer_onbekend(naam)

    def lijst(self, lijst, wacht: list[str]) -> None:
        """Een opsomming, met het teken dat de bron zelf noemt.

        Bij `itemizedlist` staat het teken in `mark`, bij `orderedlist` de soort
        nummering in `numeration`. Dat laatste is geen gok: de bron zegt dát er
        genummerd wordt en hóé, alleen niet met welke tekens. Gemeten over 159
        uitspraken: elke `orderedlist` begint bij één en geen enkele draagt een
        beginwaarde. De gegenereerde markers worden apart geteld, zodat de
        woordcontrole ze niet voor brontekst aanziet.
        """
        genummerd = _kort(lijst) == "orderedlist"
        mark = lijst.get("mark")
        numeration = lijst.get("numeration") or "arabic"
        if not genummerd and mark not in (None, "-"):
            raise ConversionError(f"Onbekend opsommingsteken in de bron ({mark!r}); omzetting geweigerd.")
        if genummerd and numeration not in _NUMMERING:
            raise ConversionError(
                f"Onbekende nummering in een opsomming ({numeration!r}); omzetting geweigerd.")
        teller = 0
        for item in lijst:
            if _kort(item) != "listitem":
                raise ConversionError(f"Onverwacht element in een opsomming ({_kort(item)}).")
            regels = []
            for kind in item:
                naam = _kort(kind)
                if naam in ("para", "bridgehead"):
                    tekst = self.inline(kind)
                    if tekst:
                        regels.append(tekst)
                        self.blad(tekst)
                elif naam in _TRANSPARANT:
                    for klein in kind:
                        tekst = self.inline(klein)
                        if tekst:
                            regels.append(tekst)
                            self.blad(tekst)
                else:
                    self.uit.markeer_onbekend(naam)
            if regels:
                teller += 1
                marker = f"{_NUMMERING[numeration](teller)}." if genummerd else "-"
                if genummerd:
                    self.gegenereerd.update(_woorden(marker))
                eerste, rest = regels[0], regels[1:]
                blok = f"{marker} " + eerste
                for regel in rest:
                    blok += "\n" + " " * (len(marker) + 1) + regel
                self.uit.blok(blok)
                self.lijstitems += 1

    def tabel(self, tabel) -> None:
        groepen = [g for g in tabel if _kort(g) == "tgroup"]
        if len(groepen) != 1:
            raise ConversionError(
                f"Een tabel heeft {len(groepen)} tgroup-elementen; precies één is vereist.")
        groep = groepen[0]
        kolomnamen = {}
        for nummer, spec in enumerate((s for s in groep if _kort(s) == "colspec"), start=1):
            if spec.get("colname"):
                kolomnamen[spec.get("colname")] = int(spec.get("colnum") or nummer) - 1
        kop_rijen = 0
        rijen: list[list[dict]] = []
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
                    colspan = 1
                    kol = None
                    if start or eind:
                        if start not in kolomnamen or eind not in kolomnamen:
                            raise ConversionError(
                                "Een cel verwijst naar een kolomnaam die de tabel niet kent.")
                        kol = kolomnamen[start]
                        colspan = kolomnamen[eind] - kolomnamen[start] + 1
                    tekst = " ".join(self.inline(cel).split())
                    rowspan = int(cel.get("morerows") or 0) + 1
                    self.blad(tekst)
                    # `tabel_markdown` herhaalt een overspannen cel op elke plek
                    # die hij in de bron beslaat. Die kopieën zijn opmaak en geen
                    # brontekst, dus ze horen in de gegenereerde telling.
                    if rowspan * colspan > 1:
                        self.gegenereerd.update(_woorden(tekst) * (rowspan * colspan - 1))
                    cellen.append({"tekst": tekst, "kol": kol, "colspan": colspan,
                                   "rowspan": rowspan})
                rijen.append(cellen)
                if naam == "thead":
                    kop_rijen += 1
        markdown, _ = xg.tabel_markdown(rijen, kop_rijen)
        self.uit.blok(markdown)


def _noot_definities(lichaam, lezer: _Lezer) -> None:
    """Verzamel de noten vóór het lopen, want een marker staat vóór zijn definitie."""
    for note in lichaam.iter():
        if _kort(note) != "footnote":
            continue
        label = (note.get("label") or "").strip()
        if not label:
            raise ConversionError("Een voetnoot in de bron heeft geen label.")
        sleutel = note.get("id") or label
        if sleutel in lezer.noot_labels:
            raise ConversionError(f"Dubbele voetnoot-id in de bron: {sleutel}.")
        lezer.noot_labels[sleutel] = label


# Een nootmarker en een nootlabel zijn geen brontekst maar een verwijzing; ze
# moeten aan beide kanten van de vergelijking weg, anders telt `[^1]` als het
# woord "1" en klopt de woordverzameling nooit.
_MARKER = re.compile(r"\[\^[^\]]+\]:?")


def _woorden(tekst: str) -> list[str]:
    return re.findall(r"\w+", _MARKER.sub(" ", tekst), re.UNICODE)


def omzetten(data: bytes, ecli: str) -> tuple[str, dict]:
    """De uitspraak als Markdown, plus wat het zijbestand erover vastlegt."""
    root = _wortel(data)
    meta = _metadata(root, ecli)

    lichaam = [el for el in root.iter() if _kort(el) in ("uitspraak", "conclusie")]
    if len(lichaam) != 1:
        raise ConversionError(
            f"De bron bevat {len(lichaam)} uitspraak- of conclusie-elementen; precies één is vereist.")
    lichaam = lichaam[0]

    lezer = _Lezer()
    _noot_definities(lichaam, lezer)
    titel = meta.get("titel") or ecli
    lezer.uit.blok(f"# {titel}")
    lezer.koppen += 1
    lezer.blad(titel)
    lezer.lees(lichaam, 0, [""])

    for note in lichaam.iter():
        if _kort(note) != "footnote":
            continue
        label = lezer.noot_labels[note.get("id") or (note.get("label") or "").strip()]
        tekst = " ".join(lezer.inline(note).split())
        lezer.uit.noten.append((label, tekst))
        lezer.blad(tekst)

    markdown = lezer.uit.markdown()
    _zelfcontrole(lezer, markdown)

    waarschuwingen = []
    if lezer.afbeeldingen_weggelaten:
        details = ", ".join(
            f"{beeld['width'] or '?'}x{beeld['depth']} pixels "
            f"(bron-id {beeld['fileref'] or 'onbekend'})"
            for beeld in lezer.afbeeldingen_weggelaten
        )
        aantal = len(lezer.afbeeldingen_weggelaten)
        soort = "inhoudsafbeelding" if aantal == 1 else "inhoudsafbeeldingen"
        waarschuwingen.append(
            f"{aantal} {soort} niet overgenomen; de tekst is zonder beeld geconverteerd: "
            f"{details}.")
    if lezer.opmaak_weggelaten:
        waarschuwingen.append(
            f"{lezer.opmaak_weggelaten} keer opmaak (<emphasis>) niet overgenomen; de tekst blijft.")
    ongebruikt = sorted(set(lezer.noot_labels.values()) - set(lezer.gebruikte_noten), key=str)
    if ongebruikt:
        waarschuwingen.append(
            f"voetnoot zonder marker in de bron: {', '.join(ongebruikt)}")

    meta.update({
        "koppen_bron": lezer.koppen,
        "koppen_markdown": sum(1 for r in markdown.splitlines() if r.startswith("#")),
        "waarschuwingen": waarschuwingen,
        "afbeeldingen_weggelaten": lezer.afbeeldingen_weggelaten,
        "secties": lezer.secties,
        "noten": len(lezer.uit.noten),
        "lijstitems": lezer.lijstitems,
        "regelvallen": lezer.regelvallen,
        "engine": "rechtspraak-xml",
    })
    return markdown, meta


def _zelfcontrole(lezer: _Lezer, markdown: str) -> None:
    """Weigeren als de omzetting tekst heeft verloren, verweven of verdubbeld.

    Twee controles die elkaar aanvullen, dezelfde als bij de Formex-route: elke
    tekstdragende bladalinea moet **aaneengesloten** in de uitvoer voorkomen
    (dat vangt afbreken en verweven), en de woordverzameling moet als multiset
    gelijk zijn (dat vangt verlies en verdubbeling).
    """
    kaal = re.sub(r"^\s*#{1,6}\s+", "", markdown, flags=re.M)
    woorden_uit = _woorden(kaal)
    uit_multiset = Counter(woorden_uit)
    bron_multiset = Counter(lezer.gegenereerd)
    for tekst in lezer.bladteksten:
        bron_multiset.update(_woorden(tekst))
    if bron_multiset != uit_multiset:
        tekort = bron_multiset - uit_multiset
        teveel = uit_multiset - bron_multiset
        raise ConversionError(
            "De omzetting mist of verdubbelt tekst ten opzichte van de bron "
            f"(ontbreekt: {dict(list(tekort.items())[:5])}; te veel: "
            f"{dict(list(teveel.items())[:5])}); omzetting geweigerd.")
    for tekst in lezer.bladteksten:
        deel = _woorden(tekst)
        if deel and not _bevat(woorden_uit, deel):
            raise ConversionError(
                f"Een alinea uit de bron staat niet aaneengesloten in de uitvoer: {tekst[:70]!r}; "
                "omzetting geweigerd.")


def _bevat(reeks: list[str], deel: list[str]) -> bool:
    eerste = deel[0]
    for i, woord in enumerate(reeks):
        if woord == eerste and reeks[i:i + len(deel)] == deel:
            return True
    return False
