"""
framework_core — infraestrutura compartilhada do framework v4-B.

Este módulo contém as funções e tipos genéricos que TODO detector L1 v4 usa.
Cada detector específico (la_grau, bbps, polipo, hill...) deve importar daqui
em vez de duplicar normalização/segmentação/evidence-store.

Princípios:
- Funciona em qualquer laudo médico em PT-BR (endoscopia, patologia, radiologia).
- Configurável por SectionConfig — cada exame declara seus headers próprios.
- Output uniforme via EvidenceRecord — auditável, comparável entre variáveis.
- Zero dependência de variável-específica. Estende, não modifica.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# 1. Normalização universal de texto médico PT-BR
# ============================================================================

HTML_ENTITIES = {
    '&#13;': ' ', '&#10;': ' ', '&#13;&#10;': ' ',
    '&nbsp;': ' ', '&amp;': '&', '&quot;': '"', '&lt;': '<', '&gt;': '>',
    '<br>': ' ', '<br/>': ' ', '<br />': ' ',
    '&ccedil;': 'c', '&atilde;': 'a', '&aacute;': 'a',
    '&eacute;': 'e', '&iacute;': 'i', '&oacute;': 'o', '&uacute;': 'u',
}


def normalize(text: str) -> str:
    """Normaliza texto médico PT-BR para busca lexical determinística.

    - Expande HTML entities comuns
    - NFKD + remove diacríticos
    - lowercase
    - colapsa whitespace e separadores tipo `||`
    - remove `\\r\\n\\t`
    """
    if not isinstance(text, str):
        return ''
    t = text
    for k, v in HTML_ENTITIES.items():
        t = t.replace(k, v)
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = t.replace('||', ' . ')
    t = re.sub(r'[\r\n\t]+', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


# ============================================================================
# 2. Segmentação por seção — configurável por exame
# ============================================================================

@dataclass
class SectionConfig:
    r"""
    Configura quais headers de seção existem em um perfil de exame.

    headers: dict {section_name: [regex_patterns]}
        Exemplo:
            {'preparo': [r'\[preparo\]', r'\bpreparo\s*:'],
             'conclusao': [r'\[conclusao\]', r'\bconclusao\s*:']}

    inicio_section: nome da pseudo-seção para texto antes de qualquer header
        (default: 'inicio')

    fallback_section: nome da seção quando NENHUM header é encontrado
        (default: 'corpo')

    NOTA: 'inicio' e 'fallback' são SEMPRE adicionadas ao output, mesmo se o
    config não declarar.
    """
    headers: dict[str, list[str]]
    inicio_section: str = 'inicio'
    fallback_section: str = 'corpo'


@dataclass
class SegmentedText:
    """Resultado de uma segmentação. Cada seção pode ter múltiplos blocos."""
    sections: dict[str, list['Block']] = field(default_factory=dict)

    def get(self, section_name: str) -> list['Block']:
        return self.sections.get(section_name, [])

    def in_priority_order(self, priority: list[str]) -> list[tuple[str, 'Block']]:
        """Itera blocos na ordem de prioridade dada (seções não listadas são ignoradas)."""
        out = []
        for sec in priority:
            for block in self.get(sec):
                out.append((sec, block))
        return out


@dataclass
class Block:
    """Um bloco contíguo de texto pertencente a uma seção."""
    section: str
    start: int          # offset no texto normalizado
    end: int
    content: str

    def __len__(self) -> int:
        return len(self.content)


def segment(text_norm: str, config: SectionConfig) -> SegmentedText:
    """
    Segmenta texto normalizado em seções declaradas pelo config.

    Estratégia:
    1. Encontra TODOS os headers no texto.
    2. Ordena por posição.
    3. Cada header inicia uma nova seção; o conteúdo vai até o próximo header (ou fim).
    4. Texto antes do primeiro header → seção 'inicio'.
    5. Se NENHUM header encontrado → tudo vai para seção 'corpo' (fallback).
    """
    out = SegmentedText(sections={s: [] for s in config.headers})
    out.sections[config.inicio_section] = []
    out.sections[config.fallback_section] = []

    matches = []
    for sec, patlist in config.headers.items():
        for pat in patlist:
            for m in re.finditer(pat, text_norm):
                matches.append((m.start(), m.end(), sec))
    matches.sort(key=lambda x: x[0])

    if not matches:
        out.sections[config.fallback_section].append(
            Block(config.fallback_section, 0, len(text_norm), text_norm)
        )
        return out

    if matches[0][0] > 0:
        out.sections[config.inicio_section].append(
            Block(config.inicio_section, 0, matches[0][0], text_norm[:matches[0][0]])
        )

    for i, (start, hdr_end, sec) in enumerate(matches):
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(text_norm)
        content = text_norm[hdr_end:next_start]
        out.sections[sec].append(Block(sec, hdr_end, next_start, content))

    return out


# Default configs para perfis comuns. Cada detector pode estender ou substituir.

ENDOSCOPY_SECTIONS = SectionConfig(headers={
    'indicacao': [
        r'\[indicacao\]', r'\[indicacoes\]',
        r'\bindicacao\s*:', r'\bindicacoes\s*:',
        r'\bhipotese\s+diagnostica\b', r'\bmotivo\s+do\s+exame\b',
        r'\bhistoria\s+clinica\b',
    ],
    'sedacao': [
        r'\[sedacao\]', r'\bsedacao\s*:', r'\banestesia\s*:',
    ],
    'preparo': [
        r'\[preparo\]', r'\bpreparo\s*(?:do\s+colon|intestinal)?\s*:',
    ],
    'esofago': [
        r'\[esofago\]', r'\besofago\s*:',
    ],
    'estomago': [
        r'\[estomago\]', r'\bestomago\s*:',
    ],
    'duodeno': [
        r'\[duodeno\]', r'\bduodeno\s*:',
    ],
    'descricao': [
        r'\[descricao\]', r'\bdescricao\s*:',
        r'\[exame\s+endoscopico\]', r'\[achados\]', r'\bachados\s*:',
    ],
    'biopsia': [
        r'\[biopsia\]', r'\bbiopsia\s*:', r'\bbiopsias\s*:',
    ],
    'conclusao': [
        r'\[conclusao\]', r'\[conclusoes\]', r'\bconclusao\s*:',
        r'\bimpressao\s*:', r'\bimpressao\s+endoscopica\s*:',
        r'\bdiagnostico\s*:', r'\bdiagnosticos\s*:',
        r'\bdiagnostico\s+endoscopico\s*:',
    ],
})


# ============================================================================
# 3. Evidence record — output canônico de qualquer L1 v4
# ============================================================================

@dataclass
class EvidenceRecord:
    """
    Registro de evidência produzido por qualquer detector L1 v4.

    Campos universais (todo detector preenche):
        value:    valor extraído (str, int, float ou None)
        status:   pattern_id (LA_explicit, bbps_soma_3seg, no_match, ...)
        section:  seção onde foi encontrado (None se no_match)
        span_start, span_end: offsets no texto NORMALIZADO
        evidence: trecho ±N chars ao redor do hit (auditoria humana)
        certainty: 'high' / 'medium' / 'low' (default 'high' para hits literais)

    Campos específicos por classe de variável:
        meta: dict para flags adicionais (arith_inc, multi_letter, historical, ...)
    """
    value: object
    status: str
    section: Optional[str] = None
    span_start: int = -1
    span_end: int = -1
    evidence: str = ''
    certainty: str = 'high'
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
            'scope_meta': self.scope_meta,
            'evidence_class': self.evidence_class,
            **{f'meta_{k}': v for k, v in self.meta.items()},
        }


# ============================================================================
# 4. Helpers de janela — usados por anti-pattern e historicidade
# ============================================================================

def evidence_around(text: str, start: int, end: int, window: int = 50) -> str:
    """Recorta janela ±window ao redor do match."""
    a = max(0, start - window)
    b = min(len(text), end + window)
    return text[a:b].strip()


def has_pattern_in_window(text: str, start: int, end: int,
                           pattern: re.Pattern, window: int = 60) -> bool:
    """Verifica se um padrão regex está em janela ±window do match."""
    a = max(0, start - window)
    b = min(len(text), end + window)
    return bool(pattern.search(text[a:b]))


# ============================================================================
# 5. Listas universais de marcadores (PT-BR médico)
# ============================================================================

# Marcadores de historicidade — independente de variável
HISTORICITY_MARKERS = re.compile(
    r'\bprevi[oa]\b|\bprevias?\b'
    r'|\bpos[-\s]?tratamento\b|\bpos[-\s]?cirurgia\b'
    r'|\bcontrole\s+(?:de|tardio|pos)\b'
    r'|\bem\s+controle\s+de\b'
    r'|\brelato\s+de\b|\bhistoria\s+de\b|\bantecedente\s+de\b'
    r'|\banteriormente\b|\bha\s+\d+\s+(?:anos?|meses?)\b'
    r'|\bja\s+(?:tratada?|tratado)\b'
    r'|\bvigilancia\s+pos\b|\bcontrole\s+de\s+(?:polipectomia|mucosectomia)\b'
)


# Anti-patterns globais de outras escalas que usam letra+número
# (cada exame pode estender a sua lista)
COMMON_SCALE_ANTI_PATTERNS = re.compile(
    r'\bforrest\b|\bsakita\b|\bsavary\b|\bmiller\b'
    r'|\bpraga\b|\bbarrett\s*c\d|\bparis\b|\bbormann\b'
    r'|\bbethesda\b|\bbi[-\s]?rads\b|\bvienna\b|\bnice\b'
    r'|\blsts?[-\s]?(g|ng)\b|\bdica\s*\d\b'
)


# ============================================================================
# 6. Sentence-level helpers (para detecção de negação local)
# ============================================================================

def sentence_around(text: str, start: int, end: int) -> str:
    """Retorna a sentença que contém o span [start, end) — delimitada por
    pontos finais ou separador `||` (já normalizado para `.` por normalize()).

    Útil para checar se a negação está NA MESMA SENTENÇA do hit afirmativo.
    Sem isso, "ausência de pólipos. Hérnia hiatal." viraria negação de HH.
    """
    if not text:
        return ''
    a = text.rfind('.', 0, start)
    b = text.find('.', end)
    a = a + 1 if a >= 0 else 0
    b = b if b >= 0 else len(text)
    return text[a:b].strip()


def has_negation_in_sentence(text: str, start: int, end: int,
                              negation_pattern: re.Pattern) -> bool:
    """Verifica se uma negação está na mesma sentença do hit afirmativo.

    Mais preciso que has_pattern_in_window porque respeita fronteiras de
    sentença — evita que "ausência de X. Y presente." seja lido como negação de Y.
    """
    sent = sentence_around(text, start, end)
    return bool(negation_pattern.search(sent))


# ============================================================================
# 7. Negação por medida implícita (LEX-E + OBJ-CM)
# ============================================================================
#
# Para variáveis com componente objetivo (cm, mm, etc.), há um padrão
# transversal de NEGAÇÃO IMPLÍCITA por medida — quando o médico descreve
# uma medida que é tipicamente negativa, sem usar lemma de negação.
#
# Exemplos:
#   - hernia_hiatal: "JEC ao nível do pinçamento" → não há HH
#   - barrett: "transição mucosa coincidente com pinçamento" → sem Barrett
#   - praga c&m: "C0M0" → sem extensão
#
# Estes padrões precisam ser tratados como classe nativa do detector L1
# para variáveis LEX-E + OBJ-CM.

# Termos que indicam "ao nível de", "junto a", "coincidente com" — negação
# por proximidade anatômica.
NEGATION_BY_LOCATION = re.compile(
    r'\bao\s+nivel\s+do\b'
    r'|\bcoincidente\s+(?:com|ao|a)\b'
    r'|\bjunto\s+(?:ao|do|a)\b'
    r'|\bvist[ao]s?\s+no\b'           # "JEC vista no pinçamento"
    r'|\blocalizad[ao]s?\s+no\s+nivel\b'
    r'|\btopic[ao]\b'                  # "cardia tópica" = na posição correta
    r'|\bajustado\s+ao\b|\bjusto\s+ao\b'
)


# ============================================================================
# 8. Resolução de conflito lemma × medida (LEX-E + OBJ-*)
# ============================================================================
#
# Princípio universal validado em hh_l1_v4: quando o detector encontra
# DOIS sinais contraditórios — um lemma negativo explícito e uma medida
# afirmativa — o LEMMA EXPLÍCITO VENCE.
#
# Razão: o juízo verbal do médico é mais forte que a inferência geométrica.
# "Transição 2,0 cm acima do pinçamento. Ausência de hérnia hiatal" significa
# que o médico decidiu que aquela borderline não é HH.
#
# Ordem de prioridade canônica para variáveis LEX-E + OBJ-*:
#   1. lemma_negative   (mais forte)
#   2. lemma_positive
#   3. measurement_positive
#   4. measurement_negative
#   5. borderline_meta  (registra meta, retorna nao)

# Tier (string identifier) — baixo = mais forte (vence empate)
PRIORITY_LEX_OBJ = {
    'lemma_negative': 1,
    'lemma_positive': 2,
    'measurement_positive': 3,
    'measurement_negative': 4,
    'borderline_meta': 5,
}


def resolve_lex_obj_conflict(hits: list[tuple[str, 'EvidenceRecord']]) -> Optional['EvidenceRecord']:
    """Aplica a prioridade lemma > medida para variáveis LEX-E + OBJ-*.

    hits: lista de (tier_name, EvidenceRecord) onde tier_name ∈ PRIORITY_LEX_OBJ.

    Retorna o EvidenceRecord do tier mais forte. None se a lista vazia.
    """
    if not hits:
        return None
    hits_sorted = sorted(hits, key=lambda h: PRIORITY_LEX_OBJ.get(h[0], 99))
    return hits_sorted[0][1]


# ============================================================================
# 9. DDPA Loop — Disagreement-Driven Policy Adjudication
# ============================================================================
#
# Funções genéricas para o loop DDPA que TODO piloto usa.
# O ciclo: L1 extract → compare with IA → classify disagreements →
# adjudicate policies → patch L1 → re-extract → check convergence.
#
# O L1 converge para zero erros; os disagreements restantes são
# erros da IA ou ambiguidade inerente.


@dataclass
class DDPAClassification:
    """Classificação de um disagreement pelo DDPA."""
    ddpa_type: str          # T1-T7
    l1_value: str
    ia_value: str
    who_correct: str        # 'l1', 'ia', 'ambiguous', 'unknown'
    description: str = ''


# Standard DDPA taxonomy (7 types)
DDPA_TYPES = {
    'T1': 'AI hallucination — AI inferred value not stated in text',
    'T2': 'AI false negative — AI missed real data present in text',
    'T3': 'Ontological refinement — L1 classifies more specifically than AI',
    'T4': 'AI false positive — AI assigned value to stub/empty report',
    'T5': 'Normalization — same concept, different label (e.g., 14 vs 15 days)',
    'T6': 'L1 miss — L1 failed to capture data present in text',
    'T7': 'Other / ambiguous — genuine ambiguity or edge case',
}


def assess_convergence(
    n_total: int,
    n_disagree: int,
    n_l1_errors: int,
    n_ia_errors: int,
    n_ambiguous: int,
    concordance_threshold: float = 95.0,
) -> dict:
    """Avalia se o loop DDPA atingiu saturação operacional.

    Critérios de saturação:
    - L1 errors == 0 (ou < 0.01% do total)
    - Todos os disagreements restantes são IA errors ou ambiguidade
    - Concordância > concordance_threshold (default 95%)

    concordance_threshold pode ser rebaixado quando disagreements são conceituais
    (ex: bilateral vs estrita), não erros de extração.

    Returns dict com:
        saturated: bool
        concordance_pct: float
        l1_error_rate: float
        ia_error_share: float (% dos disagreements que são IA errada)
        residual_ambiguity_pct: float
    """
    concordance = 100.0 * (n_total - n_disagree) / n_total if n_total > 0 else 0
    l1_error_rate = 100.0 * n_l1_errors / n_total if n_total > 0 else 0
    ia_error_share = 100.0 * n_ia_errors / n_disagree if n_disagree > 0 else 100
    residual = 100.0 * n_ambiguous / n_total if n_total > 0 else 0

    saturated = (
        n_l1_errors == 0
        and concordance >= concordance_threshold
        and ia_error_share >= 85.0
    )

    return {
        'saturated': saturated,
        'concordance_pct': round(concordance, 2),
        'disagree_n': n_disagree,
        'l1_error_rate_pct': round(l1_error_rate, 4),
        'ia_error_share_pct': round(ia_error_share, 1),
        'residual_ambiguity_pct': round(residual, 2),
    }


# ============================================================================
# Self-test
# ============================================================================

if __name__ == '__main__':
    txt = """
    [INDICACAO] Rastreamento de neoplasia
    [PREPARO] Adequado (Escala de Boston: 3+3+3=9)
    [DESCRICAO] colon sem alteracoes
    [CONCLUSAO] EXAME NORMAL
    """
    n = normalize(txt)
    print('Normalized:', repr(n[:100]))

    seg = segment(n, ENDOSCOPY_SECTIONS)
    for sec, blocks in seg.sections.items():
        if blocks:
            print(f'  [{sec}] {[b.content[:60] for b in blocks]}')

    # DDPA convergence test (Pilot #6 iter 2 data)
    conv = assess_convergence(
        n_total=51959, n_disagree=1683,
        n_l1_errors=0, n_ia_errors=1494, n_ambiguous=189
    )
    print(f'\nDDPA convergence: {conv}')
    assert conv['saturated'] is True, f'Expected saturated=True, got {conv}'
    print('DDPA self-test passed.')
