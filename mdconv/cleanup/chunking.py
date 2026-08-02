"""Lange documenten in delen splitsen voor de AI-opschoning.

De splitsing zakt door een ladder van scheidingstekens: eerst witregels
(alinea's), dan losse regeleindes, dan spaties, en als laatste redmiddel een
harde knip midden in een "woord". Die laatste trap is er niet voor de sier: een
PDF- of Word-conversie kan één doorlopend blok zonder enkele witregel zijn, en
een splitsing die alleen op alinea's let gaf dat dan ongesplitst door — met een
verzoek dat het contextvenster overschreed.
"""

from __future__ import annotations

from . import config


def pack(pieces: list[str], sep: str, limit: int) -> list[str]:
    """Vul stukken (verbonden met `sep`) hebzuchtig tot `limit` tekens.

    Een stuk dat zelf al langer is dan `limit` gaat als eigen (te groot) deel
    door; de aanroeper splitst dat verder.
    """
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = f"{buf}{sep}{piece}" if buf else piece
        if buf and len(candidate) > limit:
            chunks.append(buf)
            buf = piece
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def split(text: str, limit: int | None = None) -> list[str]:
    """Splits tekst in delen van maximaal `limit` tekens.

    `limit` volgt standaard de ingestelde deelgrootte, zodat een wijziging in
    het instellingenpaneel direct meedoet.
    """
    if limit is None:
        limit = config.get_chunk_chars()

    chunks: list[str] = []
    for block in pack(text.split("\n\n"), "\n\n", limit) or [text]:
        if len(block) <= limit:
            chunks.append(block)
            continue
        # Deze "alinea" is zelf te groot: verder splitsen op regeleindes.
        for piece in pack(block.split("\n"), "\n", limit):
            if len(piece) <= limit:
                chunks.append(piece)
                continue
            for word_chunk in pack(piece.split(" "), " ", limit):
                if len(word_chunk) <= limit:
                    chunks.append(word_chunk)
                else:
                    # Eén "woord" langer dan de limiet (tekst zonder spaties):
                    # harde knip als laatste redmiddel.
                    chunks.extend(
                        word_chunk[i:i + limit] for i in range(0, len(word_chunk), limit)
                    )
    return chunks or [text]


def chunks_for(text: str, profile: str) -> list[str]:
    """De delen waarin dit profiel het document verwerkt.

    Het obsidian-profiel draait altijd ongesplitst: dat levert één notitie op en
    frontmatter/analyse mag maar één keer voorkomen.
    """
    text = text.strip()
    if not text:
        return []
    if profile in config.NO_CHUNK_PROFILES:
        return [text]
    return split(text)
