"""Behoud expliciete voetnootrelaties in de bijlagen van wetten.overheid.nl.

Alleen de bronvorm met een numerieke <sup class="sup"> in een genummerde
bijlage en een overeenkomstige definitie wordt native Markdown. Een definitie
begint een alinea buiten de tabel. Een kaal suffixcijfer wordt nooit geraden.
"""
from __future__ import annotations

import re
from collections import defaultdict

from bs4 import NavigableString

from ..errors import ConversionError


def prepare_footnotes(container) -> None:
    """Normaliseer gekoppelde noten in-place; weiger dubbele/ongekoppelde noten."""
    scopes = list(container.select('div.bijlage[id]'))
    if container.name == 'div' and 'bijlage' in container.get('class', []):
        scopes.insert(0, container)
    for scope in scopes:
        match = re.fullmatch(r'Bijlage(\d+)', scope.get('id', ''))
        if not match:
            continue
        prefix = f'annex-{int(match[1])}'
        supers = [sup for sup in scope.select('sup.sup')
                  if re.fullmatch(r'\d{1,3}', sup.get_text(strip=True))]
        definitions = {}
        refs = defaultdict(list)
        for sup in supers:
            number = sup.get_text(strip=True)
            paragraph = sup.find_parent('p')
            leading = (paragraph is not None and paragraph.find_parent('table') is None
                       and not ''.join(str(node) for node in sup.previous_siblings).strip()
                       and sup.parent is paragraph)
            if leading:
                if number in definitions:
                    raise ConversionError(f'Voetnoot {prefix}-{number} heeft meerdere definities.')
                definitions[number] = (sup, paragraph)
            else:
                refs[number].append(sup)
        for number, markers in refs.items():
            if number not in definitions:
                for marker in markers:
                    previous = marker.find_previous_sibling()
                    if (previous is not None and previous.name == 'a'
                            and 'eurlex-link' in previous.get('class', [])):
                        raise ConversionError(f'Voetnootmarker {prefix}-{number} na een rechtsverwijzing heeft geen definitie.')
        # Zonder nootdefinitie kan dit een macht of rangtelwoord zijn. Dat is
        # geen reden om er een voetnoot van te maken.
        if not definitions:
            continue
        for number, (sup, paragraph) in definitions.items():
            if number not in refs:
                raise ConversionError(f'Voetnoot {prefix}-{number} heeft geen marker in de bijlage.')
        for number, (sup, paragraph) in definitions.items():
            name = f'{prefix}-{number}'
            sup.replace_with(NavigableString(f'[^{name}]: '))
            # Opmaak-newlines van de HTML zijn geen nieuwe alinea's; een
            # native definitie moet ook na markdownify één alinea blijven.
            for node in list(paragraph.descendants):
                if isinstance(node, NavigableString):
                    node.replace_with(NavigableString(re.sub(r'[\r\n\t ]+', ' ', str(node))))
            for marker in refs[number]:
                marker.replace_with(NavigableString(f'[^{name}]'))
