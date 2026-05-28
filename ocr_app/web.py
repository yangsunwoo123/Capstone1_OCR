from __future__ import annotations

import cgi
import io
import json
import mimetypes
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from PIL import Image, UnidentifiedImageError

from .config import (
    DATA_DIR,
    DB_PATH,
    FORM_ASSET_DIR,
    FORM_IMAGES,
    MAX_FORM_TEMPLATE_BYTES,
    MAX_UPLOAD_BYTES,
    STORAGE_DIR,
    UPLOAD_DIR,
    WebConfig,
)
from .forms import FormRepository, build_prefill, validate_form_payload
from .inference import RecognitionService
from .storage import save_payload, save_two_files


@dataclass(slots=True)
class AppState:
    form_repository: FormRepository
    recognition_service: RecognitionService
    upload_dir: Path
    storage_dir: Path
    form_asset_dir: Path
    data_dir: Path
    static_dir: Path
    template_path: Path


class OCRRequestHandler(BaseHTTPRequestHandler):
    server_version = "OCRProject/1.0"

    @property
    def state(self) -> AppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, payload: dict[str, object], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        mime_type, _ = mimetypes.guess_type(str(path))
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _parse_multipart(self) -> cgi.FieldStorage:
        return cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )

    def _find_data_file(self, filename: str) -> Path | None:
        import unicodedata
        target_nfc = unicodedata.normalize("NFC", filename)
        data_dir = self.state.data_dir
        if not data_dir.exists():
            return None
        for entry in data_dir.iterdir():
            if unicodedata.normalize("NFC", entry.name) == target_nfc:
                base_resolved = data_dir.resolve()
                if base_resolved in entry.resolve().parents or entry.resolve() == base_resolved:
                    return entry
        return None

    def _resolve_within(self, base_dir: Path, relative_path: str) -> Path | None:
        candidate = (base_dir / relative_path).resolve()
        base_resolved = base_dir.resolve()
        if candidate == base_resolved or base_resolved in candidate.parents:
            return candidate
        return None

    def _is_route(self, path: str, *routes: str) -> bool:
        return path in routes

    def _split_form_detail_path(self, path: str) -> str | None:
        for prefix in ("/api/forms/", "/forms/"):
            if path.startswith(prefix):
                return path.replace(prefix, "", 1) or None
        return None

    def _read_image_bytes(self, field: cgi.FieldStorage, size_limit: int) -> tuple[bytes, str]:
        payload = field.file.read(size_limit + 1)
        if len(payload) > size_limit:
            raise ValueError(f"파일은 {size_limit // (1024 * 1024)}MB 이하만 업로드할 수 있습니다.")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                image_format = (image.format or "PNG").lower()
        except UnidentifiedImageError as error:
            raise ValueError("이미지 파일만 업로드할 수 있습니다.") from error
        extension_map = {"jpeg": ".jpg", "png": ".png", "bmp": ".bmp", "gif": ".gif", "webp": ".webp", "tiff": ".tif"}
        return payload, extension_map.get(image_format, ".png")

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path)
        if path == "/":
            self._send_file(self.state.template_path)
            return
        if path.startswith("/static/"):
            target = self._resolve_within(self.state.static_dir, path.replace("/static/", "", 1))
            if target is not None and target.exists() and target.is_file():
                self._send_file(target)
                return
        if path.startswith("/uploads/"):
            target = self._resolve_within(self.state.upload_dir, path.replace("/uploads/", "", 1))
            if target is not None and target.exists() and target.is_file():
                self._send_file(target)
                return
        if path.startswith("/form-assets/"):
            target = self._resolve_within(self.state.form_asset_dir, path.replace("/form-assets/", "", 1))
            if target is not None and target.exists() and target.is_file():
                self._send_file(target)
                return
        if path.startswith("/data/"):
            target = self._find_data_file(path.replace("/data/", "", 1))
            if target is not None:
                self._send_file(target)
                return
        if self._is_route(path, "/api/form-images"):
            images = []
            for img_path in FORM_IMAGES:
                if img_path.exists():
                    images.append(f"/data/{img_path.name}")
            self._send_json({"images": images})
            return
        if self._is_route(path, "/api/forms", "/forms"):
            forms = [form.to_dict() for form in self.state.form_repository.list_forms()]
            self._send_json({"forms": forms})
            return
        form_id = self._split_form_detail_path(path)
        if form_id is not None:
            form = self.state.form_repository.get_form(form_id)
            if form is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "양식을 찾을 수 없습니다.")
                return
            self._send_json({"form": form.to_dict()})
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "지원하지 않는 경로입니다.")

    def do_POST(self) -> None:  # noqa: N802
        if self._is_route(self.path, "/api/upload", "/upload"):
            self._handle_upload()
            return
        if self._is_route(self.path, "/api/recognize", "/recognize"):
            self._handle_recognize()
            return
        if self._is_route(self.path, "/api/save", "/save"):
            self._handle_save()
            return
        if self._is_route(self.path, "/api/forms", "/forms"):
            self._handle_form_upsert()
            return
        if self._is_route(self.path, "/api/recognize-region"):
            self._handle_recognize_region()
            return
        if self._is_route(self.path, "/api/suggest"):
            self._handle_suggest()
            return
        if self._is_route(self.path, "/api/export-pdf"):
            self._handle_export_pdf()
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "지원하지 않는 경로입니다.")

    def _handle_form_upsert(self) -> None:
        try:
            multipart = self._parse_multipart()
            metadata_raw = multipart.getvalue("metadata")
            if not metadata_raw:
                raise ValueError("양식 메타데이터가 없습니다.")
            metadata = json.loads(str(metadata_raw))
            if not isinstance(metadata, dict):
                raise ValueError("양식 메타데이터 형식이 잘못되었습니다.")
            current_form = self.state.form_repository.get_form(str(metadata.get("id", "")).strip())
            template_image = current_form.template_image if current_form is not None else None
            remove_template_image = str(metadata.get("remove_template_image", "")).lower() == "true"
            if remove_template_image:
                template_image = None
            template_field = multipart["template_image"] if "template_image" in multipart else None
            draft_form = validate_form_payload(metadata, template_image=template_image)
            if template_field is not None and getattr(template_field, "filename", ""):
                image_bytes, extension = self._read_image_bytes(template_field, MAX_FORM_TEMPLATE_BYTES)
                asset_name = f"{draft_form.form_id}{extension}"
                destination = self.state.form_asset_dir / asset_name
                destination.write_bytes(image_bytes)
                draft_form.template_image = f"/form-assets/{asset_name}"
            self.state.form_repository.upsert_form(draft_form)
            self._send_json({"form": draft_form.to_dict()})
        except (ValueError, json.JSONDecodeError) as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))

    def _handle_upload(self) -> None:
        try:
            multipart = self._parse_multipart()
            files_field = multipart["files"] if "files" in multipart else []
            fields = files_field if isinstance(files_field, list) else [files_field]
            session_id = uuid.uuid4().hex
            session_dir = self.state.upload_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            uploaded_files: list[dict[str, object]] = []
            for index, field in enumerate(fields):
                if not getattr(field, "filename", ""):
                    continue
                image_bytes, extension = self._read_image_bytes(field, MAX_UPLOAD_BYTES)
                original_name = Path(field.filename).name
                stem = Path(original_name).stem or f"image_{index + 1}"
                stored_name = f"{index:04d}_{stem}{extension}"
                destination = session_dir / stored_name
                destination.write_bytes(image_bytes)
                uploaded_files.append(
                    {
                        "name": original_name,
                        "stored_name": stored_name,
                        "url": f"/uploads/{session_id}/{stored_name}",
                        "path": str(destination),
                        "size": len(image_bytes),
                    }
                )
            if not uploaded_files:
                raise ValueError("업로드할 이미지가 없습니다.")
            (session_dir / "manifest.json").write_text(
                json.dumps(uploaded_files, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._send_json({"session_id": session_id, "files": uploaded_files})
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))

    def _handle_recognize(self) -> None:
        try:
            payload = self._read_json_body()
            session_id = str(payload.get("session_id", "")).strip()
            form_id = str(payload.get("form_id", "")).strip()
            session_dir = self.state.upload_dir / session_id
            manifest_path = session_dir / "manifest.json"
            image_paths: list[Path] = []
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                image_paths = [session_dir / item["stored_name"] for item in manifest]
            elif session_dir.exists():
                image_paths = sorted(
                    path for path in session_dir.iterdir() if path.is_file() and path.name != "manifest.json"
                )
            if not image_paths:
                raise ValueError("업로드된 이미지가 없습니다.")
            form = self.state.form_repository.get_form(form_id)
            if form is None:
                forms = self.state.form_repository.list_forms()
                form = forms[0] if forms else None
            if form is None:
                raise ValueError("사용 가능한 양식이 없습니다.")
            template_images = [
                f"/data/{p.name}" for p in FORM_IMAGES if p.exists()
            ]
            documents = self.state.recognition_service.recognize_many(
                image_paths,
                fields=form.fields,
                template_images=template_images,
                prefer_annotation_fallback=False,
            )
            low_confidence_lines = [
                region.text
                for document in documents
                for region in document.regions
                if region.confidence < self.state.recognition_service.model_config.confidence_threshold
            ]
            prefill = build_prefill(form, documents, low_confidence_lines)
            self._send_json(
                {
                    "session_id": session_id,
                    "documents": [
                        document.to_dict(self.state.recognition_service.model_config.confidence_threshold)
                        for document in documents
                    ],
                    "form": form.to_dict(),
                    "prefill": prefill,
                    "template_images": template_images,
                }
            )
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"인식 중 오류가 발생했습니다: {error}")

    def _handle_recognize_region(self) -> None:
        try:
            payload = self._read_json_body()
            session_id = str(payload.get("session_id", "")).strip()
            image_index = int(payload.get("image_index", 0))
            form_id = str(payload.get("form_id", "")).strip()
            tmpl_x = int(payload.get("x", 0))
            tmpl_y = int(payload.get("y", 0))
            tmpl_w = int(payload.get("width", 0))
            tmpl_h = int(payload.get("height", 0))
            if not session_id:
                raise ValueError("세션 ID가 필요합니다.")
            if tmpl_w <= 0 or tmpl_h <= 0:
                raise ValueError("유효한 영역 크기가 필요합니다.")
            session_dir = self.state.upload_dir / session_id
            manifest_path = session_dir / "manifest.json"
            image_paths: list[Path] = []
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                image_paths = [session_dir / item["stored_name"] for item in manifest]
            if not image_paths or image_index >= len(image_paths):
                raise ValueError("이미지를 찾을 수 없습니다.")
            image_path = image_paths[image_index]
            form = self.state.form_repository.get_form(form_id)
            template_image_str = None
            if image_index < len(FORM_IMAGES) and FORM_IMAGES[image_index].exists():
                template_image_str = f"/data/{FORM_IMAGES[image_index].name}"
            elif form:
                template_image_str = form.template_image
            template_size = self.state.recognition_service._template_size(template_image_str)
            with Image.open(image_path) as raw:
                img = raw.convert("RGB")
                img_w, img_h = img.size
            if template_size and template_size[0] > 0 and template_size[1] > 0:
                sx = img_w / template_size[0]
                sy = img_h / template_size[1]
            else:
                sx, sy = 1.0, 1.0
            ix = max(0, round(tmpl_x * sx))
            iy = max(0, round(tmpl_y * sy))
            iw = min(round(tmpl_w * sx), img_w - ix)
            ih = min(round(tmpl_h * sy), img_h - iy)
            if iw <= 0 or ih <= 0:
                raise ValueError("좌표가 이미지 범위를 벗어났습니다.")
            with Image.open(image_path) as raw:
                img = raw.convert("RGB")
                crop = img.crop((ix, iy, ix + iw, iy + ih))
            template = self.state.recognition_service._load_template_for_image(template_image_str, (img_w, img_h))
            if template is not None:
                template_crop = template.crop((ix, iy, ix + iw, iy + ih))
                crop = self.state.recognition_service._isolate_handwriting_crop(crop, template_crop)
                if not self.state.recognition_service._has_sufficient_ink(crop):
                    self._send_json({"text": "", "confidence": 0.0, "candidates": [], "source": "empty_crop"})
                    return
            try:
                prediction = self.state.recognition_service.engine.predict_crop(crop)
            except Exception:
                self._send_json({"text": "", "confidence": 0.0, "candidates": [], "source": "unavailable"})
                return
            self._send_json({
                "text": prediction.text,
                "confidence": prediction.confidence,
                "candidates": prediction.candidates,
                "source": prediction.source,
            })
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"영역 인식 오류: {error}")

    def _handle_save(self) -> None:
        try:
            payload = self._read_json_body()
            session_id = str(payload.get("session_id", "")).strip()
            form_id    = str(payload.get("form_id", "")).strip()
            birthdate  = str(payload.get("birthdate", "")).strip()
            values     = payload.get("values", {})
            recognition = payload.get("recognition", {})

            if not session_id or not form_id:
                raise ValueError("저장할 세션과 양식 정보가 필요합니다.")
            clean_bd = "".join(c for c in birthdate if c.isdigit())
            if len(clean_bd) < 6:
                raise ValueError("암호화를 위한 생년월일 6자리(YYMMDD)를 입력해야 합니다.")

            full_payload = {
                "session_id":  session_id,
                "form_id":     form_id,
                "values":      values,
                "recognition": recognition,
            }

            public_path, private_path, encrypted = save_two_files(
                full_payload,
                birthdate=clean_bd,
                storage_dir=self.state.storage_dir,
                session_id=session_id,
                form_id=form_id,
            )

            self.state.form_repository.save_submission(
                form_id=form_id,
                session_id=session_id,
                storage_path=str(public_path),
            )

            self._send_json({
                "public_path":  str(public_path),
                "private_path": str(private_path),
                "encrypted":    encrypted,
                "message": (
                    "저장 완료\n"
                    f"① 공개용(마스킹): {public_path.name}\n"
                    f"② {'암호화' if encrypted else '전체(미암호화)'}: {private_path.name}"
                ),
            })
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))


    def _handle_suggest(self) -> None:
        """
        POST /api/suggest
        Body: { text, field_name?, candidates?, context? }
        → { suggestions: string[], source: "claude"|"beam_search" }
        """
        try:
            payload       = self._read_json_body()
            text          = str(payload.get("text", "")).strip()
            field_name    = str(payload.get("field_name", "")).strip()
            raw_cands     = payload.get("candidates", [])
            raw_ctx       = payload.get("context", [])

            if not text:
                raise ValueError("text 파라미터가 필요합니다.")

            candidates    = [str(c) for c in raw_cands if str(c).strip()] if isinstance(raw_cands, list) else []
            context_texts = [str(c) for c in raw_ctx  if str(c).strip()] if isinstance(raw_ctx,   list) else []

            from .suggestion import get_suggestions
            suggestions, source = get_suggestions(
                text=text,
                field_name=field_name,
                candidates=candidates,
                context_texts=context_texts,
            )
            self._send_json({"suggestions": suggestions, "source": source})
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"교정 제안 오류: {error}")

    def _handle_export_pdf(self) -> None:
        """
        POST /api/export-pdf
        Body: { session_id, pages: [{image_url, width, height, regions:[{bbox,text,low_confidence}]}] }
        → PDF 파일 (application/pdf)
        """
        try:
            import io as _io
            from PIL import Image as _Image, ImageDraw as _IDraw, ImageFont as _IFont

            payload    = self._read_json_body()
            session_id = str(payload.get("session_id", "")).strip()
            pages      = payload.get("pages", [])
            if not pages:
                raise ValueError("pages 데이터가 없습니다.")

            # 한국어 폰트 탐색 (Windows: Malgun Gothic, Linux/Mac: 기본폰트)
            def _find_font(size: int) -> "_IFont.FreeTypeFont | _IFont.ImageFont":
                font_candidates = [
                    r"C:\Windows\Fonts\malgun.ttf",
                    r"C:\Windows\Fonts\malgunbd.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
                ]
                for path in font_candidates:
                    try:
                        return _IFont.truetype(path, size)
                    except (IOError, OSError):
                        continue
                return _IFont.load_default()

            pdf_images: list[_Image.Image] = []

            for page in pages:
                # 업로드된 이미지 경로 찾기
                image_url: str = page.get("image_url") or ""
                # /uploads/session_id/filename 형태
                img_path = None
                if image_url.startswith("/uploads/"):
                    rel = image_url.replace("/uploads/", "", 1)
                    img_path = self.state.upload_dir / rel
                if img_path is None or not img_path.exists():
                    continue

                with _Image.open(img_path) as raw:
                    img = raw.convert("RGB")

                draw     = _IDraw.Draw(img)
                img_w, img_h = img.size
                orig_w   = page.get("width",  img_w) or img_w
                orig_h   = page.get("height", img_h) or img_h
                sx       = img_w / orig_w
                sy       = img_h / orig_h

                for region in (page.get("regions") or []):
                    bbox = region.get("bbox", {})
                    text = str(region.get("text") or "").strip()
                    if not text:
                        continue

                    x1 = round(bbox.get("x1", 0) * sx)
                    y1 = round(bbox.get("y1", 0) * sy)
                    x2 = round(bbox.get("x2", x1 + 50) * sx)
                    y2 = round(bbox.get("y2", y1 + 20) * sy)
                    bw = max(x2 - x1, 20)
                    bh = max(y2 - y1, 14)

                    # 흰 배경 덮기
                    draw.rectangle([x1, y1, x2, y2], fill="white", outline="#22a64a", width=2)

                    # 폰트 크기 = 박스 높이 × 0.65 (최소 10, 최대 28)
                    fs     = max(10, min(28, int(bh * 0.65)))
                    font   = _find_font(fs)

                    # 텍스트가 박스에 맞도록 줄바꿈
                    lines: list[str] = []
                    words  = list(text)  # 한국어: 글자 단위로 처리
                    line   = ""
                    for ch in words:
                        test = line + ch
                        try:
                            tw = font.getlength(test)
                        except AttributeError:
                            tw = len(test) * fs * 0.6
                        if tw <= bw - 4:
                            line = test
                        else:
                            if line:
                                lines.append(line)
                            line = ch
                    if line:
                        lines.append(line)

                    # 텍스트 그리기 (중앙 정렬)
                    total_h  = len(lines) * (fs + 2)
                    start_y  = y1 + max(0, (bh - total_h) // 2)
                    for li, ln in enumerate(lines):
                        try:
                            tw = font.getlength(ln)
                        except AttributeError:
                            tw = len(ln) * fs * 0.6
                        tx = x1 + max(0, (bw - tw) // 2)
                        ty = start_y + li * (fs + 2)
                        draw.text((tx, ty), ln, fill="#1a4f26", font=font)

                pdf_images.append(img)

            if not pdf_images:
                raise ValueError("PDF로 변환할 페이지가 없습니다.")

            # PDF 바이트 생성
            buf = _io.BytesIO()
            pdf_images[0].save(
                buf,
                format="PDF",
                save_all=True,
                append_images=pdf_images[1:],
                resolution=150,
            )
            pdf_bytes = buf.getvalue()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="recognition_result.pdf"')
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)

        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"PDF 생성 오류: {error}")


def run_server(config: WebConfig) -> None:
    repository = FormRepository(DB_PATH)
    repository.initialize()
    app_state = AppState(
        form_repository=repository,
        recognition_service=RecognitionService(),
        upload_dir=UPLOAD_DIR,
        storage_dir=STORAGE_DIR,
        form_asset_dir=FORM_ASSET_DIR,
        data_dir=DATA_DIR,
        static_dir=Path(__file__).resolve().parent / "static",
        template_path=Path(__file__).resolve().parent / "templates" / "index.html",
    )
    app_state.upload_dir.mkdir(parents=True, exist_ok=True)
    app_state.storage_dir.mkdir(parents=True, exist_ok=True)
    app_state.form_asset_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((config.host, config.port), OCRRequestHandler)
    server.app_state = app_state  # type: ignore[attr-defined]
    print(f"Server running at http://{config.host}:{config.port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
