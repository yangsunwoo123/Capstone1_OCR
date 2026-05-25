from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


@dataclass(frozen=True)
class BoxSample:
    page_stem: str
    image_path: Path
    label_path: Path
    box_id: int
    text: str
    x: tuple[int, int, int, int]
    y: tuple[int, int, int, int]

    @property
    def crop_key(self) -> str:
        return f"{self.page_stem}__{self.box_id:04d}"


@dataclass(frozen=True)
class PageSample:
    stem: str
    image_path: Path
    label_path: Path
    boxes: tuple[BoxSample, ...]
    width: int
    height: int


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def load_matched_pages(data_root: Path) -> list[PageSample]:
    raw_dir = data_root / "rawdata"
    label_dir = data_root / "label"
    if not raw_dir.exists():
        raise FileNotFoundError(f"missing rawdata directory: {raw_dir}")
    if not label_dir.exists():
        raise FileNotFoundError(f"missing label directory: {label_dir}")

    images = {path.stem: path for path in raw_dir.iterdir() if path.is_file()}
    labels = {path.stem: path for path in label_dir.iterdir() if path.is_file()}

    image_stems = set(images)
    label_stems = set(labels)
    if image_stems != label_stems:
        missing_labels = sorted(image_stems - label_stems)
        missing_images = sorted(label_stems - image_stems)
        message = ["image-label pairs are not 1:1"]
        if missing_labels:
            message.append(f"missing labels: {missing_labels[:10]}")
        if missing_images:
            message.append(f"missing images: {missing_images[:10]}")
        raise ValueError("; ".join(message))

    pages: list[PageSample] = []
    for stem in sorted(image_stems):
        image_path = images[stem]
        label_path = labels[stem]
        with label_path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

        image_meta = payload["Images"]
        boxes: list[BoxSample] = []
        for entry in payload["bbox"]:
            x_values = tuple(int(v) for v in entry["x"])
            y_values = tuple(int(v) for v in entry["y"])
            if len(x_values) != 4 or len(y_values) != 4:
                raise ValueError(f"unexpected bbox format in {label_path.name}: {entry}")
            boxes.append(
                BoxSample(
                    page_stem=stem,
                    image_path=image_path,
                    label_path=label_path,
                    box_id=int(entry["id"]),
                    text=str(entry["data"]),
                    x=x_values,
                    y=y_values,
                )
            )

        pages.append(
            PageSample(
                stem=stem,
                image_path=image_path,
                label_path=label_path,
                boxes=tuple(sorted(boxes, key=lambda item: item.box_id)),
                width=int(image_meta["width"]),
                height=int(image_meta["height"]),
            )
        )

    return pages


def select_pages(pages: list[PageSample], sample_pages: int, seed: int) -> list[PageSample]:
    if sample_pages <= 0 or sample_pages >= len(pages):
        return list(pages)
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(pages)), sample_pages))
    return [pages[index] for index in indices]


def flatten_boxes(pages: Iterable[PageSample], max_items: int | None = None) -> list[BoxSample]:
    items: list[BoxSample] = []
    for page in pages:
        items.extend(page.boxes)
        if max_items is not None and len(items) >= max_items:
            return items[:max_items]
    return items


def crop_box(image: Image.Image, box: BoxSample) -> Image.Image:
    left = _clamp(min(box.x), 0, image.width)
    right = _clamp(max(box.x), 0, image.width)
    top = _clamp(min(box.y), 0, image.height)
    bottom = _clamp(max(box.y), 0, image.height)
    if right <= left or bottom <= top:
        raise ValueError(f"invalid crop bounds for {box.crop_key}: {(left, top, right, bottom)}")
    return image.crop((left, top, right, bottom))


def load_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")
