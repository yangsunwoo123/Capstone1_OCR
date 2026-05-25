# Feature Usage

## 환경 활성화

```bash
conda activate ocr
```

필수 패키지가 없으면 아래로 맞춥니다.

```bash
python -m pip install -r requirements.txt sentencepiece
```

---

## 1. 데이터 구조 점검

`many_handwrite` 데이터셋의 이미지-라벨 매칭, 해상도, bbox 구조를 점검합니다.

```bash
python main.py phase0-report
```

출력:
- `reports/phase0_dataset_report.json`

관련 모듈:
- `ocr_app/data.py`
- `pipeline_dataset.py`

---

## 2. Zero-shot 베이스라인

`ddobokki/ko-trocr`로 일부 crop에 대해 zero-shot 평가를 수행합니다.

```bash
python main.py zero-shot --sample-pages 1 --max-items 4
```

주요 옵션:
- `--sample-pages`: 샘플링할 문서 수
- `--max-items`: 평가할 bbox crop 수
- `--batch-size`: 추론 배치 크기
- `--save-visuals`: bbox 오버레이 이미지 저장

출력:
- `runs/phase1_zero_shot/.../metrics.json`
- `runs/phase1_zero_shot/.../predictions.jsonl`
- `runs/phase1_zero_shot/.../report.md`

관련 모듈:
- `recognition_model.py`
- `pipeline_dataset.py`
- `text_metrics.py`
- `report_writer.py`

---

## 3. 일반 손글씨 fine-tuning 실행

`many_handwrite`를 train/validation/test로 분리하고, test 데이터셋을 별도 폴더로 보존한 뒤 일반 손글씨 fine-tuning을 실행합니다.

```bash
python main.py train-general --sample-limit 16
```

작은 샘플로 빠르게 검증하려면 아래처럼 validation/test 평가 수를 줄일 수 있습니다.

```bash
python main.py train-general --sample-limit 16 --val-sample-limit 8 --test-sample-limit 8
```

출력:
- `artifacts/phase2_general_finetune/many_handwrite/training_plan.json`
- `artifacts/phase2_general_finetune/many_handwrite/best_checkpoint/`
- `artifacts/phase2_general_finetune/many_handwrite/test_metrics.json`
- `artifacts/phase2_general_finetune/many_handwrite/test_dataset/`

관련 모듈:
- `ocr_app/training.py`
- `ocr_app/data.py`

---

## 4. 노인 손글씨 추가 학습 계획 생성

`public_old` 데이터를 대상으로 추가 fine-tuning용 계획을 생성합니다.

```bash
python main.py train-elderly --sample-limit 12
```

출력:
- `artifacts/phase3_elderly_finetune/public_old/training_plan.json`

관련 모듈:
- `ocr_app/training.py`
- `ocr_app/data.py`

---

## 5. Development 증강 파이프라인 확인

증강 샘플 미리보기와 재학습 계획을 생성합니다.

```bash
python main.py train-development --sample-limit 8
```

출력:
- `reports/phase9_development.json`
- `artifacts/phase9_development/preview/*.png`

관련 모듈:
- `ocr_app/training.py`

---

## 6. 양식 DB 초기화

기본 양식과 submission 테이블을 생성합니다.

```bash
python main.py seed-forms
```

출력:
- `forms.db`

관련 모듈:
- `ocr_app/forms.py`

---

## 7. 통합 인식/저장 체크

인식 결과 생성, 양식 prefill, 암호화 저장까지 한 번에 점검합니다.

```bash
python main.py integration-check --birthdate 900101
```

출력:
- `reports/phase7_integration_check.json`
- `storage/*.json.enc`

관련 모듈:
- `ocr_app/inference.py`
- `ocr_app/forms.py`
- `ocr_app/storage.py`

---

## 8. 웹 서버 실행

업로드, 인식, 양식 편집, 저장 UI를 로컬 서버로 실행합니다.

```bash
python main.py serve --host 127.0.0.1 --port 8000
```

브라우저 접속:
- `http://127.0.0.1:8000`

관련 모듈:
- `ocr_app/web.py`
- `ocr_app/templates/index.html`
- `ocr_app/static/app.js`
- `ocr_app/static/style.css`

---

## 파일명 규칙

기존 phase 이름 기반 파일은 기능명 기준으로 정리했습니다.

- `phase1_data.py` -> `pipeline_dataset.py`
- `phase1_metrics.py` -> `text_metrics.py`
- `phase1_model.py` -> `recognition_model.py`
- `phase1_report.py` -> `report_writer.py`
