"""
polipo_l1_v4 — detector L1 v4-B para `polipo_presente` (LEX-E binário sim/nao).

Consome framework_core. Section priority: conclusao > descricao > corpo.
NUNCA escaneia [INDICACAO] (historicidade) nem [SEDACAO]/[PREPARO].

Algoritmo:
1. Para cada bloco de findings:
   - Procura padrão de polipo (`polip(o|os|oide|oides)`).
   - Para cada hit, checa se está em sentença/janela negativa.
   - Hit positivo (sem negação) → polipo_explicit → sim
   - Hit negativo → polipo_negative → nao explícito
2. Se nenhum hit em nenhuma seção → no_match → default nao
3. Se primeiro hit foi negativo, continua escaneando — uma menção positiva
   posterior na mesma seção deve ganhar (`presença de pólipo` mesmo após
   `não foram identificadas ectasias`).

Output: PolipoResult(value, status, section, span, evidence)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from framework_core import (
    normalize, segment, ENDOSCOPY_SECTIONS,
    evidence_around, has_pattern_in_window,
)


SECTION_PRIORITY = ['conclusao', 'descricao', 'corpo', 'sedacao', 'preparo', 'inicio']


# ----------------------------------------------------------------------------
# Padrões
# ----------------------------------------------------------------------------

# Hit de polipo: polipo, polipos, polipoide, polipoides
# Excluímos polipectomia e polipose (tratados separadamente).
PAT_POLIP = re.compile(
    r'\bpolip(?:o|os|oide|oides)\b'
)

# Polipose isolada (PAF, polipose adenomatosa) — tratado como historicidade
# se aparecer no findings (raro). Não dispara afirmativo sozinho.
PAT_POLIPOSE = re.compile(r'\bpolipose\b')

# Polipectomia — historicidade (controle, prévia)
PAT_POLIPECTOMIA = re.compile(r'\bpolipectomia\b')

# Negação na MESMA SENTENÇA do hit. Os médicos enumeram negativos em listas
# longas: "não foram identificados óstios diverticulares, ectasias vasculares,
# formações polipóides ou tumorais". A negação fica >80 chars antes do "polip".
# Solução: pegar a sentença (até último '.' antes do hit) e checar marker.
PAT_NEGATION_MARKER = re.compile(
    r'\b(?:'
    r'nao\s+(?:foram|forma|ha|se\s+observa|observad|evidenciad|visualiz|identificad|existem|encontrad)'
    r'|nao\s+visualiz'
    r'|sem\b'
    r'|ausencia\b'
    r'|ausentes?\b'
    r'|nenhum(?:a)?\b'
    r'|livre\s+de'
    r')'
)

# Pseudopolipos (RCUI) — não conta como polipo verdadeiro.
PAT_PSEUDOPOLIP = re.compile(r'\bpseudopolip')

# Sinônimos morfológicos não-lexicais — interpretação que um médico humano faria.
# LST (Lateral Spreading Tumor), lesão plana/deprimida/elevada, Paris classification,
# mucosectomia (procedimento implica lesão).
PAT_POLIP_SYNONYM = re.compile(
    r'\blsts?[-\s]?(?:g|ng|granular|nao[-\s]?granular)?\b'
    r'|\blateral\s+spreading\s+(?:tumor|lesion)\b'
    r'|\blesao\s+plana(?:\s+elevada)?\b'
    r'|\blesao\s+deprimida\b'
    r'|\blesao\s+(?:com\s+)?espraiamento\s+lateral\b'
    r'|\bespraiamento\s+lateral\b'
    r'|\bparis\s+0?[-\s]?(?:is|ip|iia|iib|iic|iii)\b'
    r'|\bmucosectomia\b'
    r'|\bemr\b'
    r'|\besd\b'
)

# Contexto hipotético/condicional dentro do texto:
# "pequenas lesões, como pólipos, podem passar desapercebidas" — não é achado.
PAT_HYPOTHETICAL = re.compile(
    r'\b(?:podem\s+passar|podem\s+(?:nao\s+)?ser\s+vistos|pode[m]?\s+(?:nao\s+)?ser\s+visualizad'
    r'|escapem?\s+a\s+visualizacao|despercebid)'
)

# Negação imediata à direita: "polipos ... ausentes", "polipoide ... nao"
# Mais raro mas existe.
PAT_NEGATION_RIGHT = re.compile(
    r'^[\w\s,çãéáíóú-]{0,40}?(?:ausentes?|nao\s+identificad|nao\s+visualiz)'
)

# Marcadores de historicidade locais (controle de polipectomia, prévio, pos)
PAT_HISTORICAL_LOCAL = re.compile(
    r'\b(?:controle\s+de\s+polip(?:o|ectomia)?'
    r'|vigilancia\s+de\s+polip'
    r'|follow[-\s]?up\s+polip'
    r'|pos[-\s]?polipectomia'
    r'|polipectomia\s+previa'
    r'|polipo\s+previo'
    r'|previas?\b'
    r'|previamente'
    r'|antecedente\s+de'
    r'|historia\s+(?:familiar|pessoal|previa)\s+de'
    r'|rastrei?o\s+de\s+polip'
    r'|rastreamento\s+de\s+polip'
    r')'
)


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

DETECTOR_VERSION = 'polipo_l1_v4@1.1.0'


@dataclass
class PolipoResult:
    value: Optional[str]              # 'sim' / 'nao'
    status: str                       # polipo_explicit / polipo_synonym / polipo_negative / no_match / polipo_historical
    section: Optional[str] = None
    span_start: int = -1
    span_end: int = -1
    evidence: str = ''
    certainty: str = 'high'           # 'high' (lexical) / 'medium' (synonym interpretation)
    meta: dict = field(default_factory=dict)
    # v1.1 (GPT-5 #3): coverage claim do escaneamento — obrigatório para auditar
    # silêncio como evidência de negativo.
    scope_meta: dict = field(default_factory=dict)
    # v1.1 (GPT-5 #1): semantic distance — eixo separado de epistemic certainty.
    # literal | lexical_alias | ontology_alias | bound | qualitative | silence_default
    evidence_class: str = 'literal'

    def to_dict(self) -> dict:
        return {
            'value': self.value,
            'status': self.status,
            'section': self.section,
            'span_start': self.span_start,
            'span_end': self.span_end,
            'evidence': self.evidence[:200],
            'certainty': self.certainty,
            **{f'meta_{k}': v for k, v in self.meta.items()},
        }


# ----------------------------------------------------------------------------
# Detecção
# ----------------------------------------------------------------------------

def _is_negated(content: str, hit_start: int, hit_end: int) -> bool:
    """Verifica se hit está em sentença negativa.

    Estratégia: procura o início da sentença atual (último '.' até 250 chars
    antes do hit) e checa se há marker de negação entre esse início e o hit.
    Captura enumerações longas tipo "não foram identificados X, Y, polipoides, Z".
    Também detecta contexto hipotético: "lesões como pólipos podem passar desapercebidas".
    """
    sentence_start = max(0, hit_start - 250)
    last_period = content.rfind('.', sentence_start, hit_start)
    sent_left = max(sentence_start, last_period + 1) if last_period != -1 else sentence_start

    # Sentença completa = de sent_left até próximo período
    next_period = content.find('.', hit_end)
    sent_right = next_period if next_period != -1 else min(len(content), hit_end + 100)
    full_sentence = content[sent_left: sent_right]

    if PAT_HYPOTHETICAL.search(full_sentence):
        return True

    left_window = content[sent_left: hit_start]
    if PAT_NEGATION_MARKER.search(left_window):
        return True
    right_window = content[hit_end: min(len(content), hit_end + 40)]
    if PAT_NEGATION_RIGHT.match(right_window):
        return True
    return False


def _is_historical(content: str, hit_start: int, hit_end: int) -> bool:
    """Verifica se hit é menção histórica (controle de, prévio, pos-polipectomia)."""
    a = max(0, hit_start - 60)
    b = min(len(content), hit_end + 60)
    return bool(PAT_HISTORICAL_LOCAL.search(content[a:b]))


def _scan_section(content: str, section_name: str, base_offset: int):
    """Escaneia uma seção. Retorna lista de (status, span_start, span_end, evidence).

    Tipos de hit:
    - polipo_explicit  (lexical, certainty=high)
    - polipo_synonym   (LST, lesão plana, Paris, mucosectomia — certainty=medium)
    - polipo_negative  (negação no contexto)
    - polipo_historical (controle/vigilância/follow-up)

    Coleta TODOS os hits — detect_polipo decide o valor final.
    """
    hits = []

    # 1. Hits lexicais explícitos
    for m in PAT_POLIP.finditer(content):
        s, e = m.start(), m.end()
        if PAT_PSEUDOPOLIP.search(content[max(0, s - 6):e]):
            continue
        ev = evidence_around(content, s, e, window=60)
        if _is_historical(content, s, e):
            hits.append(('polipo_historical', base_offset + s, base_offset + e, ev, 'high'))
            continue
        if _is_negated(content, s, e):
            hits.append(('polipo_negative', base_offset + s, base_offset + e, ev, 'high'))
            continue
        hits.append(('polipo_explicit', base_offset + s, base_offset + e, ev, 'high'))

    # 2. Hits sinônimos (interpretação morfológica)
    for m in PAT_POLIP_SYNONYM.finditer(content):
        s, e = m.start(), m.end()
        ev = evidence_around(content, s, e, window=60)
        if _is_historical(content, s, e):
            hits.append(('polipo_historical', base_offset + s, base_offset + e, ev, 'medium'))
            continue
        if _is_negated(content, s, e):
            hits.append(('polipo_negative', base_offset + s, base_offset + e, ev, 'medium'))
            continue
        hits.append(('polipo_synonym', base_offset + s, base_offset + e, ev, 'medium'))

    return hits


def _build_scope_meta(
    sections_scanned: list[str],
    chars_scanned: int,
    all_hits: list,
    scope_complete: bool,
) -> dict:
    """Coverage claim v1.1 — audit do escaneamento para silêncio auditável."""
    n_explicit = sum(1 for h in all_hits if h[1] == 'polipo_explicit')
    n_synonym = sum(1 for h in all_hits if h[1] == 'polipo_synonym')
    n_negative = sum(1 for h in all_hits if h[1] == 'polipo_negative')
    n_historical = sum(1 for h in all_hits if h[1] == 'polipo_historical')
    return {
        'detector_version': DETECTOR_VERSION,
        'scope_complete': scope_complete,
        'sections_scanned': list(sections_scanned),
        'chars_scanned': int(chars_scanned),
        'pattern_families_run': ['polip_lexical', 'polip_synonym',
                                 'negation', 'historical', 'pseudopolip_guard',
                                 'hypothetical_guard'],
        'hits_total': len(all_hits),
        'hits_polipo_explicit': n_explicit,
        'hits_polipo_synonym': n_synonym,
        'explicit_negative_hits': n_negative,
        'historical_hits': n_historical,
    }


def detect_polipo(text: str) -> PolipoResult:
    if not isinstance(text, str) or not text.strip():
        return PolipoResult(
            value='nao', status='no_match',
            scope_meta={
                'detector_version': DETECTOR_VERSION,
                'scope_complete': False,
                'sections_scanned': [],
                'chars_scanned': 0,
                'pattern_families_run': [],
                'hits_total': 0,
                'explicit_negative_hits': 0,
                'historical_hits': 0,
                'skip_reason': 'empty_or_invalid_text',
            },
        )

    norm = normalize(text)
    seg = segment(norm, ENDOSCOPY_SECTIONS)

    sections_scanned: list[str] = []
    chars_scanned = 0
    all_hits = []  # (section, status, span_start, span_end, evidence, certainty)
    for sec_name in SECTION_PRIORITY:
        blocks = list(seg.get(sec_name))
        if not blocks:
            continue
        sections_scanned.append(sec_name)
        for block in blocks:
            chars_scanned += len(block.content)
            for status, s, e, ev, cert in _scan_section(block.content, sec_name, block.start):
                all_hits.append((sec_name, status, s, e, ev, cert))

    scope_complete = chars_scanned > 0 and len(sections_scanned) > 0
    scope_meta = _build_scope_meta(sections_scanned, chars_scanned, all_hits, scope_complete)

    if not all_hits:
        return PolipoResult(
            value='nao', status='no_match',
            meta={'n_hits': 0}, scope_meta=scope_meta,
            evidence_class='silence_default',
        )

    # Prioridade: polipo_explicit (high) > polipo_synonym (medium) > polipo_negative > polipo_historical
    for sec_name, status, s, e, ev, cert in all_hits:
        if status == 'polipo_explicit':
            return PolipoResult(
                value='sim', status='polipo_explicit',
                section=sec_name, span_start=s, span_end=e, evidence=ev,
                certainty=cert, meta={'n_hits': len(all_hits)},
                scope_meta=scope_meta,
                evidence_class='literal',
            )

    for sec_name, status, s, e, ev, cert in all_hits:
        if status == 'polipo_synonym':
            return PolipoResult(
                value='sim', status='polipo_synonym',
                section=sec_name, span_start=s, span_end=e, evidence=ev,
                certainty=cert, meta={'n_hits': len(all_hits)},
                scope_meta=scope_meta,
                evidence_class='ontology_alias',
            )

    for sec_name, status, s, e, ev, cert in all_hits:
        if status == 'polipo_negative':
            return PolipoResult(
                value='nao', status='polipo_negative',
                section=sec_name, span_start=s, span_end=e, evidence=ev,
                certainty=cert, meta={'n_hits': len(all_hits)},
                scope_meta=scope_meta,
                evidence_class='literal',
            )

    sec_name, status, s, e, ev, cert = all_hits[0]
    return PolipoResult(
        value='nao', status='polipo_historical',
        section=sec_name, span_start=s, span_end=e, evidence=ev,
        certainty=cert, meta={'n_hits': len(all_hits)},
        scope_meta=scope_meta,
        evidence_class='qualitative',
    )


# ----------------------------------------------------------------------------
# Smoke tests
# ----------------------------------------------------------------------------

if __name__ == '__main__':
    cases = [
        # 1. Afirmativo simples em descrição
        ('[DESCRICAO] presença de pólipo séssil em sigmóide [CONCLUSAO] PÓLIPO',
         'sim', 'polipo_explicit'),
        # 2. Conclusão em CAPS
        ('[DESCRICAO] mucosa normal [CONCLUSAO] PÓLIPO EM CÓLON ASCENDENTE',
         'sim', 'polipo_explicit'),
        # 3. Múltiplos
        ('[DESCRICAO] dois pólipos sésseis em cólon [CONCLUSAO] DOIS PÓLIPOS COLÔNICOS',
         'sim', 'polipo_explicit'),
        # 4. Negativo: não há formações polipóides
        ('[DESCRICAO] não há formações polipóides ou tumorais [CONCLUSAO] EXAME NORMAL',
         'nao', 'polipo_negative'),
        # 5. Negativo: ausência de pólipos
        ('[DESCRICAO] mucosa normal. Ausência de pólipos e tumorações. [CONCLUSAO] DIVERTICULOSE',
         'nao', 'polipo_negative'),
        # 6. Negativo: não foram identificadas formações polipóides
        ('[DESCRICAO] não foram identificadas formações polipóides nem ectasias [CONCLUSAO] EXAME NORMAL',
         'nao', 'polipo_negative'),
        # 7. Indicação controle de polipectomia, achado normal — indicacao deve ser ignorada
        ('[INDICACAO] Controle de polipectomia [DESCRICAO] mucosa normal [CONCLUSAO] EXAME NORMAL',
         'nao', 'no_match'),
        # 8. Indicação controle + descrição com pólipo
        ('[INDICACAO] Controle de pólipo [DESCRICAO] presença de pólipo séssil [CONCLUSAO] PÓLIPO EM SIGMÓIDE',
         'sim', 'polipo_explicit'),
        # 9. Sem nenhuma menção
        ('[DESCRICAO] mucosa normal [CONCLUSAO] EXAME NORMAL',
         'nao', 'no_match'),
        # 10. Sem pólipos na conclusão
        ('[DESCRICAO] mucosa normal [CONCLUSAO] sem pólipos',
         'nao', 'polipo_negative'),
        # 11. PAF na indicação, achado normal
        ('[INDICACAO] Polipose adenomatosa familiar [DESCRICAO] mucosa normal [CONCLUSAO] EXAME NORMAL',
         'nao', 'no_match'),
        # 12. Formação polipoide afirmativa
        ('[DESCRICAO] formação polipoide em cólon transverso [CONCLUSAO] FORMAÇÃO POLIPÓIDE',
         'sim', 'polipo_explicit'),
        # 13. Lesão polipoide pediculada
        ('[DESCRICAO] lesão polipoide pediculada de 8mm [CONCLUSAO] LESÃO POLIPÓIDE',
         'sim', 'polipo_explicit'),
        # 14. Sem header — fallback corpo
        ('Colonoscopia normal. Presença de pólipo séssil de 5mm em sigmóide.',
         'sim', 'polipo_explicit'),
        # 15. Não foram visualizadas formações polipoides
        ('[DESCRICAO] não foram visualizadas formações polipoides [CONCLUSAO] EXAME NORMAL',
         'nao', 'polipo_negative'),
        # 16. Conclusão PÓLIPOS COLÔNICOS
        ('[DESCRICAO] mucosa normal [CONCLUSAO] PÓLIPOS COLÔNICOS',
         'sim', 'polipo_explicit'),
        # 17. DIVERTICULOSE sem polipo
        ('[DESCRICAO] divertículos em sigmóide [CONCLUSAO] DIVERTICULOSE COLÔNICA',
         'nao', 'no_match'),
        # 18. Negação enumerada longa: "não foram identificados óstios diverticulares, formações polipóides..."
        ('[DESCRICAO] Não foram identificados óstios diverticulares, formações polipóides, ectasias vasculares [CONCLUSAO] EXAME NORMAL',
         'nao', 'polipo_negative'),
        # 19. Polipectomia prévia em descrição — não conta (polipectomia != polipo_presente)
        ('[DESCRICAO] cicatriz de polipectomia prévia em sigmóide [CONCLUSAO] EXAME NORMAL',
         'nao', 'no_match'),
        # 20. Negativo + afirmativo na mesma sentença composta — afirmativo vence
        ('[DESCRICAO] não há formações polipóides em ceco. Presença de pólipo séssil em sigmóide [CONCLUSAO] PÓLIPO',
         'sim', 'polipo_explicit'),
        # --- Sinônimos morfológicos ---
        # 21. LST
        ('[DESCRICAO] presença de LST-G granular em ceco de cerca de 30mm [CONCLUSAO] LST EM CECO',
         'sim', 'polipo_synonym'),
        # 22. Lesão plana
        ('[DESCRICAO] lesão plana de 8mm em sigmóide [CONCLUSAO] LESÃO PLANA',
         'sim', 'polipo_synonym'),
        # 23. Paris classification
        ('[DESCRICAO] lesão Paris 0-Is em colon ascendente, com 5mm [CONCLUSAO] LESÃO 0-IS',
         'sim', 'polipo_synonym'),
        # 24. Mucosectomia (procedimento implica lesão)
        ('[DESCRICAO] realizada mucosectomia em sigmóide [CONCLUSAO] MUCOSECTOMIA',
         'sim', 'polipo_synonym'),
        # 25. Sinônimo + explicit — explicit vence
        ('[DESCRICAO] LST-G granular [CONCLUSAO] PÓLIPO EM CECO',
         'sim', 'polipo_explicit'),
        # 26. Sinônimo negado
        ('[DESCRICAO] sem lesões planas ou polipóides [CONCLUSAO] EXAME NORMAL',
         'nao', 'polipo_negative'),
    ]

    n_pass = 0
    for i, (text, exp_value, exp_status) in enumerate(cases, 1):
        r = detect_polipo(text)
        ok = (r.value == exp_value) and (r.status == exp_status)
        marker = 'OK' if ok else 'FAIL'
        if ok:
            n_pass += 1
        print(f'{marker:4} #{i:2} expected={exp_value:3}/{exp_status:18} got={str(r.value):3}/{r.status:18} sec={r.section}')
        if not ok:
            print(f'        evidence={r.evidence[:120]!r}')

    print()
    print(f'{n_pass}/{len(cases)} smoke tests passing')
