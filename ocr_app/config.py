from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
MANY_HANDWRITE_RAW_DIR = DATA_DIR / "many_handwrite" / "rawdata"
MANY_HANDWRITE_LABEL_DIR = DATA_DIR / "many_handwrite" / "label"
PUBLIC_OLD_RAW_DIR = DATA_DIR / "public_old" / "rawdata"
PUBLIC_OLD_LABEL_DIR = DATA_DIR / "public_old" / "label"
REPORT_DIR = ROOT_DIR / "reports"
ARTIFACT_DIR = ROOT_DIR / "artifacts"
UPLOAD_DIR = ROOT_DIR / "uploads"
STORAGE_DIR = ROOT_DIR / "storage"
DB_PATH = ROOT_DIR / "forms.db"
FORM_ASSET_DIR = ROOT_DIR / "form_assets"

def _find_form_images() -> list[Path]:
    import re
    import unicodedata
    if not DATA_DIR.exists():
        return []
    pattern = re.compile(r"^직불금[1-5]\.png$")
    found: list[Path] = []
    for entry in sorted(DATA_DIR.iterdir()):
        name_nfc = unicodedata.normalize("NFC", entry.name)
        if pattern.match(name_nfc):
            found.append(entry)
    return found

FORM_IMAGES = _find_form_images()
FINE_TUNED_CHECKPOINT_DIR = ARTIFACT_DIR / "phase2_general_finetune" / "many_handwrite" / "best_checkpoint"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FORM_TEMPLATE_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class ModelConfig:
    model_name: str = "ddobokki/ko-trocr"
    confidence_threshold: float = 0.5
    candidate_count: int = 3


@dataclass(slots=True)
class TrainingConfig:
    optimizer: str = "AdamW"
    learning_rate: float = 5e-6
    batch_size: int = 8
    epochs: int = 500
    early_stopping_patience: int = 10
    gradient_accumulation_steps: int = 1
    max_target_length: int = 64
    sample_limit: int | None = None
    val_sample_limit: int | None = None
    test_sample_limit: int | None = None


@dataclass(slots=True)
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000


def ensure_runtime_dirs() -> None:
    for path in (REPORT_DIR, ARTIFACT_DIR, UPLOAD_DIR, STORAGE_DIR, FORM_ASSET_DIR):
        path.mkdir(parents=True, exist_ok=True)
