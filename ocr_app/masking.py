"""
개인정보 마스킹 모듈
────────────────────────────────────────────────────
OCR로 추출된 텍스트에서 민감 정보를 자동 감지·마스킹하여
공개용(마스킹) 파일 생성에 사용합니다.

마스킹 규칙
  주민등록번호  : 뒷 7자리 전부 *  (900101-1234567 → 900101-*******)
  전화/휴대폰   : 중간 자리 ****    (010-1234-5678  → 010-****-5678)
  계좌번호      : 앞 3·뒤 4만 표시  (110-12345-67890 → 110-*****-7890)
  자유 텍스트   : 위 세 패턴을 내용으로 감지하여 자동 마스킹
"""
from __future__ import annotations

import re

# ──────────────────────────────────────────────────────────
# 민감 필드 키워드 → 마스킹 유형
# ──────────────────────────────────────────────────────────
SENSITIVE_KEYWORDS: dict[str, str] = {
    "주민등록번호": "resident_id",
    "주민번호":     "resident_id",
    "주민":         "resident_id",
    "rrn":          "resident_id",
    "계좌번호":     "account",
    "계좌":         "account",
    "account":      "account",
    "전화번호":     "phone",
    "전화":         "phone",
    "휴대폰":       "phone",
    "핸드폰":       "phone",
    "연락처":       "phone",
    "팩스":         "phone",
    "phone":        "phone",
    "mobile":       "phone",
}

# ──────────────────────────────────────────────────────────
# 정규식 패턴
# ──────────────────────────────────────────────────────────
_RRN_RE     = re.compile(r'\b(\d{6})-(\d{7})\b')
_PHONE_RE   = re.compile(r'\b(\d{2,3})-(\d{3,4})-(\d{4})\b')
_ACCOUNT_RE = re.compile(r'\b(\d{3,6})-(\d{2,6})-(\d{4,10})\b')


# ──────────────────────────────────────────────────────────
# 단일 값 마스킹
# ──────────────────────────────────────────────────────────

def mask_resident_id(value: str) -> str:
    """주민등록번호 뒷자리 마스킹: 900101-1234567 → 900101-*******"""
    return _RRN_RE.sub(r'\1-*******', value)


def mask_phone(value: str) -> str:
    """전화번호 중간자리 마스킹: 010-1234-5678 → 010-****-5678"""
    return _PHONE_RE.sub(r'\1-****-\3', value)


def mask_account(value: str) -> str:
    """계좌번호 마스킹: 앞 3자리 + *** + 뒤 4자리만 표시"""
    def _replace(m: re.Match[str]) -> str:
        front = m.group(1)
        mid   = '*' * len(m.group(2))
        back  = m.group(3)[-4:].rjust(len(m.group(3)), '*')
        return f"{front}-{mid}-{back}"
    replaced = _ACCOUNT_RE.sub(_replace, value)
    if replaced != value:
        return replaced
    # 하이픈 없는 연속 숫자 (10자리 이상)
    digits = re.sub(r'\s', '', value)
    if re.fullmatch(r'\d{10,20}', digits):
        return digits[:3] + '*' * (len(digits) - 7) + digits[-4:]
    return value


def mask_free_text(text: str) -> str:
    """자유 텍스트 내 민감 패턴을 모두 자동 마스킹"""
    text = _RRN_RE.sub(r'\1-*******', text)
    text = _PHONE_RE.sub(r'\1-****-\3', text)
    text = _ACCOUNT_RE.sub(
        lambda m: f"{m.group(1)}-{'*' * len(m.group(2))}-{'*' * (len(m.group(3)) - 4) + m.group(3)[-4:]}",
        text,
    )
    return text


def _detect_type(field_name: str) -> str | None:
    """필드 이름에서 민감도 유형 감지. 없으면 None."""
    lower = field_name.lower()
    for keyword, kind in SENSITIVE_KEYWORDS.items():
        if keyword in lower:
            return kind
    return None


def mask_value(field_name: str, value: str) -> str:
    """필드 이름과 값을 보고 적절히 마스킹하여 반환."""
    if not value or not value.strip():
        return value
    kind = _detect_type(field_name)
    if kind == "resident_id":
        return mask_resident_id(value)
    if kind == "phone":
        return mask_phone(value)
    if kind == "account":
        return mask_account(value)
    # 필드명으로 감지 못한 경우: 내용 기반 자동 마스킹
    return mask_free_text(value)


# ──────────────────────────────────────────────────────────
# payload 전체 마스킹
# ──────────────────────────────────────────────────────────

def _mask_regions(regions: list[object]) -> list[object]:
    masked = []
    for region in regions:
        if isinstance(region, dict):
            r = dict(region)
            r["text"] = mask_free_text(str(r.get("text", "")))
            cands = r.get("candidates", [])
            if isinstance(cands, list):
                r["candidates"] = [mask_free_text(str(c)) for c in cands]
            masked.append(r)
        else:
            masked.append(region)
    return masked


def _mask_recognition(recognition: object) -> object:
    if isinstance(recognition, dict):
        rec = dict(recognition)
        regions = rec.get("regions", [])
        if isinstance(regions, list):
            rec["regions"] = _mask_regions(regions)
        return rec
    if isinstance(recognition, list):
        result = []
        for doc in recognition:
            if isinstance(doc, dict):
                d = dict(doc)
                regions = d.get("regions", [])
                if isinstance(regions, list):
                    d["regions"] = _mask_regions(regions)
                result.append(d)
            else:
                result.append(doc)
        return result
    return recognition


def create_public_payload(payload: dict[str, object]) -> dict[str, object]:
    """
    전체 payload에서 민감 정보를 마스킹한 공개용 payload 반환.

    - values  : 필드명 키워드 + 내용 패턴으로 마스킹
    - regions : 자유 텍스트 내 패턴 마스킹
    """
    public: dict[str, object] = {
        "session_id": payload.get("session_id", ""),
        "form_id":    payload.get("form_id", ""),
        "_file_type": "public",
        "_note": (
            "공개용 파일 — 주민등록번호 뒷자리·전화번호 중간자리·"
            "계좌번호 일부가 * 처리되었습니다."
        ),
    }

    # values 마스킹
    raw_values = payload.get("values", {})
    if isinstance(raw_values, dict):
        public["values"] = {
            fn: mask_value(fn, str(v) if v is not None else "")
            for fn, v in raw_values.items()
        }
    else:
        public["values"] = raw_values

    # recognition 마스킹
    public["recognition"] = _mask_recognition(payload.get("recognition", {}))

    return public
