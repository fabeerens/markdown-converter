# Wijzigingen — markdown converter

Nieuwste bovenaan. De inhoudelijke uitleg staat in `CLAUDE.md`; hier alleen wat er
veranderde en waarom.

## 25 september 2026 — HUDOC uit een lokale map (`kb_fetch --hudoc-map`)

HUDOC houdt de Python-client tegen met een Cloudflare-botcontrole (T2-F5 in
`~/Documents/kb/foutlog/Fouten_test_25_09.md`); die wordt niet omzeild. Zelf in de browser
gedownloade `<itemid>.docx` plus `hudoc-records.json` gaan nu door dezelfde omzetting als
online (`hudoc.omzetten_record()`) naar een gewone kennisbankbundel, met de herkomst
"handmatig gedownload" in het zijbestand. De botcontrole heeft een eigen melding in plaats
van "probeer het over een minuut opnieuw".
