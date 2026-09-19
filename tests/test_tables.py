"""Legal table relationships survive both shared HTML render routes."""
import pytest
from bs4 import BeautifulSoup

from mdconv.errors import ConversionError
from mdconv.render import html_to_markdown, container_to_markdown
from mdconv.sources.formex import convert_formex


@pytest.mark.parametrize('render', [html_to_markdown, lambda h: container_to_markdown(BeautifulSoup(h, 'lxml'))])
def test_rowspan_repeats_parent_values(render):
    source = '''<table><tr><th>Autoriteit</th><th>Sector</th><th>Subsector</th></tr>
    <tr><td rowspan="2">Minister</td><td rowspan="2">overheid</td><td>centraal</td></tr>
    <tr><td>decentraal</td></tr></table>'''
    output = render(source)
    assert '| Minister | overheid | decentraal |' in output
    assert '| decentraal |' not in output.splitlines()


def test_dga_blank_span_does_not_inherit_previous_event():
    source = '''<table><tr><td>Gebeurtenis</td><td>Procedure</td><td>Resultaat</td></tr>
    <tr><td>Bedrijf</td><td>Kennisgeving</td><td>Bevestiging</td></tr>
    <tr><td rowspan="2"></td><td>Aanmelding</td><td>Registratie</td></tr>
    <tr><td>Betaling</td><td>Kwitantie</td></tr></table>'''
    output = html_to_markdown(source)
    assert '|  | Betaling | Kwitantie |' in output
    assert '| Bedrijf | Betaling' not in output


def test_nested_marker_tables_do_not_add_outer_rows():
    source = '''<table><tr><th>Sector</th><th>Entiteit</th></tr>
    <tr><td rowspan="2">Energie</td><td><table><tr><td>–</td><td>Eerste</td></tr></table></td></tr>
    <tr><td><table><tr><td>–</td><td>Tweede</td></tr></table></td></tr></table>'''
    output = html_to_markdown(source)
    assert '| Energie | - Eerste |' in output
    assert '| Energie | - Tweede |' in output
    assert len([line for line in output.splitlines() if line.startswith('|')]) == 4


def test_colspan_and_zero_rowspan():
    source = '''<table><tbody><tr><td rowspan="0">A</td><td colspan="2">B</td></tr>
    <tr><td>C</td><td>D</td></tr></tbody></table>'''
    output = html_to_markdown(source)
    assert '| A | B | B |' in output
    assert '| A | C | D |' in output


@pytest.mark.parametrize('source', [
    '<table><tr><td rowspan="9">A</td></tr></table>',
    '<table><tr><td rowspan="bad">A</td></tr></table>',
    '<table><tbody><tr><td rowspan="2">A</td></tr></tbody><tbody><tr></tr></tbody></table>',
    '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr></tr></table>',
])
def test_unproven_spans_fail_closed(source):
    with pytest.raises(ConversionError):
        html_to_markdown(source)


@pytest.mark.parametrize('body', [
    '<ROW><CELL ROWSPAN="2">A</CELL><CELL>B</CELL></ROW><ROW><CELL>C</CELL></ROW>',
    '<ROW><CELL>A</CELL><CELL>B</CELL></ROW><ROW><CELL>C</CELL></ROW>',
    '<ROW><CELL><TBL><ROW><CELL>A</CELL></ROW></TBL></CELL></ROW>',
])
def test_formex_does_not_right_pad_missing_or_nested_cells(body):
    with pytest.raises(ConversionError):
        convert_formex(('<ACT><ENACTING.TERMS><TBL>' + body + '</TBL></ENACTING.TERMS></ACT>').encode())


def test_multiple_table_header_layers_require_review():
    source = '<table><thead><tr><th colspan="2">Group</th></tr><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>x</td><td>y</td></tr></tbody></table>'
    with pytest.raises(ConversionError):
        html_to_markdown(source)


def test_genuine_nested_tables_require_review():
    source = '<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td><table><tr><th>C</th><th>D</th></tr><tr><td>y</td><td>z</td></tr></table></td></tr></table>'
    with pytest.raises(ConversionError):
        html_to_markdown(source)
