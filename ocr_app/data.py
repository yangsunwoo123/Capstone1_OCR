from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

from .config import (
    MANY_HANDWRITE_LABEL_DIR,
    MANY_HANDWRITE_RAW_DIR,
    PUBLIC_OLD_LABEL_DIR,
    PUBLIC_OLD_RAW_DIR,
)


@dataclass(slots=True)
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int
    text: str
    box_id: int

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.box_id,
            "text": self.text,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "width": self.width,
            "height": self.height,
        }


@dataclass(slots=True)
class DocumentRecord:
    dataset_name: str
    image_path: Path
    label_path: Path
    width: int
    height: int
    boxes: list[BoundingBox]
    writer_age: int | None = None
    writer_sex: int | None = None

    @property
    def stem(self) -> str:
        return self.image_path.stem

    @property
    def writer_group(self) -> str:
        if self.writer_age is not None or self.writer_sex is not None:
            return f"age:{self.writer_age}|sex:{self.writer_sex}"
        return self.stem


@dataclass(slots=True)
class CropSample:
    dataset_name: str
    image_path: Path
    label_path: Path
    document_stem: str
    text: str
    bbox: BoundingBox
    writer_group: str

    def crop(self) -> Image.Image:
        with Image.open(self.image_path) as image:
            return image.crop((self.bbox.x1, self.bbox.y1, self.bbox.x2, self.bbox.y2)).copy()


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_image_by_stem(raw_dir: Path, stem: str) -> Path | None:
    exact_png = raw_dir / f"{stem}.png"
    if exact_png.exists():
        return exact_png
    matches = sorted(path for path in raw_dir.iterdir() if path.is_file() and path.stem == stem)
    return matches[0] if matches else None


def _parse_many_handwrite_box(raw_box: dict[str, object]) -> BoundingBox:
    xs = [int(value) for value in raw_box.get("x", [])]
    ys = [int(value) for value in raw_box.get("y", [])]
    return BoundingBox(
        x1=min(xs),
        y1=min(ys),
        x2=max(xs),
        y2=max(ys),
        text=str(raw_box.get("data", "")),
        box_id=int(raw_box.get("id", 0)),
    )


def _parse_public_old_box(raw_box: dict[str, object]) -> BoundingBox:
    x, y, width, height = [int(value) for value in raw_box.get("annotation.bbox", [])]
    return BoundingBox(
        x1=x,
        y1=y,
        x2=x + width,
        y2=y + height,
        text=str(raw_box.get("annotation.text", "")),
        box_id=int(raw_box.get("id", 0)),
    )


def load_many_handwrite_records(
    raw_dir: Path = MANY_HANDWRITE_RAW_DIR,
    label_dir: Path = MANY_HANDWRITE_LABEL_DIR,
) -> list[DocumentRecord]:
    records: list[DocumentRecord] = []
    for label_path in sorted(label_dir.glob("*.json")):
        image_path = _find_image_by_stem(raw_dir, label_path.stem)
        if image_path is None:
            continue
        payload = _load_json(label_path)
        image_meta = payload["Images"]
        boxes = [_parse_many_handwrite_box(box) for box in payload.get("bbox", [])]
        records.append(
            DocumentRecord(
                dataset_name="many_handwrite",
                image_path=image_path,
                label_path=label_path,
                width=int(image_meta["width"]),
                height=int(image_meta["height"]),
                writer_age=int(image_meta.get("writer_age")) if image_meta.get("writer_age") is not None else None,
                writer_sex=int(image_meta.get("writer_sex")) if image_meta.get("writer_sex") is not None else None,
                boxes=boxes,
            )
        )
    return records


def load_public_old_records(
    raw_dir: Path = PUBLIC_OLD_RAW_DIR,
    label_dir: Path = PUBLIC_OLD_LABEL_DIR,
) -> list[DocumentRecord]:
    records: list[DocumentRecord] = []
    for label_path in sorted(label_dir.rglob("*.json")):
        payload = _load_json(label_path)
        image_meta = payload["images"][0]
        year = label_path.parent.name
        image_name = image_meta["image.file.name"]
        image_path = raw_dir / year / image_name
        if not image_path.exists():
            continue
        boxes = [_parse_public_old_box(box) for box in payload.get("annotations", [])]
        records.append(
            DocumentRecord(
                dataset_name="public_old",
                image_path=image_path,
                label_path=label_path,
                width=int(image_meta["image.width"]),
                height=int(image_meta["image.height"]),
                boxes=boxes,
            )
        )
    return records


def iter_crop_samples(records: Iterable[DocumentRecord]) -> Iterable[CropSample]:
    for record in records:
        for box in record.boxes:
            if not box.text.strip():
                continue
            yield CropSample(
                dataset_name=record.dataset_name,
                image_path=record.image_path,
                label_path=record.label_path,
                document_stem=record.stem,
                text=box.text,
                bbox=box,
                writer_group=record.writer_group,
            )


def inspect_many_handwrite_dataset() -> dict[str, object]:
    records = load_many_handwrite_records()
    image_paths = [record.image_path for record in records]
    label_paths = [record.label_path for record in records]
    resolutions = Counter((record.width, record.height) for record in records)
    box_counts = [len(record.boxes) for record in records]
    channel_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    invalid_boxes = 0
    for image_path in image_paths[: min(20, len(image_paths))]:
        with Image.open(image_path) as image:
            mode_counts.update([image.mode])
            channel_counts.update([str(len(image.getbands()))])
    for record in records:
        for box in record.boxes:
            if box.x1 < 0 or box.y1 < 0 or box.x2 > record.width or box.y2 > record.height:
                invalid_boxes += 1
    return {
        "images": len(image_paths),
        "labels": len(label_paths),
        "paired": len({path.stem for path in image_paths} & {path.stem for path in label_paths}),
        "image_extension_distribution": Counter(path.suffix for path in image_paths),
        "label_extension_distribution": Counter(path.suffix for path in label_paths),
        "top_level_label_keys": sorted(_load_json(label_paths[0]).keys()) if label_paths else [],
        "mode_counts_sample": dict(mode_counts),
        "channel_counts_sample": dict(channel_counts),
        "resolution_min": {
            "width": min(record.width for record in records),
            "height": min(record.height for record in records),
        }
        if records
        else {},
        "resolution_max": {
            "width": max(record.width for record in records),
            "height": max(record.height for record in records),
        }
        if records
        else {},
        "resolution_common": [
            {"size": list(size), "count": count} for size, count in resolutions.most_common(5)
        ],
        "bbox_count": {
            "min": min(box_counts) if box_counts else 0,
            "max": max(box_counts) if box_counts else 0,
            "mean": statistics.mean(box_counts) if box_counts else 0.0,
        },
        "bbox_invalid_count": invalid_boxes,
        "schema_notes": [
            "top-level keys are Annotation, Dataset, Images, bbox",
            "Images.acquistion_location is intentionally misspelled in the source labels",
            "many_handwrite bbox entries are axis-aligned 4-point rectangles",
        ],
    }


def deterministic_split(
    records: list[DocumentRecord],
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
) -> dict[str, list[DocumentRecord]]:
    sorted_records = sorted(records, key=lambda record: record.stem)
    total = len(sorted_records)
    train_end = int(total * train_ratio)
    val_end = train_end + int(total * val_ratio)
    return {
        "train": sorted_records[:train_end],
        "val": sorted_records[train_end:val_end],
        "test": sorted_records[val_end:],
    }
