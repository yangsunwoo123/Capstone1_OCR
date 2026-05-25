from __future__ import annotations


def levenshtein_distance(source: list[str], target: list[str]) -> int:
    if not source:
        return len(target)
    if not target:
        return len(source)
    rows = len(source) + 1
    cols = len(target) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if source[i - 1] == target[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[-1][-1]


def cer(reference: str, prediction: str) -> float:
    ref_units = list(reference)
    pred_units = list(prediction)
    if not ref_units:
        return 0.0 if not pred_units else 1.0
    return levenshtein_distance(ref_units, pred_units) / len(ref_units)


def wer(reference: str, prediction: str) -> float:
    ref_units = reference.split()
    pred_units = prediction.split()
    if not ref_units:
        return 0.0 if not pred_units else 1.0
    return levenshtein_distance(ref_units, pred_units) / len(ref_units)
