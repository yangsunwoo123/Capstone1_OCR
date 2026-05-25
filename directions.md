# directions.md — 작업 단계별 지시 가이드

이 문서는 프로젝트 진행 단계별로 **어떤 참고서를 Claude에게 제시하고**, **어떤 프롬프트로 작업을 지시해야 하는지**를 정리한 문서이다.

각 단계는 **첨부할 파일 목록**과 **프롬프트 예시**로 구성된다. 프롬프트는 그대로 복사하거나 상황에 맞게 수정해서 사용한다.

---

## Phase 0 · 프로젝트 초기 점검

**목적**: 데이터 구조와 환경 파악 (코드 작성 전)

**첨부 파일**
- `PROJECT.md`
- `guide.md`
- `CLAUDE.md`
- `guide_data.md`

**프롬프트 예시**
```
./data/many_handwrite/rawdata 와 ./data/many_handwrite/label 의 구조를 확인하고,
라벨 포맷, 이미지-라벨 매칭 규칙, 샘플 수, 해상도 분포를 점검해줘.
guide_data.md의 점검 사항을 순서대로 따르고, 확인이 끝나기 전에는 파싱 코드를 작성하지 마.
확인한 결과는 한국어로 요약해서 보고해줘.
```

---

## Phase 1 · Zero-shot 베이스라인 (MVP 1단계)

**목적**: ddobokki/ko-trocr 을 그대로 사용해 초기 성능 확인

**첨부 파일**
- `PROJECT.md`
- `guide_model.md`
- Phase 0에서 확인된 데이터 구조 요약

**프롬프트 예시**
```
ddobokki/ko-trocr을 로드해서 데이터셋의 일부 샘플에 zero-shot inference를 돌리고
CER과 WER을 기록하는 스크립트를 만들어줘.
guide_model.md의 "Zero-shot 평가" 섹션을 따르고, 결과는 별도 로그 파일에 남겨줘.
코드 블록을 워드 리포트에 넣을 수 있도록 정리해서 함께 보여줘.
```

---

## Phase 2 · Fine-tuning — 일반 손글씨 (MVP 2단계)

**첨부 파일**
- `guide_model.md`
- `guide_data.md`
- Phase 1 결과 요약

**프롬프트 예시**
```
일반 손글씨 데이터로 ko-trocr을 fine-tuning하는 학습 스크립트를 작성해줘.
guide_model.md의 "Fine-tuning — 일반 손글씨" 섹션의 결정 필요 항목
(optimizer, lr, batch size, epoch 등)을 먼저 나에게 물어보고,
내가 값을 정한 뒤에 코드를 작성해.
데이터 분할도 guide_data.md의 결정 필요 항목을 먼저 확인해.
```

---

## Phase 3 · 노인 손글씨 추가 Fine-tuning (MVP 3단계)

**첨부 파일**
- `guide_model.md`
- Phase 2 체크포인트 경로 및 설정

**프롬프트 예시**
```
Phase 2에서 학습된 체크포인트 위에 노인 손글씨 데이터로 추가 fine-tuning을 하는 스크립트를 만들어줘.
데이터 양은 Phase 2의 학습 데이터 양보다 적어야 한다 (PROJECT.md 명시).
학습 후에는 일반 손글씨 테스트셋 성능도 함께 평가해서 catastrophic forgetting을 점검해.
```

---

## Phase 4 · Inference / Confidence / Detection 파이프라인

**목적**: 백엔드 연동에 필요한 출력물(텍스트, bbox, confidence) 생성

**첨부 파일**
- `guide_model.md`
- `guide_integration.md`

**프롬프트 예시**
```
학습된 모델로 이미지 한 장을 받아서 텍스트, 문자별 confidence, bbox 좌표를 반환하는
inference 함수를 만들어줘.
TrOCR은 인식 모델이므로 텍스트 영역 검출(detection) 단계가 별도로 필요하다는 점이
guide_model.md에 정리되어 있어. detection 방식은 코드 작성 전에 옵션을 제시하고 내 결정을 받아.
```

---

## Phase 5 · 백엔드 개발

**첨부 파일**
- `guide_backend.md`
- `guide_integration.md`
- `guide_security.md`

**프롬프트 예시**
```
guide_backend.md의 엔드포인트 설계에 맞춰 백엔드 스켈레톤을 만들어줘.
프레임워크 선택 등 "결정 필요" 항목은 코드 작성 전에 나에게 먼저 물어봐.
저장 부분은 guide_security.md의 생년월일 기반 암호화 규칙을 따르고,
PROJECT.md에 없는 보안 기능은 임의로 추가하지 마.
```

---

## Phase 6 · 프론트엔드 개발

**첨부 파일**
- `guide_frontend.md`
- `guide_integration.md`

**프롬프트 예시**
```
guide_frontend.md에 정리된 레이아웃과 인터랙션을 구현하는 프론트엔드 스켈레톤을 만들어줘.
프레임워크 선택 등 "결정 필요" 항목은 먼저 물어봐.
라운드박스 색상은 PROJECT.md의 "파란색(일반) / 빨간색(저신뢰도)" 규칙을 정확히 지켜.
PROJECT.md에 없는 기능은 임의로 추가하지 마.
```

---

## Phase 7 · 통합 및 엔드투엔드 확인

**첨부 파일**
- `guide_integration.md`
- 지금까지의 백엔드 / 프론트엔드 / 모델 상태 요약

**프롬프트 예시**
```
guide_integration.md의 데이터 흐름 1~4단계가 실제로 동작하는지 확인할 수 있는
간단한 엔드투엔드 테스트 시나리오를 만들어줘.
bbox 좌표계와 confidence threshold 일관성도 함께 점검해.
```

---

## Phase 8 · 저장 / 보안 마무리

**첨부 파일**
- `guide_security.md`
- `guide_backend.md`

**프롬프트 예시**
```
저장 엔드포인트에 생년월일 기반 암호화를 붙여줘.
암호화 방식은 guide_security.md의 결정 필요 항목을 먼저 정리해서 내게 옵션을 제시하고,
내가 고른 방식대로 구현해.
"마스킹 or 잠금"은 양자택일이라는 점도 같이 확인해서 어느 쪽을 구현할지 먼저 물어봐.
```

---

## Phase 9 · Development 단계 (선택)

**첨부 파일**
- `guide_model.md`

**프롬프트 예시**
```
일반/노인 손글씨 데이터에 geometric transformation, elastic distortion, noise injection을
적용하는 augmentation 파이프라인을 만들고, 이 데이터로 ko-trocr을 처음부터 재학습하는
스크립트를 만들어줘. guide_model.md의 "Development 단계" 섹션을 따라.
```

---

## 공통 규칙
- 프롬프트에는 항상 **"PROJECT.md에 없는 기능은 임의로 추가하지 말 것"**을 포함시킨다.
- 새로운 단계 시작 시 **이전 단계의 결과 요약**을 함께 제공한다.
- 참고서의 **"결정 필요" 항목**은 코드 작성 전에 반드시 짚고 넘어간다.
- 코드 블록은 워드 리포트에 넣을 수 있는 형태로 정리한다(필요 시 요청 프롬프트에 명시).
- 그림/figure 플레이스홀더는 **실제로 저장되는 그림만** 참조한다(기존 작업 방식과 동일).
