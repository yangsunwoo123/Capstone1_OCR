from __future__ import annotations

from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from ocr_app.data import BoundingBox, inspect_many_handwrite_dataset
from ocr_app.forms import DEFAULT_FORMS, FormDefinition, FormRepository, build_prefill, validate_form_payload
from ocr_app.inference import DocumentResult, RecognitionService, RegionResult
from ocr_app.metrics import cer, wer
from ocr_app.storage import save_payload
from ocr_app.training import build_training_plan_payload
from ocr_app.config import TrainingConfig


class MetricsTests(unittest.TestCase):
    def test_cer_matches_identity(self) -> None:
        self.assertEqual(cer("테스트", "테스트"), 0.0)

    def test_wer_detects_difference(self) -> None:
        self.assertGreater(wer("한 줄", "한 글"), 0.0)


class DatasetTests(unittest.TestCase):
    def test_inspection_sees_pairs(self) -> None:
        report = inspect_many_handwrite_dataset()
        self.assertEqual(report["images"], 180)
        self.assertEqual(report["paired"], 180)


class FormTests(unittest.TestCase):
    def test_prefill_populates_known_fields(self) -> None:
        document = DocumentResult(
            image_path="sample.png",
            width=100,
            height=40,
            regions=[
                RegionResult(
                    bbox=BoundingBox(0, 0, 10, 10, "가", 1),
                    text="가나다",
                    confidence=0.9,
                    candidates=["가나다"],
                    source="model",
                    field_name="recognized_text",
                )
            ],
        )
        values = build_prefill(DEFAULT_FORMS[0], [document], ["가나다"])
        self.assertEqual(values["recognized_text"], "가나다")
        self.assertEqual(values["low_confidence_notes"], "가나다")

    def test_region_source_maps_by_field_name(self) -> None:
        form = FormDefinition(
            form_id="custom",
            name="커스텀",
            description="",
            fields=[
                {"name": "name", "label": "이름", "type": "input", "source": "region", "x": 0, "y": 0, "width": 10, "height": 10},
            ],
        )
        document = DocumentResult(
            image_path="sample.png",
            width=100,
            height=40,
            regions=[
                RegionResult(
                    bbox=BoundingBox(0, 0, 10, 10, "홍", 1),
                    text="홍길동",
                    confidence=0.9,
                    candidates=["홍길동"],
                    source="model",
                    field_name="name",
                ),
                RegionResult(
                    bbox=BoundingBox(10, 0, 20, 10, "1234", 2),
                    text="1234",
                    confidence=0.9,
                    candidates=["1234"],
                    source="model",
                    field_name="other",
                ),
            ],
        )
        values = build_prefill(form, [document], [])
        self.assertEqual(values["name"], "홍길동")

    def test_validate_form_payload_keeps_template_image(self) -> None:
        form = validate_form_payload(
            {
                "id": "resident-card",
                "name": "주민등록 양식",
                "description": "설명",
                "fields": [{"label": "이름", "name": "name", "type": "input", "source": "region", "x": 1, "y": 2, "width": 30, "height": 12}],
            },
            template_image="/form-assets/resident-card.png",
        )
        self.assertEqual(form.template_image, "/form-assets/resident-card.png")

    def test_validate_form_payload_requires_region_coordinates(self) -> None:
        with self.assertRaises(ValueError):
            validate_form_payload(
                {
                    "id": "resident-card",
                    "name": "주민등록 양식",
                    "description": "설명",
                    "fields": [{"label": "이름", "name": "name", "type": "input", "source": "region"}],
                }
            )

    def test_repository_upsert_persists_dynamic_form(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = FormRepository(Path(temp_dir) / "forms.db")
            repository.initialize()
            form = validate_form_payload(
                {
                    "id": "resident-card",
                    "name": "주민등록 양식",
                    "description": "설명",
                    "fields": [{"label": "이름", "name": "name", "type": "input", "source": "region", "x": 1, "y": 2, "width": 30, "height": 12}],
                }
            )
            repository.upsert_form(form)
            loaded = repository.get_form("resident-card")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.name, "주민등록 양식")
            self.assertEqual(loaded.fields[0]["source"], "region")


class RecognitionTests(unittest.TestCase):
    def test_recognition_uses_form_coordinates_and_field_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "scan.png"
            Image.new("RGB", (100, 60), "white").save(image_path)
            service = RecognitionService()
            service.engine = SimpleNamespace(
                predict_crop=lambda crop: SimpleNamespace(
                    text=f"{crop.size[0]}x{crop.size[1]}",
                    confidence=0.8,
                    candidates=["candidate"],
                    source="model",
                )
            )
            document = service.recognize_document(
                image_path,
                fields=[
                    {"name": "name", "source": "region", "x": 10, "y": 5, "width": 30, "height": 20},
                ],
                prefer_annotation_fallback=False,
            )
            self.assertEqual(document.regions[0].text, "30x20")
            self.assertEqual(document.regions[0].field_name, "name")
            self.assertEqual(document.regions[0].bbox.x1, 10)
            self.assertFalse(document.regions[0].template_applied)

    def test_template_crop_removes_printed_form_marks_before_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / "blank.png"
            scan_path = temp_path / "scan.png"
            template = Image.new("RGB", (80, 40), "white")
            template_draw = ImageDraw.Draw(template)
            template_draw.line((0, 20, 80, 20), fill="black", width=2)
            template.save(template_path)
            scan = template.copy()
            scan_draw = ImageDraw.Draw(scan)
            scan_draw.rectangle((30, 10, 36, 16), fill="black")
            scan.save(scan_path)
            captured: dict[str, Image.Image] = {}
            service = RecognitionService()

            def predict_crop(crop: Image.Image) -> SimpleNamespace:
                captured["crop"] = crop.copy()
                return SimpleNamespace(text="손글씨", confidence=0.9, candidates=["손글씨"], source="model")

            service.engine = SimpleNamespace(predict_crop=predict_crop)
            document = service.recognize_document(
                scan_path,
                fields=[
                    {"name": "memo", "source": "region", "x": 0, "y": 0, "width": 80, "height": 40},
                ],
                template_image=str(template_path),
                prefer_annotation_fallback=False,
            )
            processed = captured["crop"].convert("L")
            self.assertEqual(processed.getpixel((33, 13)), 0)
            self.assertEqual(processed.getpixel((10, 20)), 255)
            self.assertTrue(document.regions[0].template_applied)

    def test_template_coordinates_scale_to_scan_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / "blank.png"
            scan_path = temp_path / "scan.png"
            Image.new("RGB", (100, 50), "white").save(template_path)
            Image.new("RGB", (200, 100), "white").save(scan_path)
            service = RecognitionService()
            service.engine = SimpleNamespace(
                predict_crop=lambda crop: SimpleNamespace(
                    text=f"{crop.size[0]}x{crop.size[1]}",
                    confidence=0.9,
                    candidates=[],
                    source="model",
                )
            )
            document = service.recognize_document(
                scan_path,
                fields=[
                    {"name": "memo", "source": "region", "x": 10, "y": 5, "width": 30, "height": 20},
                ],
                template_image=str(template_path),
                prefer_annotation_fallback=False,
            )
            region = document.regions[0]
            self.assertEqual((region.bbox.x1, region.bbox.y1, region.bbox.width, region.bbox.height), (20, 10, 60, 40))
            self.assertEqual(region.text, "60x40")

    def test_recognition_rejects_missing_region_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "scan.png"
            Image.new("RGB", (100, 60), "white").save(image_path)
            service = RecognitionService()
            with self.assertRaises(ValueError):
                service.recognize_document(image_path, fields=[], prefer_annotation_fallback=False)


class StorageTests(unittest.TestCase):
    def test_save_payload_writes_plain_json_for_mvp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = save_payload({"values": {"name": "수정값"}}, Path(temp_dir) / "result.json")
            self.assertIn("수정값", path.read_text(encoding="utf-8"))


class TrainingPlanTests(unittest.TestCase):
    def test_training_plan_payload_splits_crops(self) -> None:
        payload, split_docs, split_crops = build_training_plan_payload(
            dataset_name="many_handwrite",
            stage_name="phase2_general_finetune",
            config=TrainingConfig(sample_limit=32),
        )
        self.assertEqual(sum(payload["document_split"].values()), len(split_docs["train"]) + len(split_docs["val"]) + len(split_docs["test"]))
        self.assertLessEqual(payload["crop_split"]["train"], 32)
        self.assertGreater(payload["crop_split"]["val"], 0)
        self.assertGreater(payload["crop_split"]["test"], 0)
        train_docs = {record.stem for record in split_docs["train"]}
        val_docs = {record.stem for record in split_docs["val"]}
        test_docs = {record.stem for record in split_docs["test"]}
        self.assertTrue(train_docs.isdisjoint(val_docs))
        self.assertTrue(train_docs.isdisjoint(test_docs))
        self.assertTrue(val_docs.isdisjoint(test_docs))
        self.assertGreater(len(split_crops["train"]), 0)

    def test_elderly_training_plan_enforces_smaller_train_split_than_phase2(self) -> None:
        general_payload, _, _ = build_training_plan_payload(
            dataset_name="many_handwrite",
            stage_name="phase2_general_finetune",
            config=TrainingConfig(sample_limit=48),
        )
        elderly_payload, _, _ = build_training_plan_payload(
            dataset_name="public_old",
            stage_name="phase3_elderly_finetune",
            config=TrainingConfig(sample_limit=9999),
            reference_train_crop_count=general_payload["crop_split"]["train"],
        )
        self.assertLess(elderly_payload["crop_split"]["train"], general_payload["crop_split"]["train"])


if __name__ == "__main__":
    unittest.main()
