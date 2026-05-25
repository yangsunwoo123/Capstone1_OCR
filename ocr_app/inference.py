from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import DATA_DIR, FORM_ASSET_DIR, FORM_IMAGES, ModelConfig
from .data import BoundingBox, DocumentRecord, load_many_handwrite_records, load_public_old_records
from .ocr_engine import KoTrOCREngine, MissingModelDependencyError, annotation_prediction, unavailable_prediction


@dataclass(slots=True)
class RegionResult:
    bbox: BoundingBox
    text: str
    confidence: float
    candidates: list[str]
    source: str
    field_name: str | None = None
    template_applied: bool = False

    def to_dict(self, threshold: float) -> dict[str, object]:
        return {
            "bbox": self.bbox.to_dict(),
            "text": self.text,
            "confidence": self.confidence,
            "candidates": self.candidates,
            "source": self.source,
            "field_name": self.field_name,
            "template_applied": self.template_applied,
            "low_confidence": self.confidence < threshold,
        }


@dataclass(slots=True)
class DocumentResult:
    image_path: str
    width: int
    height: int
    regions: list[RegionResult]

    @property
    def combined_text(self) -> str:
        ordered = sorted(self.regions, key=lambda region: (region.bbox.y1, region.bbox.x1))
        return "\n".join(region.text for region in ordered if region.text.strip())

    def to_dict(self, threshold: float) -> dict[str, object]:
        return {
            "image_path": self.image_path,
            "width": self.width,
            "height": self.height,
            "combined_text": self.combined_text,
            "regions": [region.to_dict(threshold) for region in self.regions],
        }


class AnnotationCatalog:
    def __init__(self) -> None:
        self._lookup: dict[str, DocumentRecord] = {}
        for record in load_many_handwrite_records():
            self._lookup[record.stem] = record
        for record in load_public_old_records():
            self._lookup[record.stem] = record

    def get(self, path: Path) -> DocumentRecord | None:
        return self._lookup.get(path.stem)


class RecognitionService:
    def __init__(self, model_config: ModelConfig | None = None) -> None:
        self.model_config = model_config or ModelConfig()
        self.catalog = AnnotationCatalog()
        self.engine = KoTrOCREngine(self.model_config)

    def _fallback_detection(self, image: Image.Image) -> list[BoundingBox]:
        width, height = image.size
        return [BoundingBox(x1=0, y1=0, x2=width, y2=height, text="", box_id=1)]

    def _detect_form_regions(
        self,
        image_path: Path,
        fields: list[dict[str, object]],
        template_size: tuple[int, int] | None = None,
    ) -> tuple[int, int, list[tuple[BoundingBox, str]]]:
        region_fields = [
            field
            for field in fields
            if str(field.get("source", "")).strip().lower() == "region"
        ]
        if not region_fields:
            raise ValueError("선택한 양식에 좌표 기반 region 필드가 없습니다.")
        with Image.open(image_path) as image:
            width, height = image.size
        scale_x = 1.0
        scale_y = 1.0
        if template_size is not None and template_size[0] > 0 and template_size[1] > 0:
            scale_x = width / template_size[0]
            scale_y = height / template_size[1]
        boxes: list[tuple[BoundingBox, str]] = []
        for index, field in enumerate(region_fields, start=1):
            field_name = str(field.get("name", "")).strip()
            try:
                x = round(int(field["x"]) * scale_x)
                y = round(int(field["y"]) * scale_y)
                box_width = round(int(field["width"]) * scale_x)
                box_height = round(int(field["height"]) * scale_y)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{field_name or index} region 필드에 x/y/width/height 좌표가 필요합니다.") from error
            x2 = x + box_width
            y2 = y + box_height
            if x < 0 or y < 0 or box_width <= 0 or box_height <= 0 or x2 > width or y2 > height:
                raise ValueError(f"{field_name or index} region 필드 좌표가 이미지 범위를 벗어났습니다.")
            boxes.append(
                (
                    BoundingBox(x1=x, y1=y, x2=x2, y2=y2, text="", box_id=index),
                    field_name,
                )
            )
        return width, height, boxes

    def _detect_regions(self, image_path: Path) -> tuple[int, int, list[BoundingBox]]:
        record = self.catalog.get(image_path)
        if record is not None:
            return record.width, record.height, record.boxes
        with Image.open(image_path) as image:
            width, height = image.size
            return width, height, self._fallback_detection(image)

    def _resolve_template_path(self, template_image: str | None) -> Path | None:
        if not template_image:
            return None
        if template_image.startswith("/form-assets/"):
            candidate = FORM_ASSET_DIR / template_image.replace("/form-assets/", "", 1)
        elif template_image.startswith("/data/"):
            filename = template_image.replace("/data/", "", 1)
            candidate = self._find_data_file(filename)
            if candidate is None:
                return None
            return candidate
        else:
            candidate = Path(template_image)
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    @staticmethod
    def _find_data_file(filename: str) -> Path | None:
        import unicodedata
        target_nfc = unicodedata.normalize("NFC", filename)
        if not DATA_DIR.exists():
            return None
        for entry in DATA_DIR.iterdir():
            if unicodedata.normalize("NFC", entry.name) == target_nfc:
                return entry
        return None

    def _template_size(self, template_image: str | None) -> tuple[int, int] | None:
        template_path = self._resolve_template_path(template_image)
        if template_path is None:
            return None
        with Image.open(template_path) as template:
            return template.size

    def _load_template_for_image(self, template_image: str | None, target_size: tuple[int, int]) -> Image.Image | None:
        template_path = self._resolve_template_path(template_image)
        if template_path is None:
            return None
        with Image.open(template_path) as template:
            loaded = template.convert("RGB")
        if loaded.size != target_size:
            loaded = loaded.resize(target_size, Image.Resampling.BICUBIC)
        return loaded

    def _detect_handwriting_from_template(
        self,
        image: Image.Image,
        template: Image.Image,
    ) -> list[BoundingBox]:
        """Returns empty list when no handwriting is found — never falls back to a full-image box."""
        import numpy as np
        from PIL import ImageFilter

        width, height = image.size
        if template.size != image.size:
            template = template.resize(image.size, Image.Resampling.BICUBIC)

        # Blur before diff: smooths JPEG compression artifacts and sub-pixel misalignment
        blur_r = max(1.0, min(width, height) / 600.0)
        img_blur = image.filter(ImageFilter.GaussianBlur(radius=blur_r))
        tmpl_blur = template.filter(ImageFilter.GaussianBlur(radius=blur_r))

        gray_img = np.array(img_blur.convert("L"), dtype=np.float32)
        gray_tmpl = np.array(tmpl_blur.convert("L"), dtype=np.float32)

        # Normalise brightness so scan-exposure differences don't generate false diffs
        brightness_offset = float(gray_img.mean() - gray_tmpl.mean())
        gray_tmpl_adj = np.clip(gray_tmpl + brightness_offset, 0, 255)

        directed_diff = gray_tmpl_adj - gray_img

        gray_orig = np.array(image.convert("L"), dtype=np.float32)

        # 양식 원본(블러 전)으로 흰색/회색 배경 판별: 흰색 배경만 예측 대상
        gray_tmpl_raw = np.array(template.convert("L"), dtype=np.float32)
        template_is_white = gray_tmpl_raw > 210

        mask = (directed_diff > 30) & (gray_orig < 200) & template_is_white

        row_sums = mask.sum(axis=1)
        # Require enough pixels per row: filters isolated noise while allowing handwriting
        min_pixels = max(15, width // 120)
        row_active = row_sums >= min_pixels

        # Small gap tolerance to keep individual text lines separate
        gap = max(4, height // 200)
        i = 0
        while i < height:
            if row_active[i]:
                j = i + 1
                while j < height and not row_active[j]:
                    j += 1
                if 0 < (j - i - 1) <= gap and j < height:
                    row_active[i + 1 : j] = True
                i = j
            else:
                i += 1

        boxes: list[BoundingBox] = []
        in_region = False
        start_row = 0
        margin = 6
        # 같은 행 밴드 내 항목 간 최소 빈 열 수: 이보다 작은 간격은 같은 항목으로 합침
        col_gap = max(10, width // 150)

        def _split_row_band(r0: int, r1: int) -> None:
            """row band [r0, r1) 안에서 열 방향으로도 분리하여 개별 박스 추가."""
            rh = r1 - r0
            row_slice = mask[r0:r1, :]
            col_sums = row_slice.sum(axis=0)
            col_min = max(2, rh // 40)
            col_active = col_sums >= col_min

            # 열 방향 작은 간격 메우기 (같은 단어/항목 내 글자 사이)
            ci = 0
            while ci < width:
                if col_active[ci]:
                    cj = ci + 1
                    while cj < width and not col_active[cj]:
                        cj += 1
                    if 0 < (cj - ci - 1) <= col_gap and cj < width:
                        col_active[ci + 1 : cj] = True
                    ci = cj
                else:
                    ci += 1

            # 연속된 열 그룹마다 별도 박스 생성
            in_col = False
            sc = 0
            for ci in range(width):
                if not in_col and col_active[ci]:
                    in_col = True
                    sc = ci
                elif in_col and not col_active[ci]:
                    in_col = False
                    x1 = max(0, sc - margin)
                    x2 = min(width, ci + margin)
                    y1 = max(0, r0 - margin)
                    y2 = min(height, r1 + margin)
                    if (x2 - x1) >= 15 and (y2 - y1) >= 10:
                        boxes.append(BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2, text="", box_id=len(boxes) + 1))
            if in_col:
                x1 = max(0, sc - margin)
                x2 = width
                y1 = max(0, r0 - margin)
                y2 = min(height, r1 + margin)
                if (x2 - x1) >= 15 and (y2 - y1) >= 10:
                    boxes.append(BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2, text="", box_id=len(boxes) + 1))

        for row_idx in range(height):
            if not in_region and row_active[row_idx]:
                in_region = True
                start_row = row_idx
            elif in_region and not row_active[row_idx]:
                in_region = False
                _split_row_band(start_row, row_idx)

        if in_region:
            _split_row_band(start_row, height)

        # Sanity check: 비정상적으로 많으면 템플릿 불일치로 판단 → fallback
        # 빈칸이 많은 양식도 처리할 수 있도록 150으로 상향
        if len(boxes) > 150:
            return []

        return boxes  # empty list when no handwriting found

    def _map_boxes_to_fields(
        self,
        boxes: list[BoundingBox],
        fields: list[dict[str, object]],
        image_size: tuple[int, int],
        template_size: tuple[int, int] | None,
    ) -> list[tuple[BoundingBox, str | None]]:
        width, height = image_size
        scale_x, scale_y = 1.0, 1.0
        if template_size and template_size[0] > 0 and template_size[1] > 0:
            scale_x = width / template_size[0]
            scale_y = height / template_size[1]

        region_fields = [f for f in fields if str(f.get("source", "")).strip().lower() == "region"]
        used_field_names: set[str] = set()
        result: list[tuple[BoundingBox, str | None]] = []

        for box in boxes:
            best_name: str | None = None
            best_ratio = 0.0
            for field in region_fields:
                fname = str(field.get("name", "")).strip()
                if fname in used_field_names:
                    continue
                try:
                    fx1 = round(int(field["x"]) * scale_x)
                    fy1 = round(int(field["y"]) * scale_y)
                    fx2 = fx1 + round(int(field["width"]) * scale_x)
                    fy2 = fy1 + round(int(field["height"]) * scale_y)
                except (KeyError, TypeError, ValueError):
                    continue
                ix1 = max(box.x1, fx1)
                iy1 = max(box.y1, fy1)
                ix2 = min(box.x2, fx2)
                iy2 = min(box.y2, fy2)
                if ix1 < ix2 and iy1 < iy2:
                    intersection = (ix2 - ix1) * (iy2 - iy1)
                    field_area = (fx2 - fx1) * (fy2 - fy1)
                    if field_area > 0:
                        ratio = intersection / field_area
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_name = fname
            if best_name and best_ratio >= 0.3:
                used_field_names.add(best_name)
            result.append((box, best_name if best_ratio >= 0.3 else None))

        return result

    def _has_sufficient_ink(self, image: Image.Image, min_pixels: int = 15) -> bool:
        """Return True if the image has enough dark pixels to contain actual handwriting."""
        import numpy as np
        gray = np.array(image.convert("L"), dtype=np.uint8)
        return int((gray < 200).sum()) >= min_pixels

    def _isolate_handwriting_crop(self, crop: Image.Image, template_crop: Image.Image) -> Image.Image:
        import numpy as np
        crop_arr = np.array(crop.convert("L"), dtype=np.float32)
        tmpl_resized = template_crop.resize(crop.size)
        tmpl_arr = np.array(tmpl_resized.convert("L"), dtype=np.float32)
        offset = float(tmpl_arr.mean() - crop_arr.mean())
        tmpl_norm = np.clip(tmpl_arr - offset, 0, 255)
        directed = tmpl_norm - crop_arr
        # 양식 원본으로 흰색 배경 판별: 회색 배경 영역은 손글씨 대상에서 제외
        tmpl_raw = np.array(tmpl_resized.convert("L"), dtype=np.float32)
        template_is_white = tmpl_raw > 210
        handwriting = (directed > 25) & (crop_arr < 200) & template_is_white
        result = np.full_like(crop_arr, 255.0)
        result[handwriting] = crop_arr[handwriting]
        return Image.fromarray(result.astype(np.uint8)).convert("RGB")

    def recognize_document(
        self,
        image_path: Path,
        fields: list[dict[str, object]] | None = None,
        template_image: str | None = None,
        prefer_annotation_fallback: bool = True,
    ) -> DocumentResult:
        regions: list[RegionResult] = []
        with Image.open(image_path) as raw_image:
            image = raw_image.convert("RGB")
            width, height = image.size
            template = self._load_template_for_image(template_image, image.size)

            template_size = self._template_size(template_image)
            has_region_fields = fields is not None and any(
                str(f.get("source", "")).strip().lower() == "region" for f in fields
            )
            if template is not None and fields is not None:
                raw_boxes = self._detect_handwriting_from_template(image, template)
                if raw_boxes:
                    field_boxes = self._map_boxes_to_fields(raw_boxes, fields, (width, height), template_size)
                elif has_region_fields:
                    _, _, field_boxes = self._detect_form_regions(image_path, fields, template_size=template_size)
                else:
                    field_boxes = []
            elif has_region_fields:
                _, _, field_boxes = self._detect_form_regions(image_path, fields, template_size=template_size)
            else:
                _, _, boxes = self._detect_regions(image_path)
                field_boxes = [(box, None) for box in boxes]

            for box, field_name in field_boxes:
                crop = image.crop((box.x1, box.y1, box.x2, box.y2))
                template_applied = False
                if template is not None:
                    template_crop = template.crop((box.x1, box.y1, box.x2, box.y2))
                    crop = self._isolate_handwriting_crop(crop, template_crop)
                    template_applied = True
                    if not self._has_sufficient_ink(crop):
                        # Skip OCR on empty crops — prevents garbage text from blank regions
                        regions.append(
                            RegionResult(
                                bbox=box,
                                text="",
                                confidence=0.0,
                                candidates=[],
                                source="empty_crop",
                                field_name=field_name,
                                template_applied=True,
                            )
                        )
                        continue
                try:
                    prediction = self.engine.predict_crop(crop)
                except MissingModelDependencyError:
                    if prefer_annotation_fallback and box.text:
                        prediction = annotation_prediction(box.text)
                    else:
                        prediction = unavailable_prediction()
                regions.append(
                    RegionResult(
                        bbox=box,
                        text=prediction.text,
                        confidence=prediction.confidence,
                        candidates=prediction.candidates,
                        source=prediction.source,
                        field_name=field_name,
                        template_applied=template_applied,
                    )
                )
        return DocumentResult(
            image_path=str(image_path),
            width=width,
            height=height,
            regions=regions,
        )

    def recognize_many(
        self,
        image_paths: list[Path],
        fields: list[dict[str, object]] | None = None,
        template_image: str | None = None,
        template_images: list[str] | None = None,
        prefer_annotation_fallback: bool = False,
    ) -> list[DocumentResult]:
        results: list[DocumentResult] = []
        for index, path in enumerate(image_paths):
            if template_images and index < len(template_images):
                tmpl = template_images[index]
            else:
                tmpl = template_image
            results.append(
                self.recognize_document(
                    path,
                    fields=fields,
                    template_image=tmpl,
                    prefer_annotation_fallback=prefer_annotation_fallback,
                )
            )
        return results
