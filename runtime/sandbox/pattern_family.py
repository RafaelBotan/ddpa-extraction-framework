"""Pattern Family (Proposer v2.0) — normalized shape families with F4/F5 routing.

Per GPT-5 verdict 2026-04-16: the unit of clustering is a normalized textual
family, not ia_value. Multidim (F4) and negative-subject (F5) windows go to
separate lanes, never into the positive proposer funnel.

Pipeline:
    clusters  -> normalize_window per evidence window
              -> route to {positive, multidim, negative}
              -> group positive windows by skeleton hash
              -> emit PatternFamily per group + per routed lane
              -> emit_regex_from_family + generate_patch_cards_from_families
"""
from __future__ import annotations
import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sandbox.patch_card import PatchCard, ProposedChange


# ---------------------------------------------------------------------------
# Lexicons per variable — positive subjects, negative subjects, verbs
# ---------------------------------------------------------------------------

# Default lexicons cover polipo_tamanho_max_mm and similar "lesion-with-size"
# variables. Extend per-variable via SUBJECT_LEXICON override when needed.
POLIPO_POSITIVE_SUBJECTS = {
    'polipo', 'polipos',
    'lesao', 'lesoes',  # lesao plana/elevada/polipoide treated as lesao
    'polipoide', 'polipoides',
    'polipectomia', 'polipectomias',
    'sessil', 'sesseis',
    'pediculado', 'pediculada', 'pediculados', 'pediculadas',
    'plano', 'plana', 'planos', 'planas',  # plana/elevada
    'elevada', 'elevado', 'elevadas', 'elevados',
    'neoplasia', 'neoplasias',
}

POLIPO_NEGATIVE_SUBJECTS = {
    'papilofibroma', 'papilofibromas',
    'papilofirboma', 'papilofirbomas',  # obs: typo real no corpus
    'hemorroida', 'hemorroidas', 'hemorroide', 'hemorroides',
    'hemorroidaria', 'hemorroidarias',
    'fissura', 'fissuras',
    'corpo', 'corpos',  # "corpo estranho"
    'divertículo', 'diverticulo', 'diverticulos',
    'ulcera', 'ulceras',  # úlcera sem pólipo
    'tatuagem', 'tatuagens',
    'pedículo', 'pediculo',  # quando isolado ("pedículo menor que 10mm") é tamanho do pedículo, não pólipo
}

# Subject class tokens (replace after lexicon match)
SUBJ_POLIPO = 'POLIPO_SUBJ'
SUBJ_LESAO = 'LESAO_SUBJ'

# Verbs/quantifiers
SHOW_VERBS = {'exibia', 'exibiam', 'apresentava', 'apresentavam', 'apresenta',
              'apresentou', 'visualizada', 'visualizado', 'identificada',
              'identificado', 'presenca', 'presente', 'presentes'}
MEASURE_VERBS = {'medindo', 'mede', 'medida', 'medem', 'mediu'}
QUANTIFIER_CERCA = {'cerca', 'aproximadamente', 'aprox'}
QUANTIFIER_PLUSMINUS_TOKEN = 'PLUSMINUS'

# Tokens to keep as-is (clinical connectors that carry meaning for skeleton).
# Anatomy is deliberately excluded here — it varies per case but doesn't change
# the template "polipo sessil N mm". Anatomy belongs to scope metadata, not
# skeleton. Keep only words that define the template shape.
KEEP_TOKENS = {
    'superficie', 'regular', 'irregular',
    'padrao', 'vascular', 'margem',
    'de', 'com', 'em',  # short connectors used in "com cerca de N mm"
}

NEGATIVE_LANE_THRESHOLD = 0.5  # fraction of windows with negative subject to route to negative lane
MULTIDIM_LANE_THRESHOLD = 0.5  # fraction of windows with multidim pattern


# ---------------------------------------------------------------------------
# Normalization primitives
# ---------------------------------------------------------------------------

def _strip_accents(s: str) -> str:
    """NFKD decompose + drop combining marks."""
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_plusminus(s: str) -> str:
    """Replace '+/-' and variants with PLUSMINUS token."""
    return re.sub(r'\+\s*/?\s*-', f' {QUANTIFIER_PLUSMINUS_TOKEN} ', s)


def normalize_window(text: str, unit: str | None = None) -> str:
    """Normalize an evidence window into a comparable skeleton.

    Steps: accents stripped, lowercased, numbers -> N, unit -> UNIT_{UNIT},
    subject lexicon -> POLIPO_SUBJ/LESAO_SUBJ, verbs -> SHOW_VERB/MEASURE_VERB,
    'cerca de' -> CERCA, '+/-' -> PLUSMINUS. Clinical connectors preserved.
    """
    if not text:
        return ''
    s = _strip_accents(str(text)).lower()
    s = _normalize_plusminus(s)

    # tokenize on non-word chars, keep PLUSMINUS as single token
    s = re.sub(r'[^\w\s/]', ' ', s)  # drop punctuation; keep '/' for plana/elevada
    tokens = [t for t in re.split(r'\s+', s) if t]

    # unit token upper-case (mm -> UNIT_MM)
    unit_token = f'UNIT_{unit.upper()}' if unit else None

    out: list[str] = []
    for tok in tokens:
        if tok == QUANTIFIER_PLUSMINUS_TOKEN.lower() or tok == QUANTIFIER_PLUSMINUS_TOKEN:
            out.append(QUANTIFIER_PLUSMINUS_TOKEN)
            continue
        # Number (with optional decimal)
        if re.fullmatch(r'\d+(?:[.,]\d+)?', tok):
            out.append('N')
            continue
        # Number glued to unit: "5mm"
        if unit and re.fullmatch(rf'\d+(?:[.,]\d+)?{re.escape(unit.lower())}', tok):
            out.append('N')
            out.append(unit_token)
            continue
        # Unit alone
        if unit and tok == unit.lower():
            out.append(unit_token)
            continue
        # Subject lexicon
        if tok in POLIPO_POSITIVE_SUBJECTS and tok in {'polipo', 'polipos', 'polipoide', 'polipoides', 'polipectomia', 'polipectomias', 'sessil', 'sesseis', 'pediculado', 'pediculada', 'pediculados', 'pediculadas'}:
            out.append(SUBJ_POLIPO)
            continue
        if tok in POLIPO_POSITIVE_SUBJECTS and tok in {'lesao', 'lesoes', 'plano', 'plana', 'planos', 'planas', 'elevada', 'elevado', 'elevadas', 'elevados', 'neoplasia', 'neoplasias'}:
            out.append(SUBJ_LESAO)
            continue
        if tok in QUANTIFIER_CERCA:
            out.append('CERCA')
            continue
        if tok in SHOW_VERBS:
            out.append('SHOW_VERB')
            continue
        if tok in MEASURE_VERBS:
            out.append('MEASURE_VERB')
            continue
        if tok in KEEP_TOKENS:
            out.append(tok)
            continue
        # drop everything else (noise words)

    return ' '.join(out)


def detect_negative_subject(text: str, var_name: str) -> bool:
    """Return True if the window contains a variable-specific negative subject.

    For polipo_tamanho_max_mm, these are papilofibroma/hemorroida/fissura/etc.
    """
    if not text:
        return False
    s = _strip_accents(str(text)).lower()
    tokens = set(re.findall(r'\w+', s))
    # Variable-aware (expand with more vars as needed)
    if 'polipo' in var_name.lower() or 'tamanho' in var_name.lower():
        negatives = POLIPO_NEGATIVE_SUBJECTS
    else:
        negatives = set()
    # Must have a negative subject AND no positive polipo/lesao subject nearby
    has_negative = bool(tokens & {_strip_accents(n) for n in negatives})
    has_polipo = 'polipo' in tokens or 'polipos' in tokens
    return has_negative and not has_polipo


def detect_multidim(text: str) -> bool:
    """Return True if the window contains a multidimensional measurement (NxN).

    Examples that match: '3x4 mm', '3 x 4mm', '10x15 mm'.
    Examples that do NOT match: '8mm e 10mm' (list), '5mm'.
    """
    if not text:
        return False
    s = _strip_accents(str(text)).lower()
    return bool(re.search(r'\d+\s*x\s*\d+', s))


# ---------------------------------------------------------------------------
# Skeleton extraction — a bounded window around N UNIT_{UNIT}
# ---------------------------------------------------------------------------

def extract_skeleton(normalized: str, unit: str | None = None, radius: int = 5) -> str:
    """Take N tokens around the first 'N UNIT_{U}' occurrence as the skeleton.

    If no unit anchor, fall back to first 'N' token. If none, return empty.
    """
    if not normalized:
        return ''
    tokens = normalized.split()
    if not tokens:
        return ''
    unit_token = f'UNIT_{unit.upper()}' if unit else None
    anchor_idx: int | None = None
    for i, t in enumerate(tokens):
        if unit_token and i > 0 and t == unit_token and tokens[i - 1] == 'N':
            anchor_idx = i - 1
            break
    if anchor_idx is None:
        # fallback: first N
        for i, t in enumerate(tokens):
            if t == 'N':
                anchor_idx = i
                break
    if anchor_idx is None:
        return ''
    lo = max(0, anchor_idx - radius)
    hi = min(len(tokens), anchor_idx + radius + 1)
    return ' '.join(tokens[lo:hi])


def _hash_skeleton(skel: str) -> str:
    if not skel:
        return 'empty'
    return hashlib.sha256(skel.encode('utf-8')).hexdigest()[:12]


def _grounding_pattern_for(ia_value, unit: str | None) -> re.Pattern | None:
    """Build a compile regex to find the ia_value adjacent to the unit in text.

    Same semantics as observability.pattern_discovery._build_grounding_pattern,
    duplicated here to avoid a cross-package import cycle.
    """
    try:
        num = float(ia_value)
    except (TypeError, ValueError):
        return None
    if unit is None:
        return None
    int_val = int(num) if num.is_integer() else None
    unit_esc = re.escape(str(unit))
    if int_val is not None:
        pat = rf'\b{int_val}\s*(?:[.,]\s*\d+)?\s*{unit_esc}\b'
    else:
        val_str = str(ia_value).rstrip('0').rstrip('.')
        pat = rf'\b{re.escape(val_str)}\s*(?:[.,]\s*\d+)?\s*{unit_esc}\b'
    return re.compile(pat, flags=re.IGNORECASE)


def classify_text_to_family(
    text: str,
    ia_value,
    var_name: str,
    unit: str | None,
    window_chars: int = 50,
) -> tuple[str, str]:
    """Classify a miss text into a lane + skeleton-hash family.

    Returns (lane, family_id). lane in {positive, multidim, negative, no_anchor}.
    family_id is 'pf_<hash>' for positive, or lane tag for others.
    """
    if not text:
        return ('no_anchor', 'pf_noanchor')
    pat = _grounding_pattern_for(ia_value, unit)
    if pat is None:
        return ('no_anchor', 'pf_noanchor')
    m = pat.search(text)
    if m is None:
        return ('no_anchor', 'pf_noanchor')
    start = max(0, m.start() - window_chars)
    end = min(len(text), m.end() + window_chars)
    window = text[start:end]
    if detect_negative_subject(window, var_name):
        return ('negative', 'pf_negative')
    if detect_multidim(window):
        return ('multidim', 'pf_multidim')
    normalized = normalize_window(window, unit=unit)
    skel = extract_skeleton(normalized, unit=unit)
    if not skel:
        return ('no_anchor', 'pf_noanchor')
    return ('positive', f'pf_{_hash_skeleton(skel)}')


# ---------------------------------------------------------------------------
# PatternFamily dataclass
# ---------------------------------------------------------------------------

@dataclass
class PatternFamily:
    family_id: str                 # 'pf_' + skeleton_hash or lane tag
    lane: str                      # positive | multidim | negative | needs_reclustering
    skeleton: str                  # canonical skeleton (empty for non-positive lanes)
    variable: str
    source_cluster_ids: list[str]  # ia_values from source clusters
    ia_value_distribution: dict[str, int]  # ia_value -> sum of window counts
    evidence_windows: list[str]
    negative_windows: list[str]
    total_count: int               # sum of source cluster counts mapped to this family
    grounding_stats: dict[str, Any] = field(default_factory=dict)
    purity_score: float = 0.0      # fraction of windows matching dominant skeleton

    def to_dict(self) -> dict:
        return {
            'family_id': self.family_id,
            'lane': self.lane,
            'skeleton': self.skeleton,
            'variable': self.variable,
            'source_cluster_ids': self.source_cluster_ids,
            'ia_value_distribution': self.ia_value_distribution,
            'evidence_windows': self.evidence_windows[:20],
            'negative_windows': self.negative_windows[:10],
            'total_count': self.total_count,
            'grounding_stats': self.grounding_stats,
            'purity_score': self.purity_score,
            'ia_values': list(self.ia_value_distribution.keys()),
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_pattern_families(
    clusters: list[dict],
    var_name: str,
    unit: str | None = None,
) -> list[dict]:
    """Route each evidence window to a lane, then group positive windows by
    normalized skeleton. Returns a list of family dicts.

    Each window carries a share of its cluster's count (evenly split), so the
    total_count of a family sums shares across clusters.
    """
    # lane buckets
    # positive windows are re-bucketed by skeleton hash
    positive_by_skel: dict[str, list[dict]] = defaultdict(list)
    multidim_buf: list[dict] = []
    negative_buf: list[dict] = []

    for cluster in clusters:
        ia_val = str(cluster.get('ia_value'))
        count = int(cluster.get('count', 0))
        windows = cluster.get('evidence_windows', []) or []
        if not windows:
            continue
        # share of count per window; using equal share for simplicity
        per_window_count = count / len(windows)

        for w in windows:
            record = {
                'window': w,
                'ia_value': ia_val,
                'share': per_window_count,
            }
            # Priority: negative > multidim > positive
            if detect_negative_subject(w, var_name):
                negative_buf.append(record)
                continue
            if detect_multidim(w):
                multidim_buf.append(record)
                continue
            normalized = normalize_window(w, unit=unit)
            skel = extract_skeleton(normalized, unit=unit)
            if not skel:
                # no numeric anchor -> needs reclustering
                positive_by_skel['__noanchor__'].append({**record, 'normalized': normalized})
                continue
            positive_by_skel[skel].append({**record, 'normalized': normalized})

    families: list[PatternFamily] = []

    # Positive families per skeleton
    for skel, records in positive_by_skel.items():
        if not records:
            continue
        lane = 'positive' if skel != '__noanchor__' else 'needs_reclustering'
        ia_dist: dict[str, float] = defaultdict(float)
        for r in records:
            ia_dist[r['ia_value']] += r['share']
        total = sum(ia_dist.values())
        fam = PatternFamily(
            family_id=f'pf_{_hash_skeleton(skel)}' if lane == 'positive' else 'pf_noanchor',
            lane=lane,
            skeleton=skel if lane == 'positive' else '',
            variable=var_name,
            source_cluster_ids=sorted({r['ia_value'] for r in records}),
            ia_value_distribution={k: int(v) for k, v in ia_dist.items()},
            evidence_windows=[r['window'] for r in records],
            negative_windows=[],
            total_count=int(total),
            purity_score=1.0 if lane == 'positive' else 0.0,
        )
        families.append(fam)

    # Multidim lane (single family)
    if multidim_buf:
        ia_dist: dict[str, float] = defaultdict(float)
        for r in multidim_buf:
            ia_dist[r['ia_value']] += r['share']
        total = sum(ia_dist.values())
        fam = PatternFamily(
            family_id='pf_multidim',
            lane='multidim',
            skeleton='',
            variable=var_name,
            source_cluster_ids=sorted({r['ia_value'] for r in multidim_buf}),
            ia_value_distribution={k: int(v) for k, v in ia_dist.items()},
            evidence_windows=[r['window'] for r in multidim_buf],
            negative_windows=[],
            total_count=int(total),
            purity_score=1.0,
        )
        families.append(fam)

    # Negative lane (single family)
    if negative_buf:
        ia_dist: dict[str, float] = defaultdict(float)
        for r in negative_buf:
            ia_dist[r['ia_value']] += r['share']
        total = sum(ia_dist.values())
        fam = PatternFamily(
            family_id='pf_negative',
            lane='negative',
            skeleton='',
            variable=var_name,
            source_cluster_ids=sorted({r['ia_value'] for r in negative_buf}),
            ia_value_distribution={k: int(v) for k, v in ia_dist.items()},
            evidence_windows=[],
            negative_windows=[r['window'] for r in negative_buf],
            total_count=int(total),
            purity_score=1.0,
        )
        families.append(fam)

    # Sort: positive first by count desc, then multidim, then negative, then needs_reclustering
    lane_order = {'positive': 0, 'multidim': 1, 'negative': 2, 'needs_reclustering': 3}
    families.sort(key=lambda f: (lane_order[f.lane], -f.total_count))

    return [f.to_dict() for f in families]


# ---------------------------------------------------------------------------
# Regex emission — template-based per GPT-5 verdict
# ---------------------------------------------------------------------------

# Surface patterns for subject classes (what we anchor to in the raw text).
# These are case-insensitive; negatives are excluded separately.
_SUBJECT_SURFACE = {
    'POLIPO_SUBJ': r'p[óo]lip(?:o|os|oide|oides)|p[óo]lip[óo]id[eo]s?|s[ée]ss(?:il|eis)|pedicul[ao]d[oa]s?',
    'LESAO_SUBJ': r'les[ãa]o|les[õo]es|plana|elevada|neoplas[iy]a',
    'CERCA': r'cerca\s+de|aproximadamente|aprox\.?',
    'PLUSMINUS': r'\+\s*/?\s*-',
}

_UNIT_SURFACE = {
    'mm': r'mm',
    'cm': r'cm',
}


def _dominant_anchors(skeleton: str) -> list[str]:
    """Return anchor class tokens present in skeleton, ordered by specificity.

    POLIPO_SUBJ is most specific; LESAO_SUBJ next; CERCA/PLUSMINUS last.
    """
    order = ['POLIPO_SUBJ', 'LESAO_SUBJ', 'CERCA', 'PLUSMINUS']
    present = [c for c in order if c in skeleton]
    return present


def _negative_subject_alternation(var_name: str) -> str:
    """Build a regex alternation of negative subjects to be excluded."""
    if 'polipo' in var_name.lower() or 'tamanho' in var_name.lower():
        negs = POLIPO_NEGATIVE_SUBJECTS
    else:
        negs = set()
    # strip accents for cleaner regex; matching will be accent-insensitive via IGNORECASE
    # but Python regex doesn't have accent-insensitive — we just match the stripped forms
    stripped = sorted({_strip_accents(n) for n in negs if n})
    if not stripped:
        return ''
    # Word-boundary wrapped alternation: prevents false positives like
    # 'pediculo' matching inside 'pediculado' (polipo subject) or
    # 'diverticulo' matching inside 'diverticulose'.
    alt = r'|'.join(re.escape(s) for s in stripped)
    return rf'\b(?:{alt})\b'


def emit_regex_from_family(family: dict, unit: str | None = 'mm') -> dict:
    """Build a template-based regex ProposedChange payload from a family.

    Returns a dict with keys:
      - regex_skeleton: str | None (None for non-positive lanes)
      - required_subject_classes: list[str]
      - forbidden_subject_classes: list[str]   (surface patterns to reject)
      - max_token_distance: int
      - description: str
      - risk_level: 'low' | 'medium' | 'high'
    """
    lane = family.get('lane')
    skeleton = family.get('skeleton', '')
    var_name = family.get('variable', '')

    if lane != 'positive' or not skeleton:
        return {
            'regex_skeleton': None,
            'required_subject_classes': [],
            'forbidden_subject_classes': [],
            'max_token_distance': 0,
            'description': f'No regex emitted — lane={lane}',
            'risk_level': 'high',
        }

    anchors = _dominant_anchors(skeleton)
    unit_pat = _UNIT_SURFACE.get(unit or 'mm', re.escape(unit or 'mm'))
    num_pat = r'(\d+(?:[.,]\d+)?)'
    # How many characters between anchor and number
    max_chars = 60

    if 'POLIPO_SUBJ' in anchors:
        primary_anchor = _SUBJECT_SURFACE['POLIPO_SUBJ']
        regex = rf'(?:{primary_anchor})[^.\n]{{0,{max_chars}}}?{num_pat}\s*{unit_pat}\b'
        required = ['POLIPO_SUBJ']
    elif 'LESAO_SUBJ' in anchors:
        primary_anchor = _SUBJECT_SURFACE['LESAO_SUBJ']
        regex = rf'(?:{primary_anchor})[^.\n]{{0,{max_chars}}}?{num_pat}\s*{unit_pat}\b'
        required = ['LESAO_SUBJ']
    elif 'CERCA' in anchors:
        primary_anchor = _SUBJECT_SURFACE['CERCA']
        regex = rf'(?:{primary_anchor})\s*{num_pat}\s*{unit_pat}\b'
        required = ['CERCA']
    elif 'PLUSMINUS' in anchors:
        primary_anchor = _SUBJECT_SURFACE['PLUSMINUS']
        regex = rf'(?:{primary_anchor})\s*{num_pat}\s*{unit_pat}\b'
        required = ['PLUSMINUS']
    else:
        # No anchor class available — skeleton has no recognisable subject.
        return {
            'regex_skeleton': None,
            'required_subject_classes': [],
            'forbidden_subject_classes': [],
            'max_token_distance': 0,
            'description': f'No regex emitted — skeleton has no subject anchor: {skeleton!r}',
            'risk_level': 'high',
        }

    forbidden_pat = _negative_subject_alternation(var_name)
    forbidden_list = [forbidden_pat] if forbidden_pat else []

    return {
        'regex_skeleton': regex,
        'required_subject_classes': required,
        'forbidden_subject_classes': forbidden_list,
        'max_token_distance': max_chars,
        'description': (
            f'Template-based regex for family {family.get("family_id")} '
            f'(skeleton={skeleton!r}, ia_values={sorted(family.get("ia_values", []))})'
        ),
        'risk_level': 'medium',
    }


# ---------------------------------------------------------------------------
# PatchCard generator from families
# ---------------------------------------------------------------------------

def _family_card_id(family: dict, seed: int) -> str:
    raw = f'{family.get("variable")}:{family.get("family_id")}:{seed}'
    return f'pc_{hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]}'


def generate_patch_cards_from_families(
    families: list[dict],
    corpus_df,
    var_name: str,
    unit: str | None = 'mm',
    seed: int = 42,
    min_family_count: int = 10,
    skip_lanes: tuple[str, ...] = ('negative', 'needs_reclustering', 'multidim'),
    force_trivial_regex: bool = False,
) -> list[PatchCard]:
    """Generate PatchCards, one per positive family with a usable regex.

    Non-positive lanes (negative, multidim, needs_reclustering) are skipped by
    default — they produce cards only for observability purposes and have no
    usable regex for the overlay.

    `force_trivial_regex` — v2.0.1 ablation mode per GPT-5 verdict. When True,
    replaces the family-specific regex with a trivial `(\\d+(?:[.,]\\d+)?)\\s*UNIT`
    and drops all forbidden subjects. Used to measure whether the v2.0 gain
    comes from family-specific regexes/forbidden or just from the cleaner
    eval pipeline (classifier-based miss assignment + df as eval_corpus).
    """
    trivial_unit = re.escape(unit or 'mm')
    trivial_regex = rf'(\d+(?:[.,]\d+)?)\s*{trivial_unit}\b'
    value_col = f'{var_name}__value'
    ia_col = f'{var_name}__ia'
    cards: list[PatchCard] = []

    # Pre-compute: classify each L1-miss row to a family_id. This replaces the
    # old ia_value-based assignment which over-claimed rows across families
    # sharing the same ia_value.
    # Also track (ia_value -> {assigned_fid: count}) so we can compute
    # per-family coverage_rate and fragmentation per GPT-5 v2.0.1 verdict:
    # coverage_rate = rows_claimed / rows_in_source_cluster (with ia_value in family)
    # fragmentation = distribution of rows_in_source_cluster across OTHER fids
    miss_family_map: dict[str, list[int]] = defaultdict(list)  # family_id -> row indices
    ia_fid_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if value_col in corpus_df.columns and ia_col in corpus_df.columns:
        null_mask = corpus_df[value_col].isna()
        ia_mask = corpus_df[ia_col].notna()
        miss_df = corpus_df[null_mask & ia_mask]
        for idx, row in miss_df.iterrows():
            text = str(row.get('texto_laudo', '') or '')
            ia_v = row.get(ia_col)
            _, fid = classify_text_to_family(text, ia_v, var_name, unit)
            miss_family_map[fid].append(idx)
            ia_fid_counts[str(ia_v)][fid] += 1

    for fam in families:
        if fam.get('lane') in skip_lanes:
            continue
        if fam.get('total_count', 0) < min_family_count:
            continue

        emit = emit_regex_from_family(fam, unit=unit)
        if not emit['regex_skeleton']:
            continue
        if force_trivial_regex:
            # Ablation baseline: replace the family-specific template with a
            # trivial number-near-unit regex and drop forbidden subjects.
            emit = dict(emit)
            emit['regex_skeleton'] = trivial_regex
            emit['forbidden_subject_classes'] = []
            emit['description'] = (
                f'[BASELINE_ABLATION] Trivial regex for family {fam.get("family_id")}'
            )

        fam_id = fam.get('family_id')
        row_idxs = miss_family_map.get(fam_id, [])
        if row_idxs:
            miss_frame = corpus_df.loc[row_idxs]
        else:
            miss_frame = corpus_df.iloc[0:0]

        # Compute coverage + fragmentation on the family's own ia_values.
        fam_ia_values = {str(v) for v in fam.get('ia_values', [])}
        source_cluster_total = 0
        claimed_here = 0
        fragmentation_counts: dict[str, int] = defaultdict(int)
        for ia_v, fid_counts in ia_fid_counts.items():
            if ia_v not in fam_ia_values:
                continue
            for fid, n in fid_counts.items():
                source_cluster_total += n
                if fid == fam_id:
                    claimed_here += n
                else:
                    fragmentation_counts[fid] += n
        coverage_rate = (
            claimed_here / source_cluster_total if source_cluster_total > 0 else 0.0
        )
        fragmentation = {
            fid: n for fid, n in sorted(
                fragmentation_counts.items(), key=lambda kv: -kv[1]
            )
        }

        miss_rows: list[dict] = miss_frame.to_dict(orient='records')
        # Deterministic 70/30 split
        import random as _r
        rng = _r.Random(seed)
        shuffled = list(miss_rows)
        rng.shuffle(shuffled)
        split_at = int(len(shuffled) * 0.7)
        discovery, eval_set = shuffled[:split_at], shuffled[split_at:]

        site_distribution: dict[str, int] = {}
        if not miss_frame.empty and 'clinica' in miss_frame.columns:
            vc = miss_frame['clinica'].value_counts()
            site_distribution = {k: int(v) for k, v in vc.items()}

        proposed = ProposedChange(
            change_type='new_regex',
            description=emit['description'],
            regex_skeleton=emit['regex_skeleton'],
            capture_map={'group_1': 'value'},
            target_scope=['descricao', 'conclusao'],
            precedence_hint=None,
            near_miss_patterns=emit['forbidden_subject_classes'],
            risk_level=emit['risk_level'],
        )

        # Enrich the family dict with v2.0.1 coverage metrics so downstream
        # reports surface them without having to recompute.
        fam_enriched = dict(fam)
        fam_enriched['family_coverage_rate'] = round(coverage_rate, 4)
        fam_enriched['family_coverage_detail'] = {
            'source_cluster_total': source_cluster_total,
            'claimed_here': claimed_here,
        }
        fam_enriched['family_fragmentation'] = fragmentation

        card = PatchCard(
            card_id=_family_card_id(fam, seed),
            variable=var_name,
            source_cluster=fam_enriched,
            proposed_change=proposed,
            evidence_discovery=discovery,
            evidence_eval=eval_set,
            hard_negatives=[],  # filled separately if needed
            site_distribution=site_distribution,
            impact_estimate=fam.get('total_count', 0),
            plausibility_tag='plausible',
            support_tag='coherent_cluster' if fam.get('total_count', 0) >= min_family_count else 'low_support',
            sandbox_result=None,
            human_decision=None,
            overlaps_with=[],
        )
        cards.append(card)

    # overlaps: cards sharing required_subject_classes AND similar skeletons
    # v2.0: simple signal — cards with identical regex_skeleton get cross-ref
    by_regex: dict[str, list[int]] = defaultdict(list)
    for idx, c in enumerate(cards):
        by_regex[c.proposed_change.regex_skeleton].append(idx)
    for regex, idxs in by_regex.items():
        if len(idxs) > 1:
            for i in idxs:
                others = [cards[j].card_id for j in idxs if j != i]
                cards[i].overlaps_with.extend(others)

    return cards
