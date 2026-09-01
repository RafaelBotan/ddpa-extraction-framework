"""Document profile detector (canon DDPA v5.3).

Detecta flags internas do Context Builder a partir do laudo bruto ou dos
campos estruturados da megabase (material, tecnica, macroscopia, microscopia,
conclusao).

- ``DocumentComplexity``: volume e multiplicidade de entidades.
- ``DocumentFormatProfile``: forma textual (CAP-style, narrativa, lista, etc.).

Uso:
    >>> from runtime.document_profile import profile_document
    >>> p = profile_document(texto_completo=texto)
    >>> p.complexity.long_multi_entity
    True
    >>> p.format.cap_style
    False

Canon v5.3 — defaults provisórios calibráveis, não lei:
- long_report: chars > 5000  (ajustável)
- long_multi_entity: entity_count >= 10 OR specimen_count >= 10
- cap_style: >= 3 headers all-caps seguidos por ":" + header_value_blocks True
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

LONG_REPORT_CHAR_THRESHOLD = 5000
LONG_MULTI_ENTITY_COUNT_THRESHOLD = 10

_ENTITY_PATTERNS = [
    re.compile(r"\b[Pp][eé][çc]a\s*\d+", re.UNICODE),
    re.compile(r"\bPE[CÇ]A\s*\d+", re.UNICODE),
    re.compile(r"\b[Ff]ragmento\s*\d+", re.UNICODE),
    re.compile(r"\b[Aa]mostra\s*\d+", re.UNICODE),
    re.compile(r"\b[Ee]sp[eé]cime\s*\d+", re.UNICODE),
    re.compile(r"\b[Ll][aâ]mina\s*\d+", re.UNICODE),
    re.compile(r"\b[Rr]ecipiente\s*\d+", re.UNICODE),
    re.compile(r"(?:^|\n)\s*[IVX]{1,4}\s*[-–:)]", re.UNICODE),
]
_BLOCO_PATTERN = re.compile(r"\b[Bb]loco\s*\d+", re.UNICODE)
_ALLCAPS_HEADER = re.compile(
    r"(?:^|\n)\s*([A-ZÇÁÉÍÓÚÂÊÔÃÕÀ][A-ZÇÁÉÍÓÚÂÊÔÃÕÀ\s/]{3,40})\s*:(?:\s*$|\s*\n)",
    re.MULTILINE | re.UNICODE,
)
_BULLET_DASH = re.compile(r"(?:^|\n)\s*[-–—]\s+\S", re.UNICODE)
_BULLET_STAR = re.compile(r"(?:^|\n)\s*[*•]\s+\S", re.UNICODE)
_NUMBERED_ITEM = re.compile(r"(?:^|\n)\s*\d+\.\s+[A-Za-zÀ-ÿ]", re.UNICODE)
_PIPE_LINE = re.compile(r"^[^\n|]*\|[^\n|]*\|?[^\n]*$", re.MULTILINE)
_SENTENCE_PARAGRAPH = re.compile(r"[^\n]{150,}", re.UNICODE)

STRUCTURED_SECTIONS = (
    "material_especificado",
    "tecnica",
    "macroscopia",
    "microscopia",
    "conclusao",
)


@dataclass
class DocumentComplexity:
    """Signals de volume e multiplicidade."""

    char_count: int
    long_report: bool
    estimated_entity_count: int
    specimen_count: int
    section_count: int
    long_multi_entity: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentFormatProfile:
    """Signals de forma textual (orienta parser)."""

    cap_style: bool
    narrative_style: bool
    table_like: bool
    header_value_blocks: bool
    bullet_list: bool
    unknown: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentProfile:
    """Bundle canon v5.3."""

    complexity: DocumentComplexity
    format: DocumentFormatProfile

    def to_dict(self) -> dict[str, Any]:
        return {"complexity": self.complexity.to_dict(), "format": self.format.to_dict()}


def _count_entities(text: str) -> int:
    """Soma ocorrências de patterns que indicam peças/fragmentos enumerados."""

    total = 0
    for pat in _ENTITY_PATTERNS:
        total += len(pat.findall(text))
    return total


def _count_specimens(text: str) -> int:
    """Específico: enumeração de blocos/frascos que costumam mapear 1 espécime cada."""

    return len(_BLOCO_PATTERN.findall(text))


def _count_sections(structured: dict[str, str]) -> int:
    """Seções estruturadas não-vazias presentes na megabase."""

    return sum(1 for k in STRUCTURED_SECTIONS if structured.get(k, "").strip())


def _detect_cap_style(text: str, headers: list[str]) -> bool:
    """CAP-style = >=3 headers all-caps + evidência de blocos header:valor."""

    return len(headers) >= 3 and bool(
        re.search(
            r"[A-ZÇÁÉÍÓÚÂÊÔÃÕÀ][A-ZÇÁÉÍÓÚÂÊÔÃÕÀ\s/]{3,40}\s*:\s*\n\s*[-–—*•]",
            text,
            re.UNICODE,
        )
    )


def _detect_table_like(text: str) -> bool:
    """Linhas pipe-separated em volume (>=3)."""

    return len(_PIPE_LINE.findall(text)) >= 3


def _detect_header_value_blocks(text: str) -> bool:
    """HEADER:\\n- valor1\\n- valor2 (alinhado a CAP-style)."""

    return bool(
        re.search(
            r"[A-ZÇÁÉÍÓÚÂÊÔÃÕÀ][A-ZÇÁÉÍÓÚÂÊÔÃÕÀ\s/]{3,40}\s*:\s*\n\s*[-–—*•]",
            text,
            re.UNICODE,
        )
    )


def _detect_bullet_list(text: str, n_lines: int) -> bool:
    """Bullets representam fração relevante das linhas (>=5% ou >=5 bullets)."""

    bullets = len(_BULLET_DASH.findall(text)) + len(_BULLET_STAR.findall(text))
    if bullets >= 5:
        return True
    return n_lines > 0 and bullets / max(n_lines, 1) >= 0.05


def _detect_narrative(
    text: str, *, cap_style: bool, bullet_list: bool, table_like: bool
) -> bool:
    """Narrativa = não estruturado + parágrafos longos OU prosa sem marcadores."""

    if cap_style or table_like or bullet_list:
        return False
    if _SENTENCE_PARAGRAPH.search(text):
        return True
    stripped = text.strip()
    if len(stripped) < 80:
        return False
    tokens = stripped.split()
    return len(tokens) >= 15


def profile_document(
    texto_completo: str | None = None,
    *,
    material_especificado: str = "",
    tecnica: str = "",
    macroscopia: str = "",
    microscopia: str = "",
    conclusao: str = "",
    long_report_threshold: int = LONG_REPORT_CHAR_THRESHOLD,
    long_multi_entity_threshold: int = LONG_MULTI_ENTITY_COUNT_THRESHOLD,
) -> DocumentProfile:
    """Aplica heurísticas canon v5.3 e retorna DocumentProfile.

    Se ``texto_completo`` não for dado, é reconstruído a partir dos campos estruturados.
    """

    structured = {
        "material_especificado": material_especificado or "",
        "tecnica": tecnica or "",
        "macroscopia": macroscopia or "",
        "microscopia": microscopia or "",
        "conclusao": conclusao or "",
    }
    if texto_completo is None or not texto_completo.strip():
        texto_completo = "\n\n".join(
            f"[{k.upper()}]\n{v}" for k, v in structured.items() if v.strip()
        )

    text = texto_completo or ""
    char_count = len(text)
    n_lines = text.count("\n") + 1

    entity_count = _count_entities(text)
    specimen_count = _count_specimens(text)
    section_count = _count_sections(structured)

    long_report = char_count > long_report_threshold
    long_multi_entity = (
        entity_count >= long_multi_entity_threshold
        or specimen_count >= long_multi_entity_threshold
    )

    headers = _ALLCAPS_HEADER.findall(text)
    cap_style = _detect_cap_style(text, headers)
    header_value_blocks = _detect_header_value_blocks(text)
    table_like = _detect_table_like(text)
    bullet_list = _detect_bullet_list(text, n_lines)
    narrative_style = _detect_narrative(
        text, cap_style=cap_style, bullet_list=bullet_list, table_like=table_like
    )
    unknown = not any(
        (cap_style, narrative_style, table_like, header_value_blocks, bullet_list)
    )

    complexity = DocumentComplexity(
        char_count=char_count,
        long_report=long_report,
        estimated_entity_count=entity_count,
        specimen_count=specimen_count,
        section_count=section_count,
        long_multi_entity=long_multi_entity,
    )
    fmt = DocumentFormatProfile(
        cap_style=cap_style,
        narrative_style=narrative_style,
        table_like=table_like,
        header_value_blocks=header_value_blocks,
        bullet_list=bullet_list,
        unknown=unknown,
    )
    return DocumentProfile(complexity=complexity, format=fmt)
