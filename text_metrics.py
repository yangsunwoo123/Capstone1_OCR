from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _SPACE_RE.sub(" ", text.strip())
    return text


def _edit_distance(seq_a: list[str], seq_b: list[str]) -> int:
    if not seq_a:
        return len(seq_b)
    if not seq_b:
        return len(seq_a)

    previous = list(range(len(seq_b) + 1))
    for i, token_a in enumerate(seq_a, start=1):
        current = [i]
        for j, token_b in enumerate(seq_b, start=1):
            substitution = previous[j - 1] + (token_a != token_b)
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def char_error_rate(prediction: str, reference: str) -> float:
    prediction = normalize_text(prediction)
    reference = normalize_text(reference)
    if not reference:
        return 0.0 if not prediction else 1.0
    distance = _edit_distance(list(prediction), list(reference))
    return distance / max(len(reference), 1)


def word_error_rate(prediction: str, reference: str) -> float:
    prediction = normalize_text(prediction)
    reference = normalize_text(reference)
    pred_tokens = prediction.split() if prediction else []
    ref_tokens = reference.split() if reference else []
    if not ref_tokens:
        return 0.0 if not pred_tokens else 1.0
    distance = _edit_distance(pred_tokens, ref_tokens)
    return distance / max(len(ref_tokens), 1)


@dataclass(frozen=True)
class AggregateMetrics:
    cer: float
    wer: float
    exact_match: float
    sample_count: int


def aggregate_metrics(pairs: list[tuple[str, str]]) -> AggregateMetrics:
    if not pairs:
        return AggregateMetrics(cer=0.0, wer=0.0, exact_match=0.0, sample_count=0)

    total_cer_distance = 0
    total_cer_ref = 0
    total_wer_distance = 0
    total_wer_ref = 0
    exact = 0

    for prediction, reference in pairs:
        pred_norm = normalize_text(prediction)
        ref_norm = normalize_text(reference)
        if pred_norm == ref_norm:
            exact += 1

        total_cer_distance += _edit_distance(list(pred_norm), list(ref_norm))
        total_cer_ref += len(ref_norm)

        pred_words = pred_norm.split() if pred_norm else []
        ref_words = ref_norm.split() if ref_norm else []
        total_wer_distance += _edit_distance(pred_words, ref_words)
        total_wer_ref += len(ref_words)

    cer = total_cer_distance / total_cer_ref if total_cer_ref else 0.0
    wer = total_wer_distance / total_wer_ref if total_wer_ref else 0.0
    exact_match = exact / len(pairs)
    return AggregateMetrics(cer=cer, wer=wer, exact_match=exact_match, sample_count=len(pairs))
