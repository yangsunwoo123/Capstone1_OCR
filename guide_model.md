# guide_model.md — 모델링 참고서

## 사용 모델
- `ddobokki/ko-trocr` (Hugging Face Hub)
- TrOCR 계열 (Vision Encoder + Text Decoder)
- **인식(recognition) 모델**임. 이미지 내 텍스트 영역 검출(detection)은 별도 단계 필요.

---

## 작업 단계

### 1. Zero-shot 평가 (MVP 1단계)
- [ ] `TrOCRProcessor`, `VisionEncoderDecoderModel` 로드
- [ ] `./data/many_handwrite/rawdata` 샘플 일부로 inference
- [ ] 결과와 라벨을 육안 비교
- [ ] CER / WER 초기 수치 기록

### 2. Fine-tuning — 일반 손글씨 (MVP 2단계)
- [ ] train/val/test 분할 완료 후 진행
- 결정 필요 항목
  - [ ] Optimizer (AdamW 등)
  - [ ] Learning rate
  - [ ] Batch size
  - [ ] Epoch / early stopping 기준
  - [ ] Gradient accumulation 필요 여부
- [ ] 학습 중 validation CER 기록
- [ ] 체크포인트 저장 위치 및 규칙

### 3. 노인 손글씨 추가 Fine-tuning (MVP 3단계 — 아직 진행하지 않음)
- [ ] **일반 손글씨 fine-tuning에 사용한 데이터 양보다 적은 양**으로 수행 (PROJECT.md 명시)
- [ ] Catastrophic forgetting 점검 (일반 손글씨 테스트셋 성능 재평가)

### 4. Development 단계 (후속)
- [ ] Data augmentation 적용
  - Geometric transformation
  - Elastic distortion
  - Noise injection
- [ ] 증강 데이터로 **처음부터 재학습**
- [ ] 손글씨 인식 관련 논문 서베이 후 개선안 반영

---

## 평가 지표
- [ ] CER (Character Error Rate) — 주 지표
- [ ] WER (Word Error Rate)
- [ ] 저신뢰도 샘플 비율 (서비스 UX 직결)

---

## Inference 시 추출해야 하는 정보
백엔드/프론트와 연동되는 출력물:
- [ ] 인식된 텍스트
- [ ] 토큰/문자별 confidence score (저신뢰도 판정용)
- [ ] 인식 영역 bbox (라운드박스 표시용)
  - **확인 필요**: ko-trocr은 라인/영역을 입력으로 받으므로, bbox는 별도 detection 단계 결과이다. Detection 방식은 결정 필요.

---

## 주의
- ko-trocr은 인식 모델이므로, PROJECT.md의 "라운드박스" 표시는 detection 결과를 전제로 한다. Detection 방식(기존 OCR detector 사용 / 별도 학습 / 규칙 기반 등)은 코드 작성 전에 결정해야 한다.
- 모델 이름/경로를 임의로 바꾸지 말 것 (`ddobokki/ko-trocr` 고정).
