from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from text_metrics import AggregateMetrics


@dataclass(frozen=True)
class PredictionRow:
    crop_key: str
    page_stem: str
    box_id: int
    image_name: str
    bbox: tuple[int, int, int, int]
    gt_text: str
    pred_text: str
    cer: float
    wer: float
    exact_match: bool


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip() or "(empty)"


def write_report(
    report_path: Path,
    *,
    model_name: str,
    data_root: Path,
    selected_pages: list[str],
    item_count: int,
    metrics: AggregateMetrics,
    sample_rows: list[PredictionRow],
    visuals_dir: Path | None,
) -> None:
    lines = [
        "# Phase 1 Zero-shot Report",
        "",
        f"- Model: `{model_name}`",
        f"- Data root: `{data_root}`",
        f"- Selected pages: {len(selected_pages)}",
        f"- Selected page stems: {', '.join(selected_pages)}",
        f"- Crops evaluated: {item_count}",
        f"- CER: {metrics.cer:.4f}",
        f"- WER: {metrics.wer:.4f}",
        f"- Exact match: {metrics.exact_match:.4f}",
        "",
        "## Sample Predictions",
        "",
        "| crop_key | gt | pred | CER | WER |",
        "| --- | --- | --- | ---: | ---: |",
    ]

    for row in sample_rows[:20]:
        lines.append(
            f"| `{row.crop_key}` | {_cell(row.gt_text)} | {_cell(row.pred_text)} | {row.cer:.4f} | {row.wer:.4f} |"
        )

    lines.extend(
        [
            "",
            "## Visuals",
            "",
            f"- Saved: `{visuals_dir}`" if visuals_dir else "- Saved: none",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
