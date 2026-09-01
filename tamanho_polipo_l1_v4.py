"""
tamanho_polipo_l1_v4 — detector L1 v4-B para `polipo_tamanho_max_mm` (MEAS-N).

Consome framework_core. Section priority: descricao > conclusao > corpo.
NUNCA escaneia [INDICACAO], [SEDACAO], [PREPARO], [BIOPSIA].

Algoritmo (ancoragem por substantivo mais próximo):

1. Para cada bloco de findings, encontrar todas as MEDIDAS (\\d+(?:[.,]\\d+)?\\s*(mm|cm)).
2. Para cada medida, achar o SUBJECT mais próximo (último substantivo médico
   à esquerda em ≤200 chars; se nada, primeiro à direita em ≤100 chars).
3. Se SUBJECT ∈ POLIPO_SUBJECTS → aceitar como tamanho de pólipo.
   Se SUBJECT ∈ ANTI_SUBJECTS (úlcera, lipoma, lesão neoplásica, anastomose,
   flebectasia, hemorroida, dilatação) → descartar.
4. Anti-pattern textual de distância: número seguido de "da borda anal",
   "do canal anal", "da linha pectínea" → descartar (é localização, não diâmetro).
5. Anti-pattern: número precedido por "Boston", "Mayo", "Paris", "NICE",
   "BBPS" → descartar (score).
6. Intervalo "X a Y", "X-Y" — pegar Y (upper bound).
7. Coletar todas as medidas válidas → MAX.
8. Se nenhuma medida válida MAS há substantivo polipo + qualitativo
   ("diminuto", "puntiforme", "subcentimétrico") na mesma sentença →
   status='qualitative_*', value=None.
9. Se há polipo mas sem nenhuma medida nem qualitativo → 'polipo_no_size'.
10. Se nem polipo encontrado → 'no_polipo'.

Output: TamanhoResult(value, status, section, span, evidence, n_measurements, all_values)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from framework_core import (
    normalize, segment, ENDOSCOPY_SECTIONS,
    evidence_around,
)


DETECTOR_VERSION = 'tamanho_polipo_l1_v4@1.1.0'

_PATTERN_FAMILIES = (
    'measures_explicit',
    'measures_interval',
    'subjects_polipo',
    'subjects_anti',
    'qualitative_size',
)


def _build_scope_meta(
    sections_scanned, chars_scanned, pattern_families_run,
    hits_total, explicit_negative_hits=0, historical_hits=0,
    scope_complete=None,
):
    if scope_complete is None:
        scope_complete = chars_scanned > 0 and len(sections_scanned) > 0
    return {
        'detector_version': DETECTOR_VERSION,
        'scope_complete': bool(scope_complete),
        'sections_scanned': list(sections_scanned),
        'chars_scanned': int(chars_scanned),
        'pattern_families_run': list(pattern_families_run),
        'hits_total': int(hits_total),
        'explicit_negative_hits': int(explicit_negative_hits),
        'historical_hits': int(historical_hits),
    }


SECTION_PRIORITY = ['descricao', 'conclusao', 'estomago', 'corpo', 'inicio']
SECTIONS_NEVER = {'indicacao', 'sedacao', 'preparo', 'biopsia'}


# ----------------------------------------------------------------------------
# Padrões de medida
# ----------------------------------------------------------------------------

# Número com unidade (mm | cm). Aceita decimal vírgula ou ponto, zero-padding.
PAT_MEASURE = re.compile(
    r'(?<![\w.,])'                    # não pode ser parte de número maior
    r'(\d{1,3}(?:[.,]\d{1,2})?)'      # número (1-3 dígitos, decimal opcional)
    r'\s*'
    r'(mm|cm)'
    r'\b'
)

# Intervalo "X a Y mm" / "X-Y mm" / "X-Ymm" / "entre X e Y mm"
PAT_INTERVAL = re.compile(
    r'(?<![\w.,])(\d{1,3}(?:[.,]\d{1,2})?)'
    r'\s*(?:a|-|ate|e)\s*'
    r'(\d{1,3}(?:[.,]\d{1,2})?)'
    r'\s*(mm|cm)'
    r'\b'
)


# ----------------------------------------------------------------------------
# Subjects
# ----------------------------------------------------------------------------

# Substantivos contados como pólipo (CONTA o número associado)
PAT_POLIPO_SUBJECT = re.compile(
    r'\bpolip(?:o|os|oide|oides)\b'
    r'|\bformac(?:ao|oes)\s+polipoides?\b'
    r'|\bformac(?:ao|oes)\s+polipoide\b'
    r'|\bles(?:ao|oes)\s+polipoides?\b'
    r'|\bles(?:ao|oes)\s+plana\s+elevada\b'
    r'|\bles(?:ao|oes)\s+plano[-\s]?elevad[ao]s?\b'
    r'|\bles(?:ao|oes)\s+superficial\s+elevada\b'
    r'|\blsts?[-\s]?(?:g|ng|granular)?\b'
    r'|\blateral\s+spreading\s+(?:tumor|lesion)\b'
    r'|\bcrescimento\s+lateral\b'
    r'|\bpseudopolip(?:o|os)\b'
    r'|\badenom(?:a|as)\s+(?:plan|pedicul|sessil)'
    r'|\bporcao\s+cefalica\s+medindo\b'   # pólipo pediculado
    r'|\b(?:o\s+)?maior\s+eixo\s+de\b'    # marcador de eixo
    r'|\bles(?:ao|oes)\s+(?:planas?\s+)?esprairad'  # flat spreading / LST
)

# Substantivos NÃO contados (anti-patterns) — descartar números próximos
PAT_ANTI_SUBJECT = re.compile(
    r'\b(?:ulcer(?:a|as|ada|adas|ado))\b'
    r'|\b(?:lesao|lesoes)\s+(?:neoplasic|tumora|infiltrativ|estenosant|circunferencia|ulcerad)'
    r'|\bneoplasi(?:a|as)\s+(?:de|do|da|avancada)'
    r'|\btumor(?:al|ais|es)?\b'
    r'|\blipoma(?:s|tos[ao])?\b'
    r'|\bles(?:ao|oes)\s+subepitelia(?:l|is)\b'
    r'|\baspecto\s+subepitelia(?:l|is)\b'
    r'|\bflebectasi(?:a|as)\b'
    r'|\bectasi(?:a|as)\s+vascular(?:es)?\b'
    r'|\bhemorroid(?:a|as|aria|arios)\b'
    r'|\bmamilo\s+hemorroidari'
    r'|\banastomos(?:e|es)\b'
    r'|\bdilatac(?:ao|oes)\s+balonad'
    r'|\bextensao\s+de\b'              # "extensão de 5cm" (lesão estenosante)
    r'|\bdiverticul(?:o|os|ar|ares)\b'
    r'|\bostios?\s+diverticulares?\b'
    r'|\bgranuloma(?:s)?\b'
)

# Palavras de score/medicação que sinalizam que o número adjacente NÃO é
# tamanho. Removidas: foto/figura/ha N anos — não vêm com mm/cm e estavam
# causando falso descarte de medidas legítimas próximas a "(foto 02)".
PAT_NOISE_NEAR_NUM = re.compile(
    r'\b(?:boston|mayo|nice|paris|bbps|rutgeerts|bormann|forrest|sakita)\b'
    r'|\bescala\s+de\s+boston\b'
    r'|\baparelho\s+olympus\b'
    r'|\bdolantina\b|\bdormonid\b|\bpicoprep\b|\bmanitol\b|\blactulose\b'
    r'|\bduco?lax\b|\bfentanil\b|\bpropofol\b'
)

# Bound comparativo: "menores que X mm", "inferior a X mm", "< X mm", etc.
# Se o número é precedido por estas construções, NÃO é medida pontual — é limite.
PAT_COMPARATIVE_BOUND = re.compile(
    r'(?:\b(?:menor|menores|inferior|inferiores)\s+(?:que|do\s+que|de|a)'
    r'|\b(?:menos|abaixo)\s+(?:de|do)'
    r'|<)'
    r'\s*$'
)

# Multi-dimensional: "2,5 X 1,0 X 0,5 cm" → max dimension
PAT_MULTI_DIM = re.compile(
    r'(\d{1,3}(?:[.,]\d{1,2})?)'
    r'\s*[xX\u00d7]\s*'
    r'(\d{1,3}(?:[.,]\d{1,2})?)'
    r'(?:\s*[xX\u00d7]\s*(\d{1,3}(?:[.,]\d{1,2})?))?'
    r'\s*(mm|cm)\b'
)

# Anti-pattern textual de DISTÂNCIA: número seguido de "da borda anal" etc
PAT_DISTANCE = re.compile(
    r'\bd[oa]\s+(?:borda\s+anal|anus|canal\s+anal|linha\s+pectinea|reto|ceco|valvula|ileo)'
    r'|\b(?:proximal|distal|distais?)\s+(?:avaliad)'
)

# Qualitativos sem número
PAT_QUALITATIVE = re.compile(
    r'\bdiminut(?:o|os|a|as)\b'
    r'|\bp(?:un|on)tiform(?:e|es)\b'
    r'|\bsubcentimetric(?:o|os|a|as)\b'
    r'|\bvolumos(?:o|os|a|as)\b'
    r'|\bgrand(?:e|es)\b(?!\s+(?:quantidade|parte|volume|numero|extensao|acumulo|presenca))'
)

# Qualitativos -> rótulo
QUALITATIVE_LABELS = [
    (re.compile(r'\bdiminut(?:o|os|a|as)\b'), 'qualitative_diminuto'),
    (re.compile(r'\bp(?:un|on)tiform'), 'qualitative_punctiform'),
    (re.compile(r'\bsubcentimetric'), 'qualitative_subcm'),
    (re.compile(r'\bvolumos'), 'qualitative_large'),
    (re.compile(r'\bgrand(?:e|es)\b(?!\s+(?:quantidade|parte|volume|numero|extensao|acumulo|presenca))'), 'qualitative_large'),
]


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

@dataclass
class TamanhoResult:
    value: Optional[float]            # mm (float) ou None
    status: str                       # measure_explicit, measure_interval, measure_multi_max, qualitative_*, polipo_no_size, no_polipo
    section: Optional[str] = None
    span_start: int = -1
    span_end: int = -1
    evidence: str = ''
    certainty: str = 'high'
    all_measurements: list = field(default_factory=list)   # lista de mm válidos
    meta: dict = field(default_factory=dict)
    scope_meta: Optional[dict] = None
    evidence_class: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            'value': self.value,
            'status': self.status,
            'section': self.section,
            'span_start': self.span_start,
            'span_end': self.span_end,
            'evidence': self.evidence[:200],
            'certainty': self.certainty,
            'n_measurements': len(self.all_measurements),
            'all_values_mm': ','.join(f'{m:.1f}' for m in self.all_measurements),
            **{f'meta_{k}': v for k, v in self.meta.items()},
        }


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _to_mm(num_text: str, unit: str) -> float:
    """Converte (numero, unidade) → mm float."""
    num = float(num_text.replace(',', '.'))
    if unit == 'cm':
        return num * 10.0
    return num


def _nearest_subject(content: str, num_start: int, num_end: int) -> tuple[Optional[str], int]:
    """Procura o substantivo médico mais próximo do número.

    Estratégia:
    1. À esquerda em ≤200 chars: encontrar TODOS os matches de POLIPO_SUBJECT
       e ANTI_SUBJECT, pegar o mais próximo (maior posição < num_start).
    2. Se não houver à esquerda, procurar à direita em ≤100 chars.

    Retorna ('polipo' | 'anti' | None, posição_substantivo).
    """
    left_a = max(0, num_start - 200)
    left_text = content[left_a:num_start]

    candidates = []  # (relative_pos, kind)
    for m in PAT_POLIPO_SUBJECT.finditer(left_text):
        candidates.append((m.start(), 'polipo', left_a + m.start()))
    for m in PAT_ANTI_SUBJECT.finditer(left_text):
        candidates.append((m.start(), 'anti', left_a + m.start()))

    if candidates:
        # mais próximo = maior posição (mais à direita = mais perto do número)
        candidates.sort(key=lambda c: -c[0])
        _, kind, abs_pos = candidates[0]
        return kind, abs_pos

    # tentar à direita
    right_b = min(len(content), num_end + 100)
    right_text = content[num_end:right_b]
    for m in PAT_POLIPO_SUBJECT.finditer(right_text):
        return 'polipo', num_end + m.start()
    for m in PAT_ANTI_SUBJECT.finditer(right_text):
        return 'anti', num_end + m.start()

    return None, -1


def _is_distance(content: str, num_start: int, num_end: int) -> bool:
    """Detecta padrão de distância (não diâmetro)."""
    # Janela de até 30 chars à direita do número
    window_right = content[num_end:min(len(content), num_end + 30)]
    if PAT_DISTANCE.search(window_right):
        return True
    return False


def _is_comparative_bound(content: str, num_start: int) -> bool:
    """Detecta se o número é precedido por comparativo ("menores que", "< ", etc).
    Nesses casos o número é um LIMITE, não medida pontual."""
    a = max(0, num_start - 30)
    return bool(PAT_COMPARATIVE_BOUND.search(content[a:num_start]))


def _is_noise_score(content: str, num_start: int, num_end: int) -> bool:
    """Detecta se o número é parte de score (Boston, Mayo, NICE, etc) ou medicação.

    Janela só À ESQUERDA (≤15 chars). Em PT-BR clínico o padrão é sempre
    "Boston 6", "BBPS 9", "NICE 2", "Mayo III" — a palavra do score precede
    o número. Se olharmos à direita, "08 mm, nice 2" descartaria erradamente
    a medida real do pólipo.
    """
    a = max(0, num_start - 15)
    return bool(PAT_NOISE_NEAR_NUM.search(content[a:num_start]))


def _scan_intervals(content: str) -> list[tuple[int, int, float]]:
    """Encontra intervalos 'X a Y mm' / 'X-Y mm' e retorna max(X, Y).
    Handles inverted intervals like '15-10 mm' and comma-lists like '3, 6, 20 e 3 mm'."""
    out = []
    for m in PAT_INTERVAL.finditer(content):
        x_text, y_text, unit = m.group(1), m.group(2), m.group(3)
        try:
            x_mm = _to_mm(x_text, unit)
            y_mm = _to_mm(y_text, unit)
        except ValueError:
            continue
        best = max(x_mm, y_mm)
        # Look back for more comma-separated numbers sharing the unit
        prefix = content[max(0, m.start() - 80):m.start()]
        for nm in re.finditer(r'(\d{1,3}(?:[.,]\d{1,2})?)\s*,', prefix):
            try:
                val = _to_mm(nm.group(1), unit)
                if 0.5 <= val <= 200:
                    best = max(best, val)
            except ValueError:
                pass
        out.append((m.start(), m.end(), best))
    return out


def _scan_multidim(content: str) -> list[tuple[int, int, float]]:
    """Find multi-dimensional measurements like '2,5 X 1,0 X 0,5 cm' → max dimension."""
    out = []
    for m in PAT_MULTI_DIM.finditer(content):
        unit = m.group(4)
        dims = []
        for g in [m.group(1), m.group(2), m.group(3)]:
            if g:
                try:
                    dims.append(_to_mm(g, unit))
                except ValueError:
                    pass
        if dims:
            out.append((m.start(), m.end(), max(dims)))
    return out


def _scan_block(content: str, base_offset: int):
    """Escaneia um bloco de texto. Retorna lista de medidas válidas e info adicional."""
    valid_measurements = []   # (mm, span_start, span_end, evidence, source_type)

    # 0. Multi-dimensional (têm prioridade máxima — "2,5 X 1,0 X 0,5 cm")
    multidim_spans = []
    for s, e, max_dim in _scan_multidim(content):
        if _is_noise_score(content, s, e):
            continue
        if _is_distance(content, s, e):
            continue
        kind, _ = _nearest_subject(content, s, e)
        if kind == 'polipo':
            ev = evidence_around(content, s, e, window=70)
            valid_measurements.append((max_dim, base_offset + s, base_offset + e, ev, 'explicit'))
            multidim_spans.append((s, e))

    # 1. Intervalos (têm prioridade — capturar Y, evita dupla contagem do X)
    interval_spans = []  # (start, end) já consumidos
    for s, e, y_mm in _scan_intervals(content):
        if any(s >= ma and e <= mb for ma, mb in multidim_spans):
            continue
        if _is_noise_score(content, s, e):
            continue
        if _is_distance(content, s, e):
            continue
        kind, _ = _nearest_subject(content, s, e)
        if kind == 'polipo':
            ev = evidence_around(content, s, e, window=70)
            valid_measurements.append((y_mm, base_offset + s, base_offset + e, ev, 'interval'))
            interval_spans.append((s, e))

    # 2. Medidas individuais — pular as que estão dentro de span já consumido
    consumed_spans = multidim_spans + interval_spans
    for m in PAT_MEASURE.finditer(content):
        s, e = m.start(), m.end()
        skip = False
        for ia, ib in consumed_spans:
            if s >= ia and e <= ib:
                skip = True
                break
        if skip:
            continue

        if _is_noise_score(content, s, e):
            continue
        if _is_distance(content, s, e):
            continue
        if _is_comparative_bound(content, s):
            continue

        kind, _ = _nearest_subject(content, s, e)
        if kind != 'polipo':
            continue

        try:
            mm = _to_mm(m.group(1), m.group(2))
        except ValueError:
            continue

        # Sanity: tamanho de pólipo plausível: 0.5mm a 200mm
        if mm < 0.5 or mm > 200:
            continue
        # 0 mm não existe
        if mm == 0:
            continue

        ev = evidence_around(content, s, e, window=70)
        valid_measurements.append((mm, base_offset + s, base_offset + e, ev, 'explicit'))

    return valid_measurements


def _has_polipo_subject(content: str) -> bool:
    return bool(PAT_POLIPO_SUBJECT.search(content))


PAT_COMPARATIVE_QUAL = re.compile(
    r'\b(?:menores?|inferiores?)\s+(?:que|do\s+que|de|a)\s*'
    r'(\d{1,2}(?:[.,]\d)?)\s*(mm|cm)'
)

def _qualitative_for_polipo(content: str) -> Optional[tuple[str, int, int, str]]:
    """Procura qualitativos próximos a um polipo-subject (até 60 chars)."""
    for ps in PAT_POLIPO_SUBJECT.finditer(content):
        a = max(0, ps.start() - 60)
        b = min(len(content), ps.end() + 60)
        window = content[a:b]
        for pat, label in QUALITATIVE_LABELS:
            qm = pat.search(window)
            if qm:
                ev = evidence_around(content, ps.start(), ps.end(), window=70)
                return (label, ps.start(), ps.end(), ev)
        # Bound comparativo: "menores que X mm" → qualitative label
        cm = PAT_COMPARATIVE_QUAL.search(window)
        if cm:
            bound_mm = _to_mm(cm.group(1), cm.group(2))
            if bound_mm <= 5:
                label = 'qualitative_diminuto'
            elif bound_mm <= 10:
                label = 'qualitative_subcm'
            else:
                label = 'qualitative_large'
            ev = evidence_around(content, ps.start(), ps.end(), window=70)
            return (label, ps.start(), ps.end(), ev)
    return None


# ----------------------------------------------------------------------------
# Detect
# ----------------------------------------------------------------------------

def detect_tamanho_polipo(text: str) -> TamanhoResult:
    if not isinstance(text, str) or not text.strip():
        return TamanhoResult(
            value=None, status='no_polipo',
            evidence_class='silence_default',
            scope_meta=_build_scope_meta(
                sections_scanned=[], chars_scanned=0,
                pattern_families_run=[], hits_total=0,
                scope_complete=False,
            ),
        )

    norm = normalize(text)
    seg = segment(norm, ENDOSCOPY_SECTIONS)

    # Em formatos como Clinic C/Clinic A/Clinic B, o cabeçalho [SEDACAO] /
    # [PREPARO] é seguido implicitamente pela descrição completa SEM um
    # [DESCRICAO] explícito — então o segment() põe tudo em sedacao/preparo.
    # Critério forte: só mascaramos sedacao/preparo quando há [DESCRICAO]
    # explícito (estrutura confiável). [CONCLUSAO] sozinho não basta porque
    # é frequentemente curto e não contém medidas — a descrição real fica
    # dentro do bloco sedacao greedy. Indicacao e biopsia sempre são
    # mascaradas (curtas e bem marcadas).
    has_descricao = bool(seg.get('descricao'))

    excluded_spans = []
    always_mask = {'indicacao', 'biopsia'}
    for sec_name in SECTIONS_NEVER:
        if sec_name in always_mask or has_descricao:
            for block in seg.get(sec_name):
                excluded_spans.append((block.start, block.start + len(block.content)))

    def _is_excluded(pos: int) -> bool:
        for a, b in excluded_spans:
            if a <= pos < b:
                return True
        return False

    # Mascarar texto: substituir conteúdo proibido por espaços (preserva offsets)
    masked = list(norm)
    for a, b in excluded_spans:
        for i in range(a, min(b, len(masked))):
            masked[i] = ' '
    masked_text = ''.join(masked)

    all_measurements = []
    polipo_seen_in = None
    qualitative_hit = None

    # Scan global no texto mascarado (offsets absolutos = base 0)
    measurements = _scan_block(masked_text, 0)
    for mm, s, e, ev, src in measurements:
        all_measurements.append((mm, s, e, ev, src, 'global'))

    if _has_polipo_subject(masked_text):
        polipo_seen_in = 'global'
        if not measurements:
            q = _qualitative_for_polipo(masked_text)
            if q:
                label, qs, qe, qev = q
                qualitative_hit = (label, qs, qe, qev, 'global')

    # Scope meta shared by all non-stub returns
    sections_scanned_list = [s for s in SECTION_PRIORITY if seg.get(s)]
    if not sections_scanned_list:
        sections_scanned_list = ['global']
    chars_scanned = len(masked_text)

    def _scope(families, hits, scope_complete=True):
        return _build_scope_meta(
            sections_scanned=sections_scanned_list,
            chars_scanned=chars_scanned,
            pattern_families_run=families,
            hits_total=hits, scope_complete=scope_complete,
        )

    if all_measurements:
        # MAX entre todas as medidas válidas
        best = max(all_measurements, key=lambda x: x[0])
        mm, s, e, ev, src, sec = best
        n = len(all_measurements)
        if n == 1 and src == 'explicit':
            status = 'measure_explicit'
            ec = 'literal'
        elif src == 'interval':
            status = 'measure_interval'
            ec = 'bound'
        else:
            status = 'measure_multi_max'
            ec = 'literal'
        return TamanhoResult(
            value=mm, status=status, section=sec,
            span_start=s, span_end=e, evidence=ev,
            certainty='high',
            all_measurements=sorted([m[0] for m in all_measurements]),
            meta={'n_hits': n},
            evidence_class=ec,
            scope_meta=_scope(['measures_explicit', 'measures_interval'], n),
        )

    if qualitative_hit:
        label, s, e, ev, sec = qualitative_hit
        return TamanhoResult(
            value=None, status=label, section=sec,
            span_start=s, span_end=e, evidence=ev,
            certainty='medium',
            evidence_class='qualitative',
            scope_meta=_scope(['measures_explicit', 'subjects_polipo', 'qualitative_size'], 1),
        )

    if polipo_seen_in:
        return TamanhoResult(
            value=None, status='polipo_no_size', section=polipo_seen_in,
            certainty='medium',
            evidence_class='silence_default',
            scope_meta=_scope(['measures_explicit', 'subjects_polipo'], 0),
        )

    return TamanhoResult(
        value=None, status='no_polipo',
        evidence_class='silence_default',
        scope_meta=_scope(['measures_explicit', 'subjects_polipo'], 0),
    )


# ----------------------------------------------------------------------------
# Smoke tests
# ----------------------------------------------------------------------------

if __name__ == '__main__':
    cases = [
        # 1. Caso canônico — pólipo séssil de 5 mm
        ('[DESCRICAO] presença de pólipo séssil em sigmóide de 5 mm [CONCLUSAO] PÓLIPO',
         5.0, 'measure_explicit'),
        # 2. Zero-padding "05 mm"
        ('[DESCRICAO] pólipo séssil em ceco com 05 mm de diâmetro [CONCLUSAO] PÓLIPO',
         5.0, 'measure_explicit'),
        # 3. Múltiplos pólipos — MAX
        ('[DESCRICAO] dois pólipos sésseis: um com 10 mm e outro com 5 mm [CONCLUSAO] PÓLIPOS',
         10.0, 'measure_multi_max'),
        # 4. Intervalo "X a Y mm"
        ('[DESCRICAO] pólipos sésseis de 03 a 15 mm de diâmetro [CONCLUSAO] PÓLIPOS',
         15.0, 'measure_interval'),
        # 5. Intervalo "X-Y mm"
        ('[DESCRICAO] lesão polipóide de 05-06mm diâmetro [CONCLUSAO] PÓLIPOS',
         6.0, 'measure_interval'),
        # 6. cm convertido para mm
        ('[DESCRICAO] pólipo pediculado em sigmoide de 1,5 cm [CONCLUSAO] PÓLIPO',
         15.0, 'measure_explicit'),
        # 7. Diminuto sem número
        ('[DESCRICAO] diminuto pólipo séssil em reto, polipectomia [CONCLUSAO] PÓLIPO',
         None, 'qualitative_diminuto'),
        # 8. Pólipo sem tamanho nem qualitativo
        ('[DESCRICAO] presença de pólipos em cólon ascendente [CONCLUSAO] PÓLIPOS',
         None, 'polipo_no_size'),
        # 9. Anti-pattern: lesão neoplásica de 5cm DESCARTADA, sobra pólipo de 5mm (single)
        ('[DESCRICAO] lesão ulcerada infiltrativa em transverso com extensão de 5 cm. '
         'Pólipo séssil medindo 5 mm em sigmoide [CONCLUSAO] LESÃO + PÓLIPO',
         5.0, 'measure_explicit'),
        # 10. Anti-pattern: lipoma 20mm DESCARTADO, sobra lesão polipóide de 6mm (single)
        ('[DESCRICAO] lipoma submucoso em transverso com 20mm. Lesão polipóide em reto com 06mm '
         '[CONCLUSAO] LIPOMA + PÓLIPO',
         6.0, 'measure_explicit'),
        # 11. Anti-pattern: úlcera de 10mm + sem pólipo
        ('[DESCRICAO] múltiplas úlceras rasas medindo até 10 mm. Múltiplos pólipos sésseis. '
         '[CONCLUSAO] PANCOLITE',
         None, 'polipo_no_size'),
        # 12. Distância: pólipo a 20cm da borda anal — sem tamanho
        ('[DESCRICAO] pólipo róseo e séssil a cerca de 20 cm da borda anal [CONCLUSAO] PÓLIPO',
         None, 'polipo_no_size'),
        # 13. Score Boston não confunde
        ('[DESCRICAO] BBPS 9 (3+3+3). Pólipo séssil de 7 mm [CONCLUSAO] PÓLIPO',
         7.0, 'measure_explicit'),
        # 14. Sem pólipo nenhum
        ('[DESCRICAO] mucosa normal [CONCLUSAO] EXAME NORMAL',
         None, 'no_polipo'),
        # 15. LST conta como pólipo
        ('[DESCRICAO] LST-G granular em ceco medindo 30 mm [CONCLUSAO] LST',
         30.0, 'measure_explicit'),
        # 16. Pseudopolipos contam como pólipos (DII)
        ('[DESCRICAO] pseudopolipos esparsos com tamanhos de 3 a 12 mm [CONCLUSAO] RCUI',
         12.0, 'measure_interval'),
        # 17. Anastomose 25mm não conta como pólipo
        ('[DESCRICAO] anastomose íleo-cólica íntegra com diâmetro de 25 mm. Pólipo de cólon '
         '[CONCLUSAO] ANASTOMOSE + PÓLIPO',
         None, 'polipo_no_size'),
        # 18. Dilatação balonada não conta
        ('[DESCRICAO] dilatação balonada para 15 mm [CONCLUSAO] ESTENOSE',
         None, 'no_polipo'),
        # 19. Indicação contém polipo previo, achados sem polipo
        ('[INDICACAO] Controle de polipectomia [DESCRICAO] mucosa normal [CONCLUSAO] EXAME NORMAL',
         None, 'no_polipo'),
        # 20. Múltiplos pólipos com 4 medidas distintas
        ('[DESCRICAO] 04 pólipos sésseis: ascendente 4 mm, transverso 8 mm, sigmoide 6 mm, '
         'reto 5 mm [CONCLUSAO] PÓLIPOS',
         8.0, 'measure_multi_max'),
        # 21. "+/- X mm"
        ('[DESCRICAO] pólipo pediculado com +/- 60 mm em sigmoide [CONCLUSAO] PÓLIPO',
         60.0, 'measure_explicit'),
        # 22. Hemorroida 5mm não conta
        ('[DESCRICAO] mamilos hemorroidários internos sem tamanho. Pólipo séssil de 4 mm '
         '[CONCLUSAO] HEMORROIDA + PÓLIPO',
         4.0, 'measure_explicit'),
        # 23. Flebectasia 4mm + pólipo 3mm
        ('[DESCRICAO] flebectasia medindo 4 mm no ceco. Pólipo séssil medindo 3 mm em transverso '
         '[CONCLUSAO] PÓLIPO',
         3.0, 'measure_explicit'),
        # 24. Diverticulo "tamanho variado" não conta
        ('[DESCRICAO] óstios diverticulares de tamanhos variados, alguns 5 mm. '
         'Pólipo séssil de 8 mm [CONCLUSAO] DIVERTICULOSE + PÓLIPO',
         8.0, 'measure_explicit'),
        # 25. Cm com vírgula decimal
        ('[DESCRICAO] LST de 1,5 cm em colon ascendente [CONCLUSAO] LST',
         15.0, 'measure_explicit'),
        # 26. Mm decimal
        ('[DESCRICAO] pólipo séssil de 0,5 cm [CONCLUSAO] PÓLIPO',
         5.0, 'measure_explicit'),
        # 27. Texto sem header — fallback corpo
        ('Colonoscopia normal. Presença de pólipo séssil de 5mm em sigmoide.',
         5.0, 'measure_explicit'),
        # 28. Conclusão tem o pólipo, descrição não
        ('[DESCRICAO] mucosa normal [CONCLUSAO] PÓLIPO DE 6 MM EM CÓLON',
         6.0, 'measure_explicit'),
        # 29. Várias unidades misturadas
        ('[DESCRICAO] 02 pólipos: um com 1,5 cm em ceco e outro com 8 mm em sigmoide '
         '[CONCLUSAO] PÓLIPOS',
         15.0, 'measure_multi_max'),
        # 30. Diminuto + outras lesões qualitativas
        ('[DESCRICAO] múltiplas diminutas lesões polipoides em reto, polipectomias '
         '[CONCLUSAO] PÓLIPOS',
         None, 'qualitative_diminuto'),
        # 31. Bound comparativo: "menores que 5 mm" NÃO é medida 5mm
        ('[DESCRICAO] quatro pólipos sésseis menores que 5 mm em ascendente e transverso '
         '[CONCLUSAO] PÓLIPOS',
         None, 'qualitative_diminuto'),
        # 32. Bound comparativo: "inferiores a 10 mm"
        ('[DESCRICAO] múltiplos pólipos sésseis inferiores a 10 mm '
         '[CONCLUSAO] PÓLIPOS',
         None, 'qualitative_subcm'),
        # 33. Bound comparativo: "menores que 5 mm" MAS com outro pólipo medido
        # Status é measure_explicit (não multi) porque o bound não conta como medida
        ('[DESCRICAO] pólipo pediculado de 15 mm em ceco. '
         'Três pólipos sésseis menores que 5 mm em transverso '
         '[CONCLUSAO] PÓLIPOS',
         15.0, 'measure_explicit'),
        # 34. Bug n200 #1 (id=205653): comma-list "3, 6, 20 e 3 mm" → max=20
        ('[DESCRICAO] polipos sesseis medindo 3, 6, 20 e 3 mm em colon ascendente '
         '[CONCLUSAO] POLIPOS',
         20.0, 'measure_interval'),
        # 35. Bug n200 #2 (id=19512): LST esprairada reconhecida como polipo
        ('[DESCRICAO] lesoes planas esprairadas medindo 20 e 30 mm em colon ascendente '
         '[CONCLUSAO] LST',
         30.0, 'measure_interval'),
        # 36. Bug n200 #3 (id=62219): inverted interval "15-10 mm" → max=15
        ('[DESCRICAO] polipo sessil de 15-10 mm em sigmoide [CONCLUSAO] POLIPO',
         15.0, 'measure_interval'),
        # 37. Bug n200 #4 (id=21527): multi-dim "2,5 X 1,0 X 0,5 cm" → max=25mm
        ('[DESCRICAO] polipo pediculado medindo 2,5 X 1,0 X 0,5 cm em sigmoide '
         '[CONCLUSAO] POLIPO',
         25.0, 'measure_explicit'),
        # 38. Bug n200 #5 (id=130896): "grande quantidade" NÃO é qualitative_large
        ('[DESCRICAO] grande quantidade de residuos fecais em todo o colon '
         '[CONCLUSAO] PREPARO INADEQUADO',
         None, 'no_polipo'),
    ]

    n_pass = 0
    for i, (text, exp_value, exp_status) in enumerate(cases, 1):
        r = detect_tamanho_polipo(text)
        ok_value = (r.value == exp_value) if exp_value is None else (r.value is not None and abs(r.value - exp_value) < 0.01)
        ok_status = (r.status == exp_status)
        ok = ok_value and ok_status
        marker = 'OK' if ok else 'FAIL'
        if ok:
            n_pass += 1
        print(f'{marker:4} #{i:2} expected={str(exp_value):>5}/{exp_status:22} got={str(r.value):>5}/{r.status:22} sec={r.section}')
        if not ok:
            print(f'        evidence={r.evidence[:140]!r}')
            print(f'        all_meas={r.all_measurements}')

    print()
    print(f'{n_pass}/{len(cases)} smoke tests passing')
