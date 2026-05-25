from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def encrypt_payload(payload: dict[str, object], birthdate: str, destination: Path) -> Path:
    """AES-256-CBC (openssl pbkdf2)로 payload를 암호화하여 저장."""
    clean_birthdate = "".join(c for c in birthdate if c.isdigit())
    if not clean_birthdate:
        raise ValueError("생년월일은 숫자를 포함해야 합니다.")
    if shutil.which("openssl") is None:
        raise RuntimeError("openssl 명령을 찾을 수 없어 저장 암호화를 수행할 수 없습니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(".json")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        subprocess.run(
            [
                "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
                "-in",  str(temp_path),
                "-out", str(destination),
                "-pass", "stdin",
            ],
            check=True,
            capture_output=True,
            text=True,
            input=f"{clean_birthdate}\n",
        )
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return destination


def save_payload(payload: dict[str, object], destination: Path) -> Path:
    """payload를 평문 JSON으로 저장."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def save_two_files(
    payload: dict[str, object],
    birthdate: str,
    storage_dir: Path,
    session_id: str,
    form_id: str,
) -> tuple[Path, Path, bool]:
    """
    공개용(마스킹) 파일과 암호화(전체) 파일 두 개를 저장.

    Returns:
        (public_path, private_path, encrypted)
        encrypted: True면 openssl로 암호화됨, False면 평문 JSON 폴백
    """
    from .masking import create_public_payload

    storage_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 공개용 파일 (마스킹 JSON) ──────────────────────
    public_payload = create_public_payload(payload)
    public_path = storage_dir / f"{session_id}_{form_id}_공개용.json"
    save_payload(public_payload, public_path)

    # ── 2. 전체 파일 (암호화) ──────────────────────────────
    full_payload: dict[str, object] = dict(payload)
    full_payload["_file_type"] = "private"
    full_payload["_note"] = (
        "민원인 생년월일(6자리) 비밀번호로 AES-256-CBC 암호화된 전체 정보 파일입니다. "
        "복호화: openssl enc -d -aes-256-cbc -pbkdf2 -in <파일명>.enc -out out.json"
    )

    enc_path = storage_dir / f"{session_id}_{form_id}_암호화.enc"
    try:
        encrypt_payload(full_payload, birthdate, enc_path)
        return public_path, enc_path, True
    except RuntimeError:
        # openssl 없으면 일반 JSON으로 저장 (경고 포함)
        fallback_path = storage_dir / f"{session_id}_{form_id}_전체(미암호화).json"
        full_payload["_encryption_warning"] = (
            "openssl이 설치되지 않아 암호화 없이 저장되었습니다. "
            "운영 환경에서는 반드시 openssl을 설치하여 암호화를 활성화하세요."
        )
        save_payload(full_payload, fallback_path)
        return public_path, fallback_path, False
