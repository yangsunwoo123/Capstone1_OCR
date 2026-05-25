from __future__ import annotations

import json
import math
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .config import ARTIFACT_DIR, ModelConfig, REPORT_DIR, TrainingConfig
from .data import CropSample, DocumentRecord, deterministic_split, iter_crop_samples, load_many_handwrite_records, load_public_old_records
from .metrics import cer, wer
from .ocr_engine import KoTrOCREngine, MissingModelDependencyError


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _sample_many_handwrite(sample_size: int, seed: int) -> list[CropSample]:
    population = list(iter_crop_samples(load_many_handwrite_records()))
    random.Random(seed).shuffle(population)
    return population[: min(sample_size, len(population))]


def run_zero_shot_report(
    sample_size: int = 32,
    seed: int = 7,
    output_path: Path | None = None,
    model_config: ModelConfig | None = None,
) -> Path:
    output_path = output_path or REPORT_DIR / "phase1_zero_shot.json"
    engine = KoTrOCREngine(model_config or ModelConfig())
    samples = _sample_many_handwrite(sample_size=sample_size, seed=seed)
    results: list[dict[str, object]] = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    status = "completed"
    error_message = ""
    for sample in samples:
        try:
            prediction = engine.predict_crop(sample.crop())
        except MissingModelDependencyError as error:
            status = "blocked"
            error_message = str(error)
            break
        sample_cer = cer(sample.text, prediction.text)
        sample_wer = wer(sample.text, prediction.text)
        cer_values.append(sample_cer)
        wer_values.append(sample_wer)
        results.append(
            {
                "image": str(sample.image_path),
                "document_stem": sample.document_stem,
                "bbox": sample.bbox.to_dict(),
                "reference": sample.text,
                "prediction": prediction.text,
                "candidates": prediction.candidates,
                "confidence": prediction.confidence,
                "cer": sample_cer,
                "wer": sample_wer,
            }
        )
    payload: dict[str, object] = {
        "phase": "phase1_zero_shot",
        "model_name": (model_config or ModelConfig()).model_name,
        "sample_size": len(samples),
        "seed": seed,
        "status": status,
        "error": error_message,
        "metrics": {
            "cer": sum(cer_values) / len(cer_values) if cer_values else None,
            "wer": sum(wer_values) / len(wer_values) if wer_values else None,
        },
        "results": results,
    }
    return _write_json(output_path, payload)


def _grouped_records(dataset_name: str) -> list[DocumentRecord]:
    if dataset_name == "many_handwrite":
        return load_many_handwrite_records()
    if dataset_name == "public_old":
        return load_public_old_records()
    raise ValueError(f"Unknown dataset: {dataset_name}")


def _augment_image(image: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    rotated = image.rotate(rng.uniform(-3.0, 3.0), expand=True, fillcolor="white")
    width, height = rotated.size
    offset_x = rng.randint(-6, 6)
    offset_y = rng.randint(-6, 6)
    geometric = rotated.transform(
        rotated.size,
        Image.AFFINE,
        (1, 0, offset_x, 0, 1, offset_y),
        fillcolor="white",
    )
    mesh = []
    step = max(16, min(width, height) // 6)
    for left in range(0, width, step):
        for top in range(0, height, step):
            right = min(width, left + step)
            bottom = min(height, top + step)
            quad = (
                left + rng.randint(-3, 3),
                top + rng.randint(-3, 3),
                right + rng.randint(-3, 3),
                top + rng.randint(-3, 3),
                right + rng.randint(-3, 3),
                bottom + rng.randint(-3, 3),
                left + rng.randint(-3, 3),
                bottom + rng.randint(-3, 3),
            )
            mesh.append(((left, top, right, bottom), quad))
    elastic = geometric.transform(geometric.size, Image.MESH, mesh, fillcolor="white")
    array = np.asarray(elastic).astype(np.float32)
    noise = np.random.default_rng(seed).normal(0, 7, size=array.shape)
    noised = np.clip(array + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(noised)


def _prepare_training_output(dataset_name: str, stage_name: str) -> Path:
    output_dir = ARTIFACT_DIR / stage_name / dataset_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _serialize_record(record: DocumentRecord) -> dict[str, object]:
    return {
        "dataset_name": record.dataset_name,
        "document_stem": record.stem,
        "image_path": str(record.image_path),
        "label_path": str(record.label_path),
        "width": record.width,
        "height": record.height,
        "writer_group": record.writer_group,
    }


def _serialize_crop_sample(sample: CropSample) -> dict[str, object]:
    return {
        "dataset_name": sample.dataset_name,
        "document_stem": sample.document_stem,
        "image_path": str(sample.image_path),
        "label_path": str(sample.label_path),
        "text": sample.text,
        "bbox": sample.bbox.to_dict(),
        "writer_group": sample.writer_group,
    }


def _reference_train_crop_count(resume_from: str | None, fallback: int | None = None) -> int | None:
    if fallback is not None:
        return fallback
    if not resume_from:
        return None
    reference_path = Path(resume_from)
    if not reference_path.exists():
        return None
    payload = json.loads(reference_path.read_text(encoding="utf-8"))
    crop_split = payload.get("crop_split", {})
    if isinstance(crop_split, dict):
        train_count = crop_split.get("train")
        if isinstance(train_count, int):
            return train_count
    return None


def build_training_plan_payload(
    dataset_name: str,
    stage_name: str,
    config: TrainingConfig,
    augment: bool = False,
    resume_from: str | None = None,
    reference_train_crop_count: int | None = None,
) -> tuple[dict[str, Any], dict[str, list[DocumentRecord]], dict[str, list[CropSample]]]:
    records = _grouped_records(dataset_name)
    split_docs = deterministic_split(records)
    split_crops = {
        split_name: list(iter_crop_samples(items))
        for split_name, items in split_docs.items()
    }
    notes: list[str] = [
        "train/val/test는 document stem 정렬 후 80/10/10 비율로 자동 분리됩니다.",
        "crop manifest는 document split 이후 생성되므로 동일 문서의 bbox가 서로 다른 split에 섞이지 않습니다.",
        "test split은 별도 폴더로 저장되고 학습에는 사용되지 않습니다.",
    ]
    if config.sample_limit is not None:
        split_crops["train"] = split_crops["train"][: min(config.sample_limit, len(split_crops["train"]))]
        notes.append(f"train split에는 sample_limit={config.sample_limit}가 적용됩니다.")
    reference_count = _reference_train_crop_count(resume_from, fallback=reference_train_crop_count)
    if stage_name == "phase3_elderly_finetune" and reference_count is not None and split_crops["train"]:
        max_allowed = max(1, reference_count - 1)
        if len(split_crops["train"]) >= reference_count:
            split_crops["train"] = split_crops["train"][:max_allowed]
            notes.append(
                "PROJECT.md 요구사항에 맞춰 노인 손글씨 train crop 수를 일반 손글씨 Phase 2 train crop 수보다 작게 자동 제한했습니다."
            )
    payload: dict[str, Any] = {
        "phase": stage_name,
        "dataset_name": dataset_name,
        "status": "prepared" if KoTrOCREngine.dependencies_available() else "blocked",
        "execution_mode": "manifest_only",
        "blocked_reason": None
        if KoTrOCREngine.dependencies_available()
        else "torch/transformers가 없어 실제 학습 실행은 못 하고 split manifest와 학습 계획만 생성했습니다.",
        "training_config": asdict(config),
        "resume_from": resume_from,
        "augment": augment,
        "split_strategy": {
            "type": "document_stem_sorted",
            "train_ratio": 0.8,
            "val_ratio": 0.1,
            "test_ratio": 0.1,
        },
        "document_split": {split: len(items) for split, items in split_docs.items()},
        "crop_split": {split: len(items) for split, items in split_crops.items()},
        "reference_phase2_train_crop_count": reference_count,
        "notes": notes,
        "first_train_samples": [
            _serialize_crop_sample(sample)
            for sample in split_crops["train"][:10]
        ],
    }
    return payload, split_docs, split_crops


def _default_eval_limit(train_count: int) -> int:
    return max(2, min(16, max(2, math.ceil(train_count / 2))))


def _select_eval_subset(samples: list[CropSample], explicit_limit: int | None, train_count: int) -> list[CropSample]:
    if not samples:
        return []
    limit = explicit_limit if explicit_limit is not None else _default_eval_limit(train_count)
    return samples[: min(limit, len(samples))]


def _load_training_components(model_name: str) -> tuple[object, object, object, str]:
    if not KoTrOCREngine.dependencies_available():
        raise MissingModelDependencyError("torch/transformers가 설치되지 않아 학습을 시작할 수 없습니다.")
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    try:
        processor = TrOCRProcessor.from_pretrained(model_name, local_files_only=True)
        model = VisionEncoderDecoderModel.from_pretrained(model_name, local_files_only=True)
    except OSError as error:
        raise MissingModelDependencyError(
            "ddobokki/ko-trocr 모델 파일을 현재 환경에서 사용할 수 없습니다. 먼저 zero-shot으로 캐시를 준비하세요."
        ) from error
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    tokenizer = processor.tokenizer
    decoder_start = tokenizer.cls_token_id or tokenizer.bos_token_id
    eos_token = tokenizer.sep_token_id or tokenizer.eos_token_id
    if decoder_start is None or tokenizer.pad_token_id is None or eos_token is None:
        raise MissingModelDependencyError("TrOCR tokenizer special token 설정을 확인할 수 없습니다.")
    model.config.decoder_start_token_id = decoder_start
    model.config.pad_token_id = tokenizer.pad_token_id
    model.config.eos_token_id = eos_token
    model.float()
    model.to(device)
    return processor, model, torch, device


def _build_dataloader(samples: list[CropSample], processor: object, torch: object, batch_size: int, shuffle: bool, max_target_length: int) -> object:
    from torch.utils.data import DataLoader, Dataset

    class OCRCropDataset(Dataset):
        def __init__(self, crop_samples: list[CropSample]) -> None:
            self.samples = crop_samples

        def __len__(self) -> int:
            return len(self.samples)

        def __getitem__(self, index: int) -> dict[str, object]:
            sample = self.samples[index]
            image = sample.crop().convert("RGB")
            pixel_values = processor(images=image, return_tensors="pt").pixel_values.squeeze(0)
            labels = processor.tokenizer(
                sample.text,
                padding=False,
                truncation=True,
                max_length=max_target_length,
            ).input_ids
            return {
                "pixel_values": pixel_values,
                "labels": labels,
                "reference_text": sample.text,
                "crop_key": f"{sample.document_stem}__{sample.bbox.box_id:04d}",
            }

    def collate_fn(features: list[dict[str, object]]) -> dict[str, object]:
        pixel_values = torch.stack([feature["pixel_values"] for feature in features])
        labels = processor.tokenizer.pad(
            {"input_ids": [feature["labels"] for feature in features]},
            return_tensors="pt",
        ).input_ids
        labels[labels == processor.tokenizer.pad_token_id] = -100
        return {
            "pixel_values": pixel_values,
            "labels": labels,
            "reference_texts": [str(feature["reference_text"]) for feature in features],
            "crop_keys": [str(feature["crop_key"]) for feature in features],
        }

    return DataLoader(
        OCRCropDataset(samples),
        batch_size=max(1, batch_size),
        shuffle=shuffle,
        num_workers=0,
        collate_fn=collate_fn,
    )


def _tensor_batch(batch: dict[str, object], device: str) -> dict[str, object]:
    return {
        "pixel_values": batch["pixel_values"].to(device),
        "labels": batch["labels"].to(device),
    }


def _run_training_epoch(model: object, loader: object, optimizer: object, torch: object, device: str, grad_acc_steps: int) -> float:
    model.train()
    optimizer.zero_grad(set_to_none=True)
    total_loss = 0.0
    steps = 0
    for step_index, batch in enumerate(loader, start=1):
        outputs = model(**_tensor_batch(batch, device))
        loss = outputs.loss
        if not torch.isfinite(loss):
            raise RuntimeError(f"non-finite training loss at step {step_index}: {float(loss.item())}")
        total_loss += float(loss.item())
        steps += 1
        (loss / grad_acc_steps).backward()
        if step_index % grad_acc_steps == 0 or step_index == len(loader):
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
    return total_loss / max(steps, 1)


def _evaluate_loss(model: object, loader: object, torch: object, device: str) -> float:
    if len(loader) == 0:
        return 0.0
    model.eval()
    total_loss = 0.0
    steps = 0
    with torch.no_grad():
        for batch in loader:
            outputs = model(**_tensor_batch(batch, device))
            if not torch.isfinite(outputs.loss):
                raise RuntimeError(f"non-finite validation loss at step {steps + 1}: {float(outputs.loss.item())}")
            total_loss += float(outputs.loss.item())
            steps += 1
    return total_loss / max(steps, 1)


def _evaluate_generation(
    model: object,
    processor: object,
    loader: object,
    torch: object,
    device: str,
    max_target_length: int,
) -> tuple[dict[str, float], list[dict[str, object]]]:
    model.eval()
    prediction_rows: list[dict[str, object]] = []
    total_cer = 0.0
    total_wer = 0.0
    exact = 0
    total = 0
    with torch.no_grad():
        for batch in loader:
            generated = model.generate(
                batch["pixel_values"].to(device),
                max_length=max_target_length,
                num_beams=1,
            )
            predictions = processor.batch_decode(generated, skip_special_tokens=True)
            references = batch["reference_texts"]
            for crop_key, reference, prediction in zip(batch["crop_keys"], references, predictions, strict=True):
                sample_cer = cer(reference, prediction)
                sample_wer = wer(reference, prediction)
                total_cer += sample_cer
                total_wer += sample_wer
                exact += int(reference.strip() == prediction.strip())
                total += 1
                prediction_rows.append(
                    {
                        "crop_key": crop_key,
                        "reference": reference,
                        "prediction": prediction,
                        "cer": sample_cer,
                        "wer": sample_wer,
                        "exact_match": reference.strip() == prediction.strip(),
                    }
                )
    metrics = {
        "cer": total_cer / max(total, 1),
        "wer": total_wer / max(total, 1),
        "exact_match": exact / max(total, 1),
        "sample_count": total,
    }
    return metrics, prediction_rows


def _save_split_manifests(
    output_dir: Path,
    split_docs: dict[str, list[DocumentRecord]],
    split_crops: dict[str, list[CropSample]],
) -> dict[str, dict[str, str]]:
    manifests_dir = output_dir / "manifests"
    manifest_paths = {
        split_name: {
            "documents": str(
                _write_json(
                    manifests_dir / f"{split_name}_documents.json",
                    {"items": [_serialize_record(record) for record in docs]},
                )
            ),
            "crops": str(
                _write_jsonl(
                    manifests_dir / f"{split_name}_crops.jsonl",
                    [_serialize_crop_sample(sample) for sample in split_crops[split_name]],
                )
            ),
        }
        for split_name, docs in split_docs.items()
    }
    test_dir = output_dir / "test_dataset"
    _write_json(test_dir / "test_documents.json", {"items": [_serialize_record(record) for record in split_docs["test"]]})
    _write_jsonl(test_dir / "test_crops.jsonl", [_serialize_crop_sample(sample) for sample in split_crops["test"]])
    return manifest_paths


def _run_fine_tuning(
    output_dir: Path,
    config: TrainingConfig,
    split_crops: dict[str, list[CropSample]],
    model_name: str,
) -> dict[str, object]:
    processor, model, torch, device = _load_training_components(model_name)
    train_samples = split_crops["train"]
    val_samples = _select_eval_subset(split_crops["val"], config.val_sample_limit, len(train_samples))
    test_samples = _select_eval_subset(split_crops["test"], config.test_sample_limit, len(train_samples))
    if not train_samples:
        raise MissingModelDependencyError("학습에 사용할 train sample이 없습니다.")
    print(
        f"[training] device={device} train={len(train_samples)} val_eval={len(val_samples)} "
        f"test_eval={len(test_samples)} epochs={config.epochs}"
    )
    train_loader = _build_dataloader(train_samples, processor, torch, config.batch_size, True, config.max_target_length)
    val_loader = _build_dataloader(val_samples, processor, torch, config.batch_size, False, config.max_target_length)
    test_loader = _build_dataloader(test_samples, processor, torch, config.batch_size, False, config.max_target_length)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    history: list[dict[str, object]] = []
    best_checkpoint_dir = output_dir / "best_checkpoint"
    best_val_cer = float("inf")
    best_val_loss = float("inf")
    best_epoch = 0
    stale_epochs = 0
    for epoch in range(1, config.epochs + 1):
        print(f"[training] epoch {epoch}/{config.epochs} start")
        train_loss = _run_training_epoch(
            model=model,
            loader=train_loader,
            optimizer=optimizer,
            torch=torch,
            device=device,
            grad_acc_steps=max(1, config.gradient_accumulation_steps),
        )
        val_loss = _evaluate_loss(model, val_loader, torch, device)
        val_metrics, _ = _evaluate_generation(model, processor, val_loader, torch, device, config.max_target_length)
        epoch_row = {
            "epoch": epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "val_cer": val_metrics["cer"],
            "val_wer": val_metrics["wer"],
            "val_exact_match": val_metrics["exact_match"],
        }
        history.append(epoch_row)
        print(
            f"[training] epoch {epoch} train_loss={train_loss:.4f} "
            f"val_loss={val_loss:.4f} val_cer={val_metrics['cer']:.4f} "
            f"val_wer={val_metrics['wer']:.4f}"
        )
        val_cer = float(val_metrics["cer"])
        improved = val_cer < best_val_cer or (val_cer == best_val_cer and val_loss < best_val_loss)
        if improved:
            best_val_cer = val_cer
            best_val_loss = float(val_loss)
            best_epoch = epoch
            stale_epochs = 0
            model.save_pretrained(best_checkpoint_dir)
            processor.save_pretrained(best_checkpoint_dir)
            print(
                f"[training] new best checkpoint saved at epoch {epoch} "
                f"val_cer={best_val_cer:.4f} val_loss={best_val_loss:.4f}"
            )
        else:
            stale_epochs += 1
            if stale_epochs >= config.early_stopping_patience:
                print(f"[training] early stopping at epoch {epoch}")
                break
    from transformers import VisionEncoderDecoderModel, TrOCRProcessor

    model = VisionEncoderDecoderModel.from_pretrained(best_checkpoint_dir, local_files_only=True)
    processor = TrOCRProcessor.from_pretrained(best_checkpoint_dir, local_files_only=True)
    model.float()
    model.to(device)
    test_metrics, test_predictions = _evaluate_generation(model, processor, test_loader, torch, device, config.max_target_length)
    print(
        f"[training] test cer={test_metrics['cer']:.4f} "
        f"wer={test_metrics['wer']:.4f} exact_match={test_metrics['exact_match']:.4f}"
    )
    _write_json(output_dir / "metrics_history.json", {"epochs": history})
    _write_json(output_dir / "test_metrics.json", test_metrics)
    _write_jsonl(output_dir / "test_predictions.jsonl", test_predictions)
    return {
        "status": "completed",
        "execution_mode": "trained",
        "device": device,
        "used_splits": {
            "train": len(train_samples),
            "val": len(val_samples),
            "test": len(test_samples),
        },
        "best_epoch": best_epoch,
        "checkpoint_selection": "lower val_cer, then lower val_loss when val_cer is tied",
        "best_val_cer": best_val_cer,
        "best_val_loss": best_val_loss,
        "history_path": str(output_dir / "metrics_history.json"),
        "test_metrics_path": str(output_dir / "test_metrics.json"),
        "test_predictions_path": str(output_dir / "test_predictions.jsonl"),
        "best_checkpoint_dir": str(best_checkpoint_dir),
        "test_metrics": test_metrics,
    }


def run_training_plan(
    dataset_name: str,
    stage_name: str,
    config: TrainingConfig,
    augment: bool = False,
    resume_from: str | None = None,
) -> Path:
    payload, split_docs, split_crops = build_training_plan_payload(
        dataset_name=dataset_name,
        stage_name=stage_name,
        config=config,
        augment=augment,
        resume_from=resume_from,
    )
    output_dir = _prepare_training_output(dataset_name, stage_name)
    manifest_paths = _save_split_manifests(output_dir, split_docs, split_crops)
    payload["manifests"] = manifest_paths
    _write_json(
        output_dir / "split_summary.json",
        {
            "dataset_name": dataset_name,
            "document_split": payload["document_split"],
            "crop_split": payload["crop_split"],
            "notes": payload["notes"],
            "manifests": manifest_paths,
            "test_dataset_dir": str(output_dir / "test_dataset"),
        },
    )
    _write_json(output_dir / "training_plan.json", payload)
    if KoTrOCREngine.dependencies_available():
        try:
            training_result = _run_fine_tuning(output_dir, config, split_crops, ModelConfig().model_name)
            payload.update(training_result)
            payload["blocked_reason"] = None
        except MissingModelDependencyError as error:
            payload["status"] = "blocked"
            payload["execution_mode"] = "manifest_only"
            payload["blocked_reason"] = str(error)
    _write_json(output_dir / "training_plan.json", payload)
    return output_dir / "training_plan.json"


def run_development_plan(config: TrainingConfig) -> Path:
    samples = _sample_many_handwrite(sample_size=8, seed=11)
    preview_dir = ARTIFACT_DIR / "phase9_development" / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_files: list[str] = []
    for index, sample in enumerate(samples):
        augmented = _augment_image(sample.crop(), seed=index + 100)
        output_path = preview_dir / f"augmented_{index:02d}.png"
        augmented.save(output_path)
        preview_files.append(str(output_path))
    payload = {
        "phase": "phase9_development",
        "status": "prepared" if KoTrOCREngine.dependencies_available() else "blocked",
        "execution_mode": "manifest_only",
        "blocked_reason": None
        if KoTrOCREngine.dependencies_available()
        else "학습 의존성이 없어 augmentation preview와 재학습 계획만 생성했습니다.",
        "training_config": asdict(config),
        "augmentation_pipeline": [
            "geometric transformation",
            "elastic distortion (mesh warp approximation)",
            "noise injection",
        ],
        "preview_files": preview_files,
    }
    return _write_json(REPORT_DIR / "phase9_development.json", payload)
