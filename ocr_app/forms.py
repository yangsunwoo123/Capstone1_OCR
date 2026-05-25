from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_FIELD_TYPES = {"input", "textarea"}
ALLOWED_FIELD_SOURCES = {"manual", "region", "full_text", "summary", "low_confidence"}


def _slugify(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", value.strip())
    cleaned = cleaned.strip("-_")
    return cleaned or fallback


def _coerce_optional_int(value: object) -> int | None:
    if value in (None, "", "null"):
        return None
    return int(value)


def _normalize_field(raw_field: dict[str, object], index: int) -> dict[str, object]:
    label = str(raw_field.get("label", "")).strip() or f"필드 {index + 1}"
    name = _slugify(str(raw_field.get("name", "")).strip() or label, f"field_{index + 1}")
    field_type = str(raw_field.get("type", "input")).strip().lower() or "input"
    if field_type not in ALLOWED_FIELD_TYPES:
        raise ValueError(f"지원하지 않는 필드 타입입니다: {field_type}")
    source = str(raw_field.get("source", "")).strip().lower()
    if not source:
        source = _infer_field_source(name)
    if source not in ALLOWED_FIELD_SOURCES:
        raise ValueError(f"지원하지 않는 필드 source입니다: {source}")
    raw_coordinates = {key: _coerce_optional_int(raw_field.get(key)) for key in ("x", "y", "width", "height")}
    if source == "region":
        missing_coordinates = [key for key, value in raw_coordinates.items() if value is None]
        if missing_coordinates:
            raise ValueError("region 필드는 x/y/width/height 좌표가 모두 필요합니다.")
        if int(raw_coordinates["width"] or 0) <= 0 or int(raw_coordinates["height"] or 0) <= 0:
            raise ValueError("region 필드의 width/height는 1 이상이어야 합니다.")
    field: dict[str, object] = {
        "name": name,
        "label": label,
        "type": field_type,
        "source": source,
        "placeholder": str(raw_field.get("placeholder", "")).strip(),
    }
    region_index = _coerce_optional_int(raw_field.get("region_index"))
    if region_index is not None:
        field["region_index"] = region_index
    for key, value in raw_coordinates.items():
        if value is not None:
            field[key] = value
    return field


def _infer_field_source(field_name: str) -> str:
    lowered = field_name.lower()
    if "recognized" in lowered or "full_text" in lowered:
        return "full_text"
    if "low_confidence" in lowered:
        return "low_confidence"
    if "summary" in lowered:
        return "summary"
    return "manual"


def _document_combined_text(document: object) -> str:
    if hasattr(document, "combined_text"):
        return str(getattr(document, "combined_text"))
    if isinstance(document, dict):
        return str(document.get("combined_text", ""))
    return ""


def _region_sort_key(region: object) -> tuple[int, int]:
    if isinstance(region, dict):
        bbox = region.get("bbox", {})
        return int(bbox.get("y1", 0)), int(bbox.get("x1", 0))
    bbox = getattr(region, "bbox", None)
    return int(getattr(bbox, "y1", 0)), int(getattr(bbox, "x1", 0))


def _region_text(region: object) -> str:
    if isinstance(region, dict):
        return str(region.get("text", "")).strip()
    return str(getattr(region, "text", "")).strip()


def _document_regions(document: object) -> list[object]:
    if isinstance(document, dict):
        return list(document.get("regions", []))
    return list(getattr(document, "regions", []))


@dataclass(slots=True)
class FormDefinition:
    form_id: str
    name: str
    description: str
    fields: list[dict[str, object]]
    template_image: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.form_id,
            "name": self.name,
            "description": self.description,
            "fields": self.fields,
            "template_image": self.template_image,
        }


DEFAULT_FORMS = [
    FormDefinition(
        form_id="direct-payment",
        name="기본직접지불금 등록신청서",
        description="직불금 지급대상자 등록신청서(농업인용) — 5페이지 양식",
        fields=[
            {"name": "recognized_text", "label": "인식된 전체 텍스트", "type": "textarea", "source": "full_text"},
            {"name": "low_confidence_notes", "label": "저신뢰도 확인", "type": "textarea", "source": "low_confidence"},
            {"name": "review_notes", "label": "검토 메모", "type": "textarea", "source": "manual"},
        ],
    ),
]


def validate_form_payload(
    payload: dict[str, object],
    template_image: str | None = None,
) -> FormDefinition:
    raw_fields = payload.get("fields", [])
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("양식 필드는 1개 이상이어야 합니다.")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("양식 이름이 필요합니다.")
    raw_form_id = str(payload.get("id", "")).strip()
    form_id = _slugify(raw_form_id or name, "custom-form")
    description = str(payload.get("description", "")).strip()
    fields = [_normalize_field(field, index) for index, field in enumerate(raw_fields) if isinstance(field, dict)]
    if not fields:
        raise ValueError("유효한 필드가 없습니다.")
    return FormDefinition(
        form_id=form_id,
        name=name,
        description=description,
        fields=fields,
        template_image=template_image,
    )


class FormRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    template_image_path TEXT
                )
                """
            )
            self._ensure_column(connection, "forms", "template_image_path", "TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    form_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for form in DEFAULT_FORMS:
                connection.execute(
                    """
                    INSERT INTO forms (id, name, description, fields_json, template_image_path)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        fields_json = excluded.fields_json,
                        template_image_path = COALESCE(forms.template_image_path, excluded.template_image_path)
                    """,
                    (
                        form.form_id,
                        form.name,
                        form.description,
                        json.dumps(form.fields, ensure_ascii=False),
                        form.template_image,
                    ),
                )

    def _ensure_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def list_forms(self) -> list[FormDefinition]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, description, fields_json, template_image_path
                FROM forms
                ORDER BY CASE WHEN id = 'review-sheet' THEN 0 ELSE 1 END, name
                """
            ).fetchall()
        return [
            FormDefinition(
                form_id=row[0],
                name=row[1],
                description=row[2],
                fields=self._deserialize_fields(row[3]),
                template_image=row[4],
            )
            for row in rows
        ]

    def get_form(self, form_id: str) -> FormDefinition | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, description, fields_json, template_image_path FROM forms WHERE id = ?",
                (form_id,),
            ).fetchone()
        if row is None:
            return None
        return FormDefinition(
            form_id=row[0],
            name=row[1],
            description=row[2],
            fields=self._deserialize_fields(row[3]),
            template_image=row[4],
        )

    def upsert_form(self, form: FormDefinition) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO forms (id, name, description, fields_json, template_image_path)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    fields_json = excluded.fields_json,
                    template_image_path = excluded.template_image_path
                """,
                (
                    form.form_id,
                    form.name,
                    form.description,
                    json.dumps(form.fields, ensure_ascii=False),
                    form.template_image,
                ),
            )

    def save_submission(self, form_id: str, session_id: str, storage_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO submissions (form_id, session_id, storage_path) VALUES (?, ?, ?)",
                (form_id, session_id, storage_path),
            )

    def _deserialize_fields(self, raw_fields: str) -> list[dict[str, object]]:
        loaded = json.loads(raw_fields)
        if not isinstance(loaded, list):
            return []
        return [_normalize_field(field, index) for index, field in enumerate(loaded) if isinstance(field, dict)]


def build_prefill(form: FormDefinition, documents: list[object], low_confidence_lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    low_confidence_blob = "\n".join(line for line in low_confidence_lines if line.strip())
    combined_chunks = [_document_combined_text(document) for document in documents]
    combined_text = "\n\n".join(chunk for chunk in combined_chunks if chunk.strip())
    summary = combined_text.splitlines()[0] if combined_text.splitlines() else combined_text
    ordered_region_texts: list[str] = []
    region_values_by_field: dict[str, list[str]] = {}
    for document in documents:
        regions = sorted(_document_regions(document), key=_region_sort_key)
        for region in regions:
            text = _region_text(region)
            if not text:
                continue
            ordered_region_texts.append(text)
            field_name = ""
            if isinstance(region, dict):
                field_name = str(region.get("field_name", "")).strip()
            else:
                field_name = str(getattr(region, "field_name", "") or "").strip()
            if field_name:
                region_values_by_field.setdefault(field_name, []).append(text)
    next_region_index = 0
    for field in form.fields:
        name = str(field.get("name", "")).strip()
        source = str(field.get("source", "")).strip().lower() or _infer_field_source(name)
        if source == "full_text":
            values[name] = combined_text
            continue
        if source == "low_confidence":
            values[name] = low_confidence_blob
            continue
        if source == "summary":
            values[name] = summary
            continue
        if source == "region":
            if name in region_values_by_field:
                values[name] = "\n".join(region_values_by_field[name])
                continue
            region_index = _coerce_optional_int(field.get("region_index"))
            if region_index is None:
                region_index = next_region_index
                next_region_index += 1
            values[name] = ordered_region_texts[region_index] if region_index < len(ordered_region_texts) else ""
            continue
        if source == "manual" and _infer_field_source(name) != "manual":
            inherited_source = _infer_field_source(name)
            if inherited_source == "full_text":
                values[name] = combined_text
            elif inherited_source == "low_confidence":
                values[name] = low_confidence_blob
            elif inherited_source == "summary":
                values[name] = summary
            else:
                values[name] = ""
            continue
        values[name] = ""
    return values
