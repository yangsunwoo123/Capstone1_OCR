from __future__ import annotations

import argparse
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ocr_app.audit import run_project_audit
from ocr_app.config import DB_PATH, REPORT_DIR, STORAGE_DIR, TrainingConfig, WebConfig, ensure_runtime_dirs
from ocr_app.data import inspect_many_handwrite_dataset, load_many_handwrite_records
from ocr_app.forms import FormRepository, build_prefill
from ocr_app.inference import RecognitionService
from ocr_app.storage import save_payload
from ocr_app.training import run_development_plan, run_training_plan
from ocr_app.web import run_server
from pipeline_dataset import crop_box, flatten_boxes, load_image, load_matched_pages, select_pages
from text_metrics import aggregate_metrics, char_error_rate, word_error_rate
from recognition_model import KoTrocrZeroShot
from report_writer import PredictionRow, dump_json, dump_jsonl, ensure_dir, write_report


def setup_logging(log_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)


def overlay_page(page_image: Image.Image, rows: list[PredictionRow], output_path: Path) -> None:
    image = page_image.copy()
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for row in rows:
        left, top, right, bottom = row.bbox
        color = "#1f77b4" if row.exact_match else "#d62728"
        draw.rectangle([left, top, right, bottom], outline=color, width=5)
        label = f"{row.box_id}"
        if font is not None:
            bbox = draw.textbbox((left, top), label, font=font)
            label_bg = [bbox[0] - 3, bbox[1] - 2, bbox[2] + 3, bbox[3] + 2]
            draw.rectangle(label_bg, fill=color)
            draw.text((left, top), label, fill="white", font=font)
        else:
            draw.text((left, top), label, fill=color)
    image.save(output_path)


def command_phase0_report(args: argparse.Namespace) -> Path:
    report_path = Path(args.output or REPORT_DIR / "phase0_dataset_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(inspect_many_handwrite_dataset(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path


def command_zero_shot(args: argparse.Namespace) -> Path:
    run_name = f"seed{args.seed}_pages{args.sample_pages}_items{args.max_items}"
    run_dir = ensure_dir(Path(args.output_root) / run_name)
    visuals_dir = ensure_dir(run_dir / "visuals") if args.save_visuals else None
    setup_logging(run_dir / "run.log")

    logging.info("loading matched pages from %s", args.data_root)
    pages = load_matched_pages(Path(args.data_root))
    selected_pages = select_pages(pages, args.sample_pages, args.seed)
    selected_boxes = flatten_boxes(selected_pages, args.max_items)
    page_by_stem = {page.stem: page for page in selected_pages}

    manifest = {
        "model_name": args.model_name,
        "data_root": str(args.data_root),
        "seed": args.seed,
        "sample_pages": args.sample_pages,
        "max_items": args.max_items,
        "batch_size": args.batch_size,
        "selected_pages": [page.stem for page in selected_pages],
        "selected_box_count": len(selected_boxes),
    }
    dump_json(run_dir / "run_config.json", manifest)

    logging.info("selected %s pages and %s crops", len(selected_pages), len(selected_boxes))
    model = KoTrocrZeroShot(model_name=args.model_name, device=args.device, max_new_tokens=args.max_new_tokens)
    logging.info("model loaded on %s", model.device)

    page_cache: dict[str, Image.Image] = {}
    crop_inputs = []
    for box in selected_boxes:
        page = page_by_stem[box.page_stem]
        if box.page_stem not in page_cache:
            page_cache[box.page_stem] = load_image(page.image_path)
        crop_inputs.append((box, crop_box(page_cache[box.page_stem], box)))

    rows: list[PredictionRow] = []
    text_pairs: list[tuple[str, str]] = []
    for start in range(0, len(crop_inputs), args.batch_size):
        batch = crop_inputs[start : start + args.batch_size]
        predictions = model.predict_batch([image for _, image in batch])
        for (box, _), prediction in zip(batch, predictions, strict=True):
            row_cer = char_error_rate(prediction.text, box.text)
            row_wer = word_error_rate(prediction.text, box.text)
            text_pairs.append((prediction.text, box.text))
            rows.append(
                PredictionRow(
                    crop_key=box.crop_key,
                    page_stem=box.page_stem,
                    box_id=box.box_id,
                    image_name=box.image_path.name,
                    bbox=(min(box.x), min(box.y), max(box.x), max(box.y)),
                    gt_text=box.text,
                    pred_text=prediction.text,
                    cer=row_cer,
                    wer=row_wer,
                    exact_match=prediction.text.strip() == box.text.strip(),
                )
            )

    metrics = aggregate_metrics(text_pairs)
    dump_jsonl(run_dir / "predictions.jsonl", [asdict(row) for row in rows])
    dump_json(
        run_dir / "metrics.json",
        {
            "model_name": args.model_name,
            "sample_count": metrics.sample_count,
            "cer": metrics.cer,
            "wer": metrics.wer,
            "exact_match": metrics.exact_match,
            "selected_pages": [page.stem for page in selected_pages],
            "selected_crops": len(rows),
        },
    )
    if visuals_dir is not None:
        rows_by_page: dict[str, list[PredictionRow]] = defaultdict(list)
        for row in rows:
            rows_by_page[row.page_stem].append(row)
        for page in selected_pages:
            page_rows = rows_by_page.get(page.stem, [])
            if page_rows:
                overlay_page(page_cache[page.stem], page_rows, visuals_dir / f"{page.stem}_overlay.png")
    write_report(
        run_dir / "report.md",
        model_name=args.model_name,
        data_root=Path(args.data_root),
        selected_pages=[page.stem for page in selected_pages],
        item_count=len(rows),
        metrics=metrics,
        sample_rows=rows,
        visuals_dir=visuals_dir,
    )
    logging.info("artifacts written to %s", run_dir)
    return run_dir


def command_train_general(args: argparse.Namespace) -> Path:
    config = TrainingConfig(
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        epochs=args.epochs,
        sample_limit=args.sample_limit,
        val_sample_limit=args.val_sample_limit,
        test_sample_limit=args.test_sample_limit,
    )
    return run_training_plan("many_handwrite", "phase2_general_finetune", config)


def command_train_elderly(args: argparse.Namespace) -> Path:
    config = TrainingConfig(
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        epochs=args.epochs,
        sample_limit=args.sample_limit,
        val_sample_limit=args.val_sample_limit,
        test_sample_limit=args.test_sample_limit,
    )
    return run_training_plan(
        "public_old",
        "phase3_elderly_finetune",
        config,
        resume_from=args.resume_from,
    )


def command_train_development(args: argparse.Namespace) -> Path:
    config = TrainingConfig(
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        epochs=args.epochs,
        sample_limit=args.sample_limit,
        val_sample_limit=args.val_sample_limit,
        test_sample_limit=args.test_sample_limit,
    )
    return run_development_plan(config)


def command_seed_forms(_: argparse.Namespace) -> Path:
    repository = FormRepository(DB_PATH)
    repository.initialize()
    return DB_PATH


def command_project_audit(args: argparse.Namespace) -> Path:
    return run_project_audit(output_path=Path(args.output) if args.output else None)


def command_integration_check(args: argparse.Namespace) -> Path:
    repository = FormRepository(DB_PATH)
    repository.initialize()
    image_path = Path(args.image) if args.image else load_many_handwrite_records()[0].image_path
    service = RecognitionService()
    form = repository.get_form(args.form_id) if args.form_id else repository.list_forms()[0]
    if form is None:
        raise ValueError("선택한 양식을 찾을 수 없습니다.")
    document = service.recognize_document(
        image_path,
        fields=form.fields,
        template_image=form.template_image,
        prefer_annotation_fallback=False,
    )
    low_confidence = [
        region.text
        for region in document.regions
        if region.confidence < service.model_config.confidence_threshold
    ]
    prefill = build_prefill(form, [document], low_confidence)
    session_id = f"integration_{uuid.uuid4().hex[:8]}"
    saved_path = save_payload(
        {
            "session_id": session_id,
            "form_id": form.form_id,
            "values": prefill,
            "recognition": document.to_dict(service.model_config.confidence_threshold),
        },
        destination=STORAGE_DIR / f"{session_id}_{form.form_id}.json",
    )
    repository.save_submission(form.form_id, session_id, str(saved_path))
    output_path = Path(args.output or REPORT_DIR / "phase7_integration_check.json")
    output_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "document": document.to_dict(service.model_config.confidence_threshold),
                "prefill": prefill,
                "saved_path": str(saved_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path


def command_serve(args: argparse.Namespace) -> None:
    run_server(WebConfig(host=args.host, port=args.port))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OCR project main entry point")
    subparsers = parser.add_subparsers(dest="command", required=True)
    training_defaults = TrainingConfig()

    phase0 = subparsers.add_parser("phase0-report", help="Generate the Phase 0 dataset inspection report")
    phase0.add_argument("--output")
    phase0.set_defaults(handler=command_phase0_report)

    zero_shot = subparsers.add_parser("zero-shot", help="Run the Phase 1 zero-shot baseline")
    zero_shot.add_argument("--data-root", type=Path, default=Path("data/many_handwrite"))
    zero_shot.add_argument("--output-root", type=Path, default=Path("runs/phase1_zero_shot"))
    zero_shot.add_argument("--model-name", default="ddobokki/ko-trocr")
    zero_shot.add_argument("--seed", type=int, default=42)
    zero_shot.add_argument("--sample-pages", type=int, default=2)
    zero_shot.add_argument("--max-items", type=int, default=20)
    zero_shot.add_argument("--batch-size", type=int, default=4)
    zero_shot.add_argument("--max-new-tokens", type=int, default=64)
    zero_shot.add_argument("--device", default="auto")
    zero_shot.add_argument("--save-visuals", action="store_true")
    zero_shot.set_defaults(handler=command_zero_shot)

    train_general = subparsers.add_parser("train-general", help="Prepare Phase 2 general fine-tuning")
    train_general.add_argument("--learning-rate", type=float, default=training_defaults.learning_rate)
    train_general.add_argument("--batch-size", type=int, default=training_defaults.batch_size)
    train_general.add_argument("--epochs", type=int, default=training_defaults.epochs)
    train_general.add_argument("--sample-limit", type=int, default=training_defaults.sample_limit)
    train_general.add_argument("--val-sample-limit", type=int, default=training_defaults.val_sample_limit)
    train_general.add_argument("--test-sample-limit", type=int, default=training_defaults.test_sample_limit)
    train_general.set_defaults(handler=command_train_general)

    train_elderly = subparsers.add_parser("train-elderly", help="Prepare Phase 3 elderly fine-tuning")
    train_elderly.add_argument("--learning-rate", type=float, default=training_defaults.learning_rate)
    train_elderly.add_argument("--batch-size", type=int, default=training_defaults.batch_size)
    train_elderly.add_argument("--epochs", type=int, default=training_defaults.epochs)
    train_elderly.add_argument("--sample-limit", type=int, default=training_defaults.sample_limit)
    train_elderly.add_argument("--val-sample-limit", type=int, default=training_defaults.val_sample_limit)
    train_elderly.add_argument("--test-sample-limit", type=int, default=training_defaults.test_sample_limit)
    train_elderly.add_argument("--resume-from")
    train_elderly.set_defaults(handler=command_train_elderly)

    development = subparsers.add_parser("train-development", help="Prepare the Phase 9 development plan")
    development.add_argument("--learning-rate", type=float, default=training_defaults.learning_rate)
    development.add_argument("--batch-size", type=int, default=training_defaults.batch_size)
    development.add_argument("--epochs", type=int, default=training_defaults.epochs)
    development.add_argument("--sample-limit", type=int, default=training_defaults.sample_limit)
    development.add_argument("--val-sample-limit", type=int, default=training_defaults.val_sample_limit)
    development.add_argument("--test-sample-limit", type=int, default=training_defaults.test_sample_limit)
    development.set_defaults(handler=command_train_development)

    seed_forms = subparsers.add_parser("seed-forms", help="Initialize the form database")
    seed_forms.set_defaults(handler=command_seed_forms)

    project_audit = subparsers.add_parser("project-audit", help="Audit the project against PROJECT.md")
    project_audit.add_argument("--output")
    project_audit.set_defaults(handler=command_project_audit)

    integration = subparsers.add_parser("integration-check", help="Run the Phase 7 integration scenario")
    integration.add_argument("--birthdate", default="900101")
    integration.add_argument("--image")
    integration.add_argument("--form-id")
    integration.add_argument("--output")
    integration.set_defaults(handler=command_integration_check)

    serve = subparsers.add_parser("serve", help="Run the backend/frontend server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=command_serve)

    return parser


def main() -> int:
    ensure_runtime_dirs()
    parser = build_parser()
    args = parser.parse_args()
    result = args.handler(args)
    if result is not None:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
