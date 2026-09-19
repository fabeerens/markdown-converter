import io
import json
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import zipfile

import pytest
from mdconv import create_app
from mdconv.herkomst import Herkomst, als_zijbestand
from mdconv.source_structure import (capture_source_documents, record_html,
                                     bind_structure, sha256)
from mdconv.sources import from_link
from mdconv.sources import eurlex, wetten


def test_capture_is_isolated_between_concurrent_conversions():
    barrier = threading.Barrier(2)
    def run(name):
        with capture_source_documents() as documents:
            barrier.wait()
            record_html('<p>' + name + '</p>', source_url=name)
            barrier.wait()
            return documents
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run, ['alpha', 'beta']))
    assert [part['source_url'] for part in a] == ['alpha']
    assert [part['source_url'] for part in b] == ['beta']


def test_nested_capture_restores_outer_after_failure():
    with capture_source_documents() as outer:
        record_html('<p>A</p>', source_url='a')
        with pytest.raises(RuntimeError):
            with capture_source_documents():
                record_html('<p>B</p>', source_url='b')
                raise RuntimeError
        record_html('<p>C</p>', source_url='c')
    assert [part['source_url'] for part in outer] == ['a', 'c']


def test_sidecar_retains_source_and_binds_download_without_mutating_input():
    with capture_source_documents() as documents:
        record_html('<table><tr><td rowspan="2">A</td></tr><tr></tr></table>', source_url='https://example.test/source')
    provenance = bind_structure(Herkomst(format='html', extra={'existing': 42}), 'Original', documents).as_json()
    saved = json.loads(als_zijbestand(provenance, bewerkt_met_ai=True, markdown='Changed'))
    proof = saved['extra']['source_structure']
    assert saved['extra']['existing'] == 42
    assert saved['bewerkt_met_ai'] is True
    assert proof['sources'][0]['source_sha256'] == sha256(proof['sources'][0]['original_html'])
    assert proof['sources'][0]['tables'][0]['cells'][0]['rowspan'] == '2'
    assert proof['markdown_changed'] is True
    assert 'markdown_changed' not in provenance['extra']['source_structure']
    assert als_zijbestand({}, bewerkt_met_ai=False) is None


def test_browser_download_stays_plain_markdown_despite_provenance():
    """Het zijbestand is voor de kennisbank; de UI levert een los `.md` zoals altijd."""
    client = create_app().test_client()
    provenance = Herkomst(format='html', extra={'example': 1}).as_json()
    entry = {'markdown': '# Title', 'filename': 'test', 'provenance': provenance}
    result = client.post('/api/download', json=entry)
    assert result.mimetype == 'text/markdown'
    assert result.data.decode() == '# Title'
    result = client.post('/api/download', json={'documents': [entry]})
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.namelist() == ['test.md']


def test_eurlex_api_preserves_original_html_before_preprocessing(monkeypatch):
    html = '<html><body><h1>Regulation</h1><p>' + 'Legal text. ' * 30 + '</p></body></html>'
    response = SimpleNamespace(status_code=200, text=html, url='https://example.test/final')
    monkeypatch.setattr(eurlex.net, 'documents', lambda: SimpleNamespace(get=lambda *a, **k: response))
    monkeypatch.setattr(eurlex.net, 'decoded_text', lambda r: r.text)
    doc = from_link('32022R0868')
    proof = doc.provenance.extra['source_structure']
    assert proof['sources'][0]['original_html'] == html
    assert proof['sources'][0]['source_url'] == response.url
    assert proof['markdown_sha256'] == sha256(doc.markdown)


def test_wetten_fetch_provenance_preserves_source_before_spans_are_expanded(monkeypatch):
    html = '<div id="regeling"><h1>Wet</h1><div class="wetgeving"><h2>Artikel 1</h2><p>' + 'Juridische tekst. ' * 30 + '</p><table><tr><th>A</th><th>B</th></tr><tr><td rowspan="2">C</td><td>D</td></tr><tr><td>E</td></tr></table></div></div>'
    response = SimpleNamespace(status_code=200, text=html, url='https://wetten.overheid.nl/BWBR0040940/2026-09-01')
    monkeypatch.setattr(wetten.net, 'documents', lambda: SimpleNamespace(get=lambda *a, **k: response))
    monkeypatch.setattr(wetten.net, 'decoded_text', lambda r: r.text)
    markdown, _, provenance = wetten.fetch('BWBR0040940')
    proof = provenance.extra['source_structure']
    assert proof['sources'][0]['original_html'] == html
    assert proof['sources'][0]['tables'][0]['cells'][2]['rowspan'] == '2'
    assert '| C | E |' in markdown
