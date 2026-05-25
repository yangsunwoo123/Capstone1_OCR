"""
문맥 기반 텍스트 교정 제안 모듈 (OpenAI GPT 기반)
────────────────────────────────────────────────────
악필·저신뢰 OCR 결과에 대해 최대 3개의 교정 후보를 제안합니다.

우선순위:
  1. OPENAI_API_KEY 환경변수 설정 시 → GPT-4o-mini (문맥 이해)
  2. 미설정 시                        → TrOCR 빔서치 후보 반환

표준 라이브러리만 사용 (추가 의존성 없음).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
_MODEL          = "gpt-4o-mini"
_MAX_TOKENS     = 200


def _build_prompt(
    text: str,
    field_name: str,
    candidates: list[str],
    context_texts: list[str],
    max_n: int,
) -> str:
    cand_str = " · ".join(candidates[:3]) if candidates else "없음"
    ctx_str  = " / ".join(context_texts[:5]) if context_texts else "없음"
    return (
        "당신은 한국어 행정 서류 손글씨 OCR 교정 전문가입니다.\n"
        "악필로 인해 글자가 잘못 인식되었을 수 있습니다.\n"
        "아래 정보를 참고하여 올바른 텍스트를 추측하세요.\n\n"
        f"필드명: {field_name or '알 수 없음'}\n"
        f"OCR 인식 결과(오류 가능성 있음): {text}\n"
        f"TrOCR 후보: {cand_str}\n"
        f"같은 서류의 다른 인식 텍스트(문맥): {ctx_str}\n\n"
        f"교정 후보 {max_n}개를 JSON 문자열 배열로만 응답하세요.\n"
        "설명 없이 배열만 출력하세요.\n"
        '예시: ["수원대학교", "수원대학원", "수원고등학교"]'
    )


def _call_openai(prompt: str, api_key: str) -> list[str]:
    """OpenAI Chat Completions API 호출. 실패 시 빈 리스트 반환."""
    body = json.dumps({
        "model":       _MODEL,
        "max_tokens":  _MAX_TOKENS,
        "temperature": 0.3,
        "messages": [
            {
                "role":    "system",
                "content": "당신은 한국어 손글씨 OCR 교정 전문가입니다. 요청된 형식으로만 답변합니다.",
            },
            {
                "role":    "user",
                "content": prompt,
            },
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        _OPENAI_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data["choices"][0]["message"]["content"].strip()
        # 코드블록 제거
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 3 else raw.replace("```", "")
            if raw.startswith("json"):
                raw = raw[4:].strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(s).strip() for s in parsed if str(s).strip()]
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError):
        pass
    return []


def get_suggestions(
    text: str,
    field_name: str = "",
    candidates: list[str] | None = None,
    context_texts: list[str] | None = None,
    max_suggestions: int = 3,
) -> tuple[list[str], str]:
    """
    저신뢰도 텍스트의 교정 후보를 반환한다.

    Args:
        text:            OCR 인식 결과 (오류 가능성 있음)
        field_name:      필드명 (문맥 힌트)
        candidates:      TrOCR 빔서치 후보 목록
        context_texts:   같은 서류의 다른 인식 텍스트 (문맥)
        max_suggestions: 최대 후보 수 (기본 3)

    Returns:
        (suggestions, source)
        source: "gpt" | "beam_search"
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    cands   = candidates or []
    ctx     = context_texts or []

    # ── OpenAI GPT 우선 ───────────────────────────────────
    if api_key:
        prompt  = _build_prompt(text, field_name, cands, ctx, max_suggestions)
        results = _call_openai(prompt, api_key)
        if results:
            return results[:max_suggestions], "gpt"

    # ── 폴백: TrOCR 빔서치 후보 ──────────────────────────
    fallback: list[str] = []
    if text:
        fallback.append(text)
    for c in cands:
        if c.strip() and c not in fallback:
            fallback.append(c)
    return fallback[:max_suggestions], "beam_search"
