from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def encrypt_payload(payload: dict[str, object], birthdate: str, destination: Path) -> Path:
    clean_birthdate = "".join(character for character in birthdate if character.isdigit())
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
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-salt",
                "-in",
                str(temp_path),
                "-out",
                str(destination),
                "-pass",
                "stdin",
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
