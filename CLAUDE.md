# CLAUDE.md — 직불금 OCR 웹 시스템 작업 인계서

> 이 파일은 새 Claude 대화 창에서 바로 작업을 이어갈 수 있도록 프로젝트 전체 현황을 정리한 문서입니다.
> 새 대화를 시작하면 이 파일을 먼저 읽고 작업을 시작하세요.

---

## 프로젝트 개요

**목적**: 화성시 공무원이 손으로 작성된 직불금 신청서(기본직접지불금 지급대상자 등록신청서)를 
스캔 후 웹에 업로드하면 AI가 손글씨를 자동 인식하고, 공무원이 결과를 검토·수정 후 PDF로 저장하는 시스템.

**기술 스택**
- Python 3.11 (표준 라이브러리만 사용, `http.server` 기반 커스텀 서버)
- Pillow 11.3.0 (이미지 처리, PDF 생성)
- SQLite (`forms.db`) — 양식(Form) 정의 저장
- OpenAI GPT-4o-mini Vision API — OCR 엔진 폴백 (KoTrOCR 미설치 환경)
- 순수 HTML/CSS/JS 프론트엔드 (프레임워크 없음)

**서버 시작 방법**
```powershell
# .env 파일에서 API 키 읽어서 환경변수 설정 후 서버 시작
$env:OPENAI_API_KEY = (Get-Content ".env" | Where-Object { $_ -match "^OPENAI_API_KEY=" } | ForEach-Object { $_.Split("=", 2)[1] })
Start-Process -FilePath "python" -ArgumentList "main.py", "serve" -WorkingDirectory (Get-Location).Path -WindowStyle Hidden
# 접속: http://localhost:8000
```

---

## 디렉토리 구조

```
capstone1_ver2-main/
├── main.py                    # 진입점 (python main.py serve)
├── forms.db                   # SQLite — 양식 정의 (template_image_path 포함)
├── .env                       # OPENAI_API_KEY=sk-proj-... (gitignore 대상)
├── form_assets/
│   └── jikbulgeum1.png        # 빈 양식 원본 (2946×2086) — 배경용 템플릿
├── data/
│   ├── 직불금1.png            # 빈 양식 원본 (FORM_IMAGES[0])
│   └── (직불금테스트1.png)    # 테스트용 스캔 이미지
├── test_img.png               # 한글 경로 우회용 테스트 이미지 복사본
├── uploads/                   # 업로드된 이미지 임시 저장
├── storage/                   # 저장된 JSON 결과물
└── ocr_app/
    ├── web.py                 # HTTP 서버 핸들러 (라우팅, API 엔드포인트)
    ├── inference.py           # OCR 인식 로직 (GPT-4o-mini Vision 크롭 방식)
    ├── forms.py               # 양식 정의 및 DB 관리
    ├── config.py              # 경로·설정 상수
    ├── ocr_engine.py          # KoTrOCR 엔진 (미설치 → MissingModelDependencyError)
    ├── storage.py             # 저장 (공개용 마스킹 + 암호화)
    ├── masking.py             # 개인정보 마스킹
    ├── suggestion.py          # 교정 제안
    ├── static/
    │   ├── app.js             # 프론트엔드 전체 로직
    │   └── style.css          # 스타일시트
    └── templates/
        └── index.html         # 메인 HTML
```

---

## 핵심 설계 결정사항

### OCR 방식: GPT-4o-mini Vision 크롭 전송
- KoTrOCR(`ddobokki/ko-trocr`) 미설치 → GPT-4o-mini Vision API를 폴백으로 사용
- **핵심**: 페이지 전체 + 좌표 방식이 아닌 **칸별 크롭 이미지를 직접 전송**하는 방식
  - 이유: GPT가 양식 문맥을 보고 단어를 유추·추론하는 것을 방지
  - 크롭 이미지만 보여주면 실제 필기 글자만 읽음
- 배치: 한 번에 최대 30개 크롭, 배치 사이 1.5초 sleep (레이트 리밋 방지)
- 결과 캐시: `gpt_cache: dict[int, OCRPrediction]` — **인덱스(int)** 기반 (field_name이 None이라 이름 기반 불가)

### 템플릿 diff 기반 손글씨 감지
- 빈 양식(template)과 업로드된 스캔 이미지를 픽셀 단위로 비교
- 차이가 큰 영역 = 손글씨가 쓰인 칸 → BoundingBox 생성
- `_detect_handwriting_from_template()` 함수에서 처리
- 감지된 영역 수가 150개 초과 시 템플릿 불일치로 판단 → 빈 리스트 반환

### 신뢰도 임계값
- `confidence_threshold = 0.40` (config.py)
- 0.40 이상 → 초록 박스 (고신뢰)
- 0.40 미만 → 빨간 박스 (저신뢰, 클릭하여 수정)

### DB 상태 (forms.db)
```
id: 'direct-payment'
name: '기본직접지불금 등록신청서'  
template_image_path: '/form-assets/jikbulgeum1.png'
```

---

## API 엔드포인트 목록

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 메인 HTML 페이지 |
| GET | `/static/<file>` | JS/CSS 정적 파일 |
| GET | `/form-assets/<file>` | 양식 원본 이미지 |
| GET | `/data/<file>` | 데이터 디렉토리 이미지 |
| GET | `/api/forms` | 양식 목록 |
| GET | `/api/forms/<id>` | 특정 양식 정보 (template_image 포함) |
| POST | `/api/upload` | 이미지 업로드 |
| GET | `/api/uploads/<session>/<file>` | 업로드된 이미지 조회 |
| POST | `/api/recognize` | OCR 인식 실행 |
| POST | `/api/recognize-region` | 단일 영역 재인식 |
| POST | `/api/save` | 결과 저장 (마스킹+암호화) |
| POST | `/api/export-pdf` | PDF 파일 생성 및 다운로드 |

---

## 주요 파일별 현재 구현 상태

### `ocr_app/inference.py`

**`_demo_gpt_batch_recognize(image, field_boxes, api_key, template=None)`**
- 각 영역을 크롭 → 손글씨 격리(template diff) → min 120×48로 업스케일 → PNG base64 인코딩
- 30개씩 배치로 GPT API 호출
- 반환: `dict[int, OCRPrediction]` — 키는 field_boxes 내 인덱스

**`recognize_document()`**
- `use_gpt_batch = not self.engine.dependencies_available()` 로 GPT 모드 진입
- `for i, (box, field_name) in enumerate(field_boxes):` — enumerate로 인덱스 추적
- `elif i in gpt_cache:` — 인덱스로 GPT 결과 조회 (field_name이 None이라도 동작)

**`_detect_handwriting_from_template()`**
- 픽셀 diff → mask 생성 → row/col band로 박스 분리
- `boxes > 150` → 빈 리스트 반환 (sanity check)

### `ocr_app/web.py`

**`_handle_export_pdf()`** — POST `/api/export-pdf`
- 요청: `{session_id, pages: [{image_url, width, height, regions:[{bbox, text}]}]}`
- 빈 양식 원본을 불러와 각 region에 흰 rect + 한국어 텍스트 (Malgun Gothic) 그림
- Pillow로 다중 페이지 PDF 생성 → `application/pdf` 바이너리 응답

**폰트 경로 우선순위** (`_handle_export_pdf` 내부)
```python
font_candidates = [
    r"C:\Windows\Fonts\malgun.ttf",         # Windows
    r"C:\Windows\Fonts\malgunbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",       # macOS
]
```

### `ocr_app/static/app.js`

**`renderDocumentWithBoxes(index)`** — 인식 결과 오버레이 렌더링
```javascript
// 배경: 빈 양식 원본 사용 (state.selectedForm.template_image → /form-assets/jikbulgeum1.png)
const templateSrc = state.selectedForm && state.selectedForm.template_image
  ? state.selectedForm.template_image : null;
image.src = templateSrc || uploaded.url;

// 동적 폰트 크기: 박스 높이에 비례
const fontSize = Math.max(7, Math.min(14, renderedH * 0.50));
textLabel.style.fontSize = `${fontSize}px`;

// 툴팁으로 전체 텍스트 표시
box.title = finalText;
```

**`openBoxPopup(box, fieldKey, region, overlayEl)`** — 빨간 박스 클릭 팝업
- AI 인식 텍스트를 칩(chip) 형태로 즉시 표시 (비동기 버튼 없음)
- 아래에 공무원 직접 타이핑 가능한 입력칸

**`exportPdf()`** — PDF 저장 버튼
- `/api/export-pdf`에 POST
- 응답 blob을 `<a download>` 링크로 다운로드

**`state` 객체 주요 필드**
```javascript
{
  selectedFormId: "direct-payment",
  selectedForm: null,        // /api/recognize 응답의 form 객체 (template_image 포함)
  formValues: {},            // 공무원이 수정한 값: {fieldKey: text}
  lowConfidenceMap: {},      // 저신뢰 필드: {fieldKey: {text, candidates, confidence}}
  documents: [],             // 인식 결과 documents 배열
  uploadedFiles: [],         // 업로드된 파일 {url, stored_name}
  templateImages: [],        // 템플릿 이미지 URL 배열
}
```

### `ocr_app/static/style.css`

**레이아웃 그리드** (현재)
```css
.shell {
  grid-template-columns: minmax(280px, 0.85fr) 160px minmax(540px, 1.6fr);
}
/* 왼쪽(업로드) : 가운데(인식버튼) : 오른쪽(결과) */
```

**박스 스타일** (현재)
```css
.box { overflow: hidden; }                          /* 텍스트 박스 안에 가둠 */
.box-text-label { white-space: nowrap; text-overflow: ellipsis; }  /* 말줄임 처리 */
.template-shell { border-radius: 12px; overflow: hidden; box-shadow: ...; }
```

---

## 보안 규칙 (절대 변경 금지)

1. **API 키 하드코딩 금지** — 반드시 `os.environ.get("OPENAI_API_KEY")` 사용
2. **`.env` 파일** — gitignore 대상, 절대 커밋하지 말 것
3. 저장 파일은 공개용(마스킹) + 전체(생년월일 암호화) 두 벌로 저장
4. 기존에 노출된 API 키가 있다면 반드시 https://platform.openai.com/api-keys 에서 교체

---

## 현재 알려진 이슈 및 미완성 사항

### 완료된 기능 ✅
- GPT-4o-mini Vision 크롭 기반 OCR (실제 필기 읽기, 추론 방지)
- 템플릿 diff 손글씨 감지 (49개 영역 감지 확인)
- 초록/빨간 박스 오버레이 (신뢰도 기반)
- 빨간 박스 클릭 → AI 제안 즉시 표시 + 직접 입력
- PDF 저장 (Malgun Gothic 한국어 폰트)
- 인식 결과 배경을 빈 양식 원본으로 교체
- 동적 폰트 크기 (박스 높이에 비례)
- 오른쪽 패널 확대 (1.6fr)

### 잠재적 개선 포인트 🔧
- KoTrOCR 설치 후 로컬 OCR 전환 (`pip install torch transformers`)
- 양식 페이지가 2~5페이지일 때 페이지별 템플릿 매핑 확인
- 빨간 박스에서 수정 확정 시 `state.lowConfidenceMap`에서 해당 키 삭제 (박스가 초록으로 바뀜)
- 저장 후 `save-result-card` UI 표시 확인

---

## 자주 하는 작업 참고

### 서버 재시작
```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# 위 서버 시작 방법 참고
```

### DB 내용 확인
```powershell
cd "c:\Users\sunwo\OneDrive\바탕 화면\양선우\웹 개발\캡스톤\capstone1_ver2-main"
python -c "import sqlite3; conn=sqlite3.connect('forms.db'); c=conn.cursor(); c.execute('SELECT id, name, template_image_path FROM forms'); [print(r) for r in c.fetchall()]"
```

### 테스트 인식 (CLI)
```python
# test_img.png = 직불금테스트1.png 복사본 (한글 경로 우회)
# data/직불금1.png = 빈 양식 원본 (템플릿)
from pathlib import Path
from ocr_app.inference import RecognitionService
svc = RecognitionService()
result = svc.recognize_document(
    Path("test_img.png"),
    template_image=f"/data/{svc._find_data_file('직불금1.png').name}" if svc._find_data_file('직불금1.png') else None
)
print(len(result.regions), "개 영역 인식")
```

---

## 참고서 목록 (기존 가이드)

- `guide_data.md` — 데이터 점검
- `guide_model.md` — 모델링 (KoTrOCR fine-tuning 계획)
- `guide_backend.md` — 백엔드 설계
- `guide_frontend.md` — 프론트엔드 설계
- `guide_security.md` — 보안/개인정보
- `guide_integration.md` — 통합 점검
