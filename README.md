# 직불금 신청서 OCR 웹 시스템

손으로 작성된 **기본직접지불금 지급대상자 등록신청서** 스캔본을 업로드하면 AI가 손글씨를 자동 인식하고, 결과를 검토·수정 후 PDF로 저장하는 웹 시스템입니다.

---

## 주요 기능

- **손글씨 자동 인식** — GPT-4o-mini Vision API로 칸별 크롭 이미지를 분석해 텍스트 추출
- **신뢰도 기반 오버레이** — 고신뢰(초록 박스) / 저신뢰(빨간 박스)로 결과를 시각적으로 표시
- **빨간 박스 클릭 수정** — 저신뢰 항목 클릭 시 AI 추천 단어 + 직접 입력 팝업
- **인쇄 기능** — 박스 테두리 제거 후 깨끗한 양식으로 인쇄
- **PDF 저장** — 빈 양식 위에 인식 텍스트를 합성한 PDF 다운로드
- **개인정보 보호** — 저장 시 공개용(마스킹) + 전체(생년월일 암호화) 두 벌로 저장

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| 백엔드 | Python 3.11 (`http.server` 기반, 외부 프레임워크 없음) |
| 이미지 처리 | Pillow 11.3.0 |
| AI OCR | OpenAI GPT-4o-mini Vision API |
| DB | SQLite (`forms.db`) |
| 프론트엔드 | 순수 HTML / CSS / JavaScript (프레임워크 없음) |

---

## 빠른 시작

### 1. 사전 요구사항

- Python 3.11+
- OpenAI API 키

### 2. 의존성 설치

```bash
pip install pillow openai
```

### 3. 환경변수 설정

프로젝트 루트에 `.env` 파일 생성:

```
OPENAI_API_KEY=sk-proj-...
```

### 4. 서버 실행

**Windows (PowerShell)**
```powershell
$env:OPENAI_API_KEY = (Get-Content ".env" | Where-Object { $_ -match "^OPENAI_API_KEY=" } | ForEach-Object { $_.Split("=", 2)[1] })
python main.py serve
```

**Linux / macOS**
```bash
export OPENAI_API_KEY=$(grep OPENAI_API_KEY .env | cut -d= -f2)
python main.py serve
```

브라우저에서 `http://localhost:8000` 접속

---

## 사용 방법

1. **이미지 업로드** — 직불금 신청서 스캔본을 드래그하거나 클릭해서 업로드
2. **인식 시작** — 중앙의 `인식 시작` 버튼 클릭
3. **결과 검토** — 초록 박스(고신뢰) / 빨간 박스(저신뢰) 확인
4. **수정** — 빨간 박스 클릭 → AI 추천 선택 또는 직접 입력
5. **출력** — `인쇄` 버튼 또는 `PDF 저장` 버튼

---

## 프로젝트 구조

```
capstone1_ver2-main/
├── main.py                    # 진입점
├── forms.db                   # SQLite 양식 DB
├── form_assets/
│   └── jikbulgeum1.png        # 빈 양식 원본 (배경 템플릿)
├── uploads/                   # 업로드 이미지 임시 저장
├── storage/                   # 저장 결과물 (마스킹 + 암호화)
└── ocr_app/
    ├── web.py                 # HTTP 서버 및 API 라우팅
    ├── inference.py           # OCR 인식 로직
    ├── forms.py               # 양식 정의 및 DB 관리
    ├── config.py              # 설정 상수
    ├── masking.py             # 개인정보 마스킹
    ├── storage.py             # 저장 (공개용 + 암호화)
    ├── static/
    │   ├── app.js             # 프론트엔드 로직
    │   └── style.css          # 스타일시트
    └── templates/
        └── index.html         # 메인 HTML
```

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 메인 페이지 |
| GET | `/api/forms` | 양식 목록 조회 |
| POST | `/api/upload` | 이미지 업로드 |
| POST | `/api/recognize` | OCR 인식 실행 |
| POST | `/api/recognize-region` | 단일 영역 재인식 |
| POST | `/api/save` | 결과 저장 |
| POST | `/api/export-pdf` | PDF 생성 및 다운로드 |

---

## OCR 동작 방식

페이지 전체를 한 번에 보내는 방식이 아닌 **칸별 크롭 이미지를 개별 전송**하는 방식을 사용합니다.

- GPT가 양식 문맥을 보고 단어를 추론·유추하는 것을 방지
- 크롭된 이미지만 전달하면 실제 필기 글자만 읽어 정확도 향상
- 최대 30개 크롭을 배치로 처리, 배치 사이 1.5초 대기 (API 레이트 리밋 방지)

---

## 보안

- API 키는 반드시 환경변수로 관리 (`os.environ.get("OPENAI_API_KEY")`)
- `.env` 파일은 `.gitignore`에 포함되어 있어 커밋되지 않음
- 개인정보(주민번호 등)는 저장 시 마스킹 처리
- 생년월일은 암호화하여 별도 저장

---

## 라이선스

본 프로젝트는 캡스톤 디자인 프로젝트로 제작되었습니다.
