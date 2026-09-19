"""Bronvorm uit BWBR0052872/2026-08-15, zonder netwerk in regressietests."""
import pytest
from bs4 import BeautifulSoup

from mdconv.errors import ConversionError
from mdconv.sources.wetten import _html_to_markdown
from mdconv.sources.wetten_footnotes import prepare_footnotes


def page(contents):
    return '<div id="regeling"><h1>Cyberbeveiligingswet</h1><div class="wetgeving">' + contents + '</div></div>'


def annex(number, note=5, instrument='2017/745'):
    return f'''<div class="bijlage" id="Bijlage{number}"><h4>Bijlage {number}</h4>
    <table><tr><th>Entiteit</th></tr><tr><td><p class="al">Entiteit in
    <a class="eurlex-link" href="https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32017R0745">Verordening (EU) {instrument}</a><sup class="sup">{note}</sup>.</p></td></tr></table>
    <p class="al"><sup class="sup">{note}</sup>
    <a class="eurlex-link">Verordening (EU) {instrument}</a> van het Europees Parlement
    en de Raad.</p></div>'''


def test_instrument_and_footnote_are_separate():
    markdown = _html_to_markdown(page(annex(2)))
    assert '2017/745[^annex-2-5]' in markdown
    assert '[^annex-2-5]: Verordening (EU) 2017/745 van het Europees Parlement en de Raad.' in markdown
    assert '2017/7455' not in markdown


def test_number_restart_is_scoped_per_annex():
    markdown = _html_to_markdown(page(annex(1) + annex(2)))
    assert markdown.count('[^annex-1-5]') == 2
    assert markdown.count('[^annex-2-5]') == 2


def test_duplicates_refused():
    text = annex(2).replace('</div>', '<p><sup class="sup">5</sup>Dubbele definitie.</p></div>')
    with pytest.raises(ConversionError, match='meerdere definities'):
        _html_to_markdown(page(text))


def test_marker_after_legal_link_without_definition_refused():
    text = annex(2).replace('<p class="al"><sup class="sup">5</sup>', '<p class="al">')
    with pytest.raises(ConversionError, match='geen definitie'):
        _html_to_markdown(page(text))


def test_definition_without_marker_refused():
    text = annex(2).replace('</a><sup class="sup">5</sup>', '</a>')
    with pytest.raises(ConversionError, match='geen marker'):
        _html_to_markdown(page(text))


def test_genuine_superscript_not_interpreted_as_note():
    soup = BeautifulSoup(page(annex(2) + '<div class="bijlage" id="Bijlage3"><p>10<sup class="sup">2</sup></p></div>'), 'lxml')
    prepare_footnotes(soup)
    assert soup.select_one('#Bijlage3 sup').get_text() == '2'
    assert '[^annex-3-2]' not in str(soup)


def test_bare_suffix_is_not_guessed():
    text = '<div class="bijlage" id="Bijlage2"><p>Verordening (EU) 2017/7455.</p><p>5 Verordening (EU) 2017/745.</p></div>'
    assert '2017/7455' in _html_to_markdown(page(text))


def test_annex_fragment_keeps_its_namespace():
    markdown = _html_to_markdown(page(annex(2)), anchor='Bijlage2')
    assert '[^annex-2-5]' in markdown
