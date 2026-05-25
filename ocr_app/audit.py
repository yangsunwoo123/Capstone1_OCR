from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import DB_PATH, FORM_ASSET_DIR, REPORT_DIR, STORAGE_DIR, UPLOAD_DIR, TrainingConfig
from .data import inspect_many_handwrite_dataset
from .forms import FormRepository
from .training import build_training_plan_payload


def run_project_audit(output_path: Path | None = None) -> Path:
    output_path = output_path or REPORT_DIR / "project_audit.json"
    repository = FormRepository(DB_PATH)
    repository.initialize()
    forms = repository.list_forms()
    dataset_report = inspect_many_handwrite_dataset()
    general_payload, _, _ = build_training_plan_payload(
        dataset_name="many_handwrite",
        stage_name="phase2_general_finetune",
        config=TrainingConfig(),
    )
    elderly_payload, _, _ = build_training_plan_payload(
        dataset_name="public_old",
        stage_name="phase3_elderly_finetune",
        config=TrainingConfig(sample_limit=120),
        reference_train_crop_count=general_payload["crop_split"]["train"],
    )
    payload = {
        "status": "partial",
        "project_usage": {
            "forms_seeded": len(forms) > 0,
            "upload_dir_ready": UPLOAD_DIR.exists(),
            "storage_dir_ready": STORAGE_DIR.exists(),
            "form_asset_dir_ready": FORM_ASSET_DIR.exists(),
            "encryption_tool_available": shutil.which("openssl") is not None,
        },
        "dataset": {
            "images": dataset_report["images"],
            "paired": dataset_report["paired"],
            "resolution_common": dataset_report["resolution_common"],
        },
        "training": {
            "phase2_general": {
                "status": general_payload["status"],
                "execution_mode": general_payload["execution_mode"],
                "document_split": general_payload["document_split"],
                "crop_split": general_payload["crop_split"],
                "notes": general_payload["notes"],
            },
            "phase3_elderly": {
                "status": elderly_payload["status"],
                "execution_mode": elderly_payload["execution_mode"],
                "document_split": elderly_payload["document_split"],
                "crop_split": elderly_payload["crop_split"],
                "reference_phase2_train_crop_count": elderly_payload["reference_phase2_train_crop_count"],
                "notes": elderly_payload["notes"],
            },
        },
        "blocking_gaps": [
            "선택한 양식의 템플릿 좌표를 OCR detection 단계에 직접 연결해 손글씨 칸만 분리하는 기능은 아직 없습니다.",
            "fine-tuning CLI는 train/val/test manifest를 생성하지만 실제 trainer 실행과 checkpoint 학습은 아직 없습니다.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
