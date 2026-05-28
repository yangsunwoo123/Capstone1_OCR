let _tmplImg = null;
let _tmplOverlay = null;

const state = {
  selectedFormId: "direct-payment",
  selectedForm: null,
  formValues: {},
  localFiles: [],
  localPreviewUrls: [],
  uploadedFiles: [],
  sessionId: null,
  documents: [],
  currentImageIndex: 0,
  recognitionPayload: null,
  uploadPromise: null,
  uploadToken: 0,
  templateImages: [],
  // 저신뢰 필드 맵: fieldKey -> { text, candidates, confidence }
  lowConfidenceMap: {},
};

const fileInput           = document.getElementById("file-input");
const dropzone            = document.getElementById("dropzone");
const formEditor          = document.getElementById("form-editor");
const formPreviewCanvas   = document.getElementById("form-preview-canvas");
const selectedFormDescription = document.getElementById("selected-form-description");
const recognizeButton     = document.getElementById("recognize-button");
const imageStage          = document.getElementById("image-stage");
const imagePager          = document.getElementById("image-pager");
const progressFill        = document.getElementById("progress-fill");
const progressLabel       = document.getElementById("progress-label");
const imageCounter        = document.getElementById("image-counter");
const saveButton          = document.getElementById("save-button");
const saveStatus          = document.getElementById("save-status");
const birthdateModal      = document.getElementById("birthdate-modal");
const birthdateInput      = document.getElementById("birthdate-input");
const modalConfirmBtn     = document.getElementById("modal-confirm");
const modalCancelBtn      = document.getElementById("modal-cancel");
const saveResultCard      = document.getElementById("save-result-card");
const publicFileName      = document.getElementById("public-file-name");
const privateFileName     = document.getElementById("private-file-name");
const privateBadge        = document.getElementById("private-badge");
const uploadSummary       = document.getElementById("upload-summary");
const templateThumbs      = document.getElementById("template-thumbs");

function setProgress(value) {
  progressFill.style.width = `${value}%`;
  progressLabel.textContent = `${value}%`;
}

function setStatus(message, isError = false) {
  saveStatus.textContent = message;
  saveStatus.classList.toggle("error-text", isError);
}

function revokeLocalUrls() {
  state.localPreviewUrls.forEach((url) => URL.revokeObjectURL(url));
  state.localPreviewUrls = [];
}

function syncRecognizeButton() {
  const ready = state.localFiles.length > 0;
  recognizeButton.disabled = !ready;
  recognizeButton.classList.toggle("ready", ready);
}

function updateUploadSummary() {
  if (!state.localFiles.length) {
    uploadSummary.textContent = "업로드된 이미지 없음";
    return;
  }
  if (state.uploadedFiles.length === state.localFiles.length) {
    uploadSummary.textContent = `${state.uploadedFiles.length}개 이미지 업로드 완료`;
    return;
  }
  uploadSummary.textContent = `${state.localFiles.length}개 이미지 선택됨`;
}

function createFieldInput(field, value, className = "form-input") {
  const element = field.type === "textarea"
    ? document.createElement("textarea")
    : document.createElement("input");
  element.name        = field.name;
  element.className   = className;
  element.placeholder = field.placeholder || "";
  element.value       = value || "";
  if (field.type === "textarea") {
    element.rows = 3;
  } else {
    element.type = "text";
  }
  element.addEventListener("input", (event) => {
    state.formValues[field.name] = event.target.value;
    syncFieldInputs(field.name, event.target.value, event.target);
  });
  // 저신뢰 필드가 수동 편집되면 오버레이 박스도 초록으로 전환 (blur 시)
  element.addEventListener("change", (event) => {
    const fKey = event.target.name;
    if (state.lowConfidenceMap[fKey]) {
      const newValue = event.target.value.trim();
      if (newValue) {
        const overlay = document.getElementById("overlay");
        if (overlay) {
          const box = overlay.querySelector(`[data-field-key="${fKey}"]`);
          if (box && box.classList.contains("low")) {
            applyBoxCorrection(box, fKey, newValue, overlay);
          }
        }
      }
    }
  });
  return element;
}

function syncFieldInputs(fieldName, value, sourceElement) {
  document.querySelectorAll(`[name="${fieldName}"]`).forEach((element) => {
    if (element === sourceElement) return;
    element.value = value;
  });
}

function renderFormEditor() {
  const form = state.selectedForm;
  formEditor.innerHTML = "";
  if (!form) {
    formEditor.className = "stacked-fields";
    return;
  }
  formEditor.className = form.fields.length ? "stacked-fields" : "stacked-fields hidden";
  form.fields.forEach((field) => {
    const wrapper = document.createElement("label");
    const lcInfo  = state.lowConfidenceMap[field.name];
    wrapper.className = `stacked-field${lcInfo ? " low-confidence-field" : ""}`;
    wrapper.innerHTML = `<span>${field.label}${lcInfo ? ' <span class="low-badge">⚠ 저신뢰</span>' : ""}</span>`;
    wrapper.appendChild(createFieldInput(field, state.formValues[field.name]));
    if (lcInfo) {
      appendSuggestionRow(wrapper, field, lcInfo);
    }
    formEditor.appendChild(wrapper);
  });
}

/* ═══════════════════════════════════════════════════════════════════
   오버레이 박스 교정 적용 — 박스를 초록으로 전환하고 상태를 갱신
   ═══════════════════════════════════════════════════════════════════ */
function applyBoxCorrection(box, fieldKey, newValue, overlay) {
  // 상태 갱신
  state.formValues[fieldKey] = newValue;
  delete state.lowConfidenceMap[fieldKey];

  // 모든 동명 input 동기화
  document.querySelectorAll(`[name="${fieldKey}"]`).forEach((el) => {
    el.value = newValue;
  });

  // 박스 → 초록(확정) 전환
  box.classList.remove("low");
  const lbl = box.querySelector(".box-text-label");
  if (lbl) lbl.textContent = newValue;
  box.querySelector(".box-click-hint")?.remove();
  box.dataset.confirmed = "true";

  // 열린 팝업 닫기
  if (overlay) overlay.querySelectorAll(".box-popup").forEach((p) => p.remove());

  // 폼 에디터 저신뢰 스타일 제거
  document.querySelectorAll(".low-confidence-field").forEach((el) => {
    const inp = el.querySelector(`[name="${fieldKey}"]`);
    if (inp) {
      el.classList.remove("low-confidence-field");
      el.querySelector(".low-badge")?.remove();
      el.querySelector(".suggestion-row")?.remove();
    }
  });

  setStatus(`✅ "${newValue}" 로 수정 완료`);
}

/* ═══════════════════════════════════════════════════════════════════
   박스 팝업 열기 — 저신뢰 박스 클릭 시 인라인 수정 UI
   ═══════════════════════════════════════════════════════════════════ */
function openBoxPopup(box, fieldKey, region, overlay) {
  // 기존 팝업 제거
  overlay.querySelectorAll(".box-popup").forEach((p) => p.remove());

  const popup = document.createElement("div");
  popup.className = "box-popup";

  // 팝업 위치: 박스 아래 배치, 오버레이 밖으로 나가지 않도록 조정
  const boxLeft = parseFloat(box.style.left);
  const boxTop  = parseFloat(box.style.top);
  const boxH    = parseFloat(box.style.height);
  const ovH     = overlay.offsetHeight;
  const ovW     = overlay.offsetWidth;
  const POP_W   = 300;
  let   popTop  = boxTop + boxH + 8;
  if (popTop + 300 > ovH) popTop = Math.max(4, boxTop - 308);
  popup.style.left  = `${Math.max(4, Math.min(boxLeft, ovW - POP_W - 4))}px`;
  popup.style.top   = `${popTop}px`;
  popup.style.width = `${POP_W}px`;

  // AI가 판독한 텍스트 (GPT 크롭 인식 결과)
  const aiText  = region.text || "";
  // 이미 수정한 값이 있으면 그것을 기본값으로
  const initVal = state.formValues[fieldKey] !== undefined
    ? state.formValues[fieldKey]
    : aiText;

  // ── AI 추천 칩 생성 ──────────────────────────────────
  // 1) GPT 판독 결과 (항상 첫 번째로)
  const allSuggestions = [];
  if (aiText) allSuggestions.push(aiText);
  // 2) TrOCR 후보 (있으면 추가, 중복 제거)
  (region.candidates || []).slice(0, 3).forEach((c) => {
    if (c && !allSuggestions.includes(c)) allSuggestions.push(c);
  });

  const aiSectionHtml = allSuggestions.length
    ? allSuggestions.map((s, i) =>
        `<span class="ai-chip clickable-chip" data-idx="${i}">${s}</span>`
      ).join("")
    : `<span class="box-popup-ai-empty">AI가 읽기 어려운 영역입니다</span>
       <button class="box-popup-rerecognize" type="button">🔄 다시 인식</button>`;

  popup.innerHTML = `
    <div class="box-popup-header">
      <span class="box-popup-title">✏️ 손글씨 수정</span>
      <button class="box-popup-close" type="button">✕</button>
    </div>

    <div class="box-popup-ai-section">
      <span class="box-popup-ai-label">🤖 AI 판독 결과 (클릭하여 선택)</span>
      <div class="box-popup-chips">${aiSectionHtml}</div>
    </div>

    <hr class="box-popup-divider">

    <span class="box-popup-manual-label">✍️ 직접 입력</span>
    <input class="box-popup-input" type="text" placeholder="올바른 텍스트를 입력하세요">

    <div class="box-popup-actions">
      <button class="box-popup-cancel" type="button">취소</button>
      <button class="box-popup-confirm" type="button">확인 ✓</button>
    </div>
  `;

  const input      = popup.querySelector(".box-popup-input");
  const confirmBtn = popup.querySelector(".box-popup-confirm");
  const cancelBtn  = popup.querySelector(".box-popup-cancel");
  const closeBtn   = popup.querySelector(".box-popup-close");
  input.value = initVal;

  // 다시 인식 버튼 — AI 제안이 없을 때 해당 bbox 재인식
  const rerecognizeBtn = popup.querySelector(".box-popup-rerecognize");
  if (rerecognizeBtn) {
    rerecognizeBtn.addEventListener("click", async () => {
      rerecognizeBtn.disabled = true;
      rerecognizeBtn.textContent = "⏳ 인식 중...";
      try {
        const resp = await fetch("/api/recognize-region", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id:  state.sessionId,
            image_index: state.currentImageIndex,
            form_id:     state.selectedFormId,
            x: region.bbox.x1, y: region.bbox.y1,
            width: region.bbox.width, height: region.bbox.height,
          }),
        });
        const data = await resp.json();
        const chipsContainer = popup.querySelector(".box-popup-chips");
        if (resp.ok && data.text) {
          chipsContainer.innerHTML = "";
          const chip = document.createElement("span");
          chip.className = "ai-chip clickable-chip";
          chip.textContent = data.text;
          chip.addEventListener("click", () => {
            input.value = data.text;
            popup.querySelectorAll(".clickable-chip").forEach((c) => c.classList.remove("selected-chip"));
            chip.classList.add("selected-chip");
          });
          chipsContainer.appendChild(chip);
          input.value = data.text;
        } else {
          rerecognizeBtn.disabled = false;
          rerecognizeBtn.textContent = "🔄 다시 인식";
          chipsContainer.querySelector(".box-popup-ai-empty").textContent = "인식 결과 없음";
        }
      } catch {
        rerecognizeBtn.disabled = false;
        rerecognizeBtn.textContent = "🔄 다시 인식";
      }
    });
  }

  // 칩 클릭 → 입력창에 값 채우기
  popup.querySelectorAll(".clickable-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      input.value = chip.textContent;
      popup.querySelectorAll(".clickable-chip").forEach((c) => c.classList.remove("selected-chip"));
      chip.classList.add("selected-chip");
    });
  });

  const doConfirm = () => {
    const newVal = input.value.trim();
    if (!newVal) return;
    applyBoxCorrection(box, fieldKey, newVal, overlay);
    popup.remove();
  };

  confirmBtn.addEventListener("click", doConfirm);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter")  doConfirm();
    if (e.key === "Escape") popup.remove();
  });
  cancelBtn.addEventListener("click", () => popup.remove());
  closeBtn.addEventListener("click",  () => popup.remove());

  popup.addEventListener("click", (e) => e.stopPropagation());

  overlay.appendChild(popup);
  requestAnimationFrame(() => { input.focus(); input.select(); });
}

/* ═══════════════════════════════════════════════════════════════════
   오른쪽 패널 — 인식 결과 이미지 + 초록/빨간 박스 오버레이
   formPreviewCanvas 에 업로드 이미지를 표시하고
   고신뢰 = 초록 박스, 저신뢰 = 빨간 박스(클릭 가능)
   ═══════════════════════════════════════════════════════════════════ */
function renderDocumentWithBoxes(index) {
  const documentResult = state.documents[index];
  const uploaded       = state.uploadedFiles[index];
  if (!documentResult || !uploaded) {
    formPreviewCanvas.className = "form-preview-canvas empty";
    formPreviewCanvas.innerHTML = "<p>인식 결과가 여기에 표시됩니다.</p>";
    _tmplImg = null; _tmplOverlay = null;
    return;
  }

  formPreviewCanvas.className = "form-preview-canvas result-view";
  const shell = document.createElement("div");
  shell.className = "template-shell";

  const image = document.createElement("img");
  // 원본 빈 양식(blank template)을 배경으로 사용 — 없으면 업로드된 스캔 이미지 사용
  const templateSrc = state.selectedForm && state.selectedForm.template_image
    ? state.selectedForm.template_image
    : null;
  image.src = templateSrc || uploaded.url;
  image.alt = `인식 결과 ${index + 1}페이지`;

  const overlayEl = document.createElement("div");
  overlayEl.className = "template-overlay";
  overlayEl.id = "overlay"; // applyBoxCorrection 등에서 getElementById("overlay")로 탐색

  shell.appendChild(image);
  shell.appendChild(overlayEl);
  formPreviewCanvas.innerHTML = "";
  formPreviewCanvas.appendChild(shell);

  _tmplImg     = image;
  _tmplOverlay = overlayEl;

  const buildBoxes = () => {
    const scaleY = image.clientHeight / documentResult.height;

    documentResult.regions.forEach((region, idx) => {
      // 텍스트가 없는 빈 크롭 영역 스킵
      if (!region.text && region.source === "empty_crop") return;

      const fieldKey = region.field_name || `_ocr_${idx}`;

      // 수정 확정됐으면 초록, 아직 저신뢰면 빨간
      const wasLow   = region.low_confidence;
      const stillLow = wasLow && Object.prototype.hasOwnProperty.call(state.lowConfidenceMap, fieldKey);

      // % 기반 위치/크기 — 화면·인쇄 어떤 크기에서도 이미지와 항상 정렬
      const pctL = (region.bbox.x1 / documentResult.width  * 100).toFixed(4);
      const pctT = (region.bbox.y1 / documentResult.height * 100).toFixed(4);
      const pctW = ((region.bbox.x2 - region.bbox.x1) / documentResult.width  * 100).toFixed(4);
      const pctH = ((region.bbox.y2 - region.bbox.y1) / documentResult.height * 100).toFixed(4);

      const box = document.createElement("div");
      box.className        = `box${stillLow ? " low" : ""}`;
      box.dataset.fieldKey = fieldKey;
      box.style.left       = `${pctL}%`;
      box.style.top        = `${pctT}%`;
      box.style.width      = `${pctW}%`;
      box.style.height     = `${pctH}%`;

      // OCR 텍스트 라벨 (수정된 값 우선)
      const finalText = state.formValues[fieldKey] !== undefined
        ? state.formValues[fieldKey]
        : (region.text || "");

      // 화면 렌더링 폰트 크기: 박스의 실제 픽셀 높이 기준
      const renderedH = (region.bbox.y2 - region.bbox.y1) * scaleY;
      const fontSize = Math.max(7, Math.min(14, renderedH * 0.50));

      const textLabel = document.createElement("span");
      textLabel.className   = "box-text-label";
      textLabel.textContent = finalText;
      textLabel.style.fontSize = `${fontSize}px`;
      box.appendChild(textLabel);

      // 전체 텍스트를 툴팁으로 표시 (마우스 오버 시)
      box.title = finalText;

      if (stillLow) {
        const hint = document.createElement("span");
        hint.className   = "box-click-hint";
        hint.textContent = "클릭하여 수정";
        box.appendChild(hint);

        box.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!box.classList.contains("low")) return;
          openBoxPopup(box, fieldKey, region, overlayEl);
        });
      }

      overlayEl.appendChild(box);
    });

    // 오버레이 배경 클릭 시 열린 팝업 닫기
    overlayEl.addEventListener("click", () => {
      overlayEl.querySelectorAll(".box-popup").forEach((p) => p.remove());
    });
  };

  if (image.complete && image.naturalWidth) {
    requestAnimationFrame(buildBoxes);
  } else {
    image.addEventListener("load", buildBoxes);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   템플릿 오버레이 (우측 패널 — 필드 편집)
   ═══════════════════════════════════════════════════════════════════ */
function _buildOverlayContent(image, overlay) {
  overlay.innerHTML = "";
  const clientW = image.clientWidth;
  const clientH = image.clientHeight;

  const doc = state.documents[state.currentImageIndex];
  if (doc) {
    const activeRegions = doc.regions.filter((r) => r.source !== "empty_crop");
    activeRegions.forEach((region, idx) => {
      const dispX = (region.bbox.x1 / doc.width) * clientW;
      const dispY = (region.bbox.y1 / doc.height) * clientH;
      const dispW = (region.bbox.width / doc.width) * clientW;
      const dispH = (region.bbox.height / doc.height) * clientH;
      const wrapper = document.createElement("label");
      wrapper.className  = "overlay-field";
      wrapper.style.left = `${dispX}px`;
      wrapper.style.top  = `${dispY}px`;
      wrapper.style.width  = `${dispW}px`;
      wrapper.style.height = `${dispH}px`;
      const tag = document.createElement("span");
      tag.className   = "overlay-label";
      tag.textContent = region.field_name || `영역 ${idx + 1}`;
      wrapper.appendChild(tag);
      const fieldKey      = region.field_name || `_ocr_${idx}`;
      const currentValue  = state.formValues[fieldKey] !== undefined
        ? state.formValues[fieldKey]
        : region.text;
      wrapper.appendChild(createFieldInput(
        { name: fieldKey, type: dispH > 100 ? "textarea" : "input", placeholder: "인식 결과" },
        currentValue,
        "overlay-input",
      ));
      overlay.appendChild(wrapper);
    });
  }
}

function _addTemplateClickHandler(image, overlay) {
  overlay.addEventListener("click", async (event) => {
    if (event.target.closest(".overlay-field")) return;
    if (!state.sessionId) return;
    const clientW = image.clientWidth;
    const clientH = image.clientHeight;
    const natW    = image.naturalWidth  || 1;
    const natH    = image.naturalHeight || 1;
    const scaleX  = clientW / natW;
    const scaleY  = clientH / natH;
    const rect    = overlay.getBoundingClientRect();
    const clickX  = event.clientX - rect.left;
    const clickY  = event.clientY - rect.top;
    const natX    = Math.round(clickX / scaleX);
    const natY    = Math.round(clickY / scaleY);
    const defaultW = 200;
    const defaultH = 80;
    const regionX  = Math.max(0, natX - Math.round(defaultW / 2));
    const regionY  = Math.max(0, natY - Math.round(defaultH / 2));
    try {
      setStatus("클릭 영역 인식 중...");
      const resp = await fetch("/api/recognize-region", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id:  state.sessionId,
          image_index: state.currentImageIndex,
          form_id:     state.selectedFormId,
          x: regionX, y: regionY, width: defaultW, height: defaultH,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        setStatus(data.error || "영역 인식 실패", true);
        return;
      }
      setStatus(data.text ? `인식 완료: "${data.text}"` : "해당 영역에 손글씨가 없습니다.");
      const fieldKey2 = `_click_${Date.now()}`;
      state.formValues[fieldKey2] = data.text || "";
      const dispX2 = regionX * scaleX;
      const dispY2 = regionY * scaleY;
      const dispW2 = defaultW * scaleX;
      const dispH2 = defaultH * scaleY;
      const wrapper2 = document.createElement("label");
      wrapper2.className  = "overlay-field";
      wrapper2.style.left = `${dispX2}px`;
      wrapper2.style.top  = `${dispY2}px`;
      wrapper2.style.width  = `${dispW2}px`;
      wrapper2.style.height = `${dispH2}px`;
      const tag2 = document.createElement("span");
      tag2.className   = "overlay-label";
      tag2.textContent = "클릭 영역";
      wrapper2.appendChild(tag2);
      wrapper2.appendChild(createFieldInput(
        { name: fieldKey2, type: "input", placeholder: "인식 결과" },
        data.text || "",
        "overlay-input",
      ));
      overlay.appendChild(wrapper2);
    } catch (err) {
      setStatus("영역 인식 오류: " + err.message, true);
    }
  });
}

function refreshTemplateOverlay() {
  if (!_tmplImg || !_tmplOverlay) return;
  _buildOverlayContent(_tmplImg, _tmplOverlay);
}

function renderTemplatePreview() {
  const tmplUrl = state.templateImages[state.currentImageIndex];
  if (!tmplUrl || !state.documents.length) {
    formPreviewCanvas.className = "form-preview-canvas empty";
    formPreviewCanvas.innerHTML = "<p>인식 결과가 여기에 표시됩니다.</p>";
    _tmplImg = null; _tmplOverlay = null;
    return;
  }
  formPreviewCanvas.className = "form-preview-canvas";
  const shell   = document.createElement("div");
  shell.className = "template-shell";
  const image   = document.createElement("img");
  image.src     = tmplUrl;
  image.alt     = `양식 템플릿 ${state.currentImageIndex + 1}페이지`;
  const overlayT = document.createElement("div");
  overlayT.className = "template-overlay";
  shell.appendChild(image);
  shell.appendChild(overlayT);
  formPreviewCanvas.innerHTML = "";
  formPreviewCanvas.appendChild(shell);
  _tmplImg     = image;
  _tmplOverlay = overlayT;
  _addTemplateClickHandler(image, overlayT);
  const onLoad = () => _buildOverlayContent(image, overlayT);
  if (image.complete && image.naturalWidth) {
    requestAnimationFrame(onLoad);
  } else {
    image.addEventListener("load", onLoad);
  }
}

function renderFormPreview() {
  // 인식 결과가 있으면 오른쪽 패널에 업로드 이미지 + 박스 표시
  // 없으면 기존 양식 템플릿 미리보기
  if (state.documents.length) {
    renderDocumentWithBoxes(state.currentImageIndex);
  } else {
    renderTemplatePreview();
  }
  renderFormEditor();
}

function renderImagePager() {
  const total = state.documents.length || state.localFiles.length;
  imagePager.innerHTML = "";
  if (!total) return;
  for (let index = 0; index < total; index += 1) {
    const button = document.createElement("button");
    button.type      = "button";
    button.className = `pager-button ${state.currentImageIndex === index ? "active" : ""}`;
    button.textContent = String(index + 1);
    button.addEventListener("click", () => {
      state.currentImageIndex = index;
      renderCurrentImage();
      if (state.documents.length) renderDocumentWithBoxes(state.currentImageIndex);
    });
    imagePager.appendChild(button);
  }
}

function updateImageCounter(total) {
  imageCounter.textContent = total ? `${state.currentImageIndex + 1} / ${total}` : "0 / 0";
  renderImagePager();
}

function renderLocalPreview(index) {
  const previewUrl = state.localPreviewUrls[index];
  imageStage.className = "image-stage";
  imageStage.innerHTML = `<img src="${previewUrl}" alt="preview">`;
}

function renderCurrentImage() {
  const total = state.documents.length || state.localFiles.length;
  if (!total) {
    imageStage.className = "image-stage empty";
    imageStage.innerHTML = "<p>업로드한 이미지가 여기에 표시됩니다.</p>";
    updateImageCounter(0);
    return;
  }
  if (state.currentImageIndex >= total) state.currentImageIndex = 0;

  if (state.documents.length && state.uploadedFiles[state.currentImageIndex]) {
    // 인식 완료: 왼쪽은 원본 이미지만 (박스 없음)
    imageStage.className = "image-stage";
    imageStage.innerHTML = `<img src="${state.uploadedFiles[state.currentImageIndex].url}" alt="원본 이미지">`;
  } else {
    renderLocalPreview(state.currentImageIndex);
  }

  updateImageCounter(total);
}

function setLocalFiles(files) {
  revokeLocalUrls();
  state.localFiles        = Array.from(files);
  state.localPreviewUrls  = state.localFiles.map((file) => URL.createObjectURL(file));
  state.uploadedFiles     = [];
  state.sessionId         = null;
  state.documents         = [];
  state.recognitionPayload = null;
  state.currentImageIndex = 0;
  setProgress(0);
  updateUploadSummary();
  renderCurrentImage();
  syncRecognizeButton();
}

async function uploadFiles() {
  const formData = new FormData();
  state.localFiles.forEach((file) => formData.append("files", file));
  const response = await fetch("/api/upload", { method: "POST", body: formData });
  const payload  = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || "업로드에 실패했습니다.");
  return payload;
}

async function ensureUploaded({ force = false } = {}) {
  if (!state.localFiles.length) throw new Error("업로드할 이미지를 먼저 선택해야 합니다.");
  const alreadyUploaded = !!state.sessionId && state.uploadedFiles.length === state.localFiles.length;
  if (!force && alreadyUploaded) return { session_id: state.sessionId, files: state.uploadedFiles };
  if (!force && state.uploadPromise) return state.uploadPromise;

  const uploadToken    = ++state.uploadToken;
  setStatus("이미지 업로드 중...");
  setProgress(15);
  state.uploadPromise = uploadFiles()
    .then((payload) => {
      if (uploadToken !== state.uploadToken) return payload;
      state.sessionId     = payload.session_id;
      state.uploadedFiles = payload.files || [];
      updateUploadSummary();
      setProgress(30);
      setStatus(`${state.uploadedFiles.length}개 이미지 업로드 완료`);
      return payload;
    })
    .catch((error) => {
      if (uploadToken === state.uploadToken) {
        state.sessionId = null; state.uploadedFiles = [];
        state.documents = []; state.recognitionPayload = null;
        updateUploadSummary(); setProgress(0);
        setStatus(error.message, true);
      }
      throw error;
    })
    .finally(() => { if (uploadToken === state.uploadToken) state.uploadPromise = null; });
  return state.uploadPromise;
}

async function handleSelectedFiles(files) {
  setLocalFiles(files);
  if (!state.localFiles.length) { state.uploadToken += 1; state.uploadPromise = null; return; }
  try { await ensureUploaded({ force: true }); } catch (_error) { return; }
}

async function recognize() {
  try {
    setStatus("");
    state.documents = []; state.recognitionPayload = null;
    setProgress(10);
    await ensureUploaded();
    setProgress(45);
    const response = await fetch("/api/recognize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, form_id: state.selectedFormId }),
    });
    const payload = await response.json();
    if (!response.ok || payload.error || !payload.form) {
      throw new Error(payload.error || "인식 요청에 실패했습니다.");
    }
    state.documents          = payload.documents || [];
    state.recognitionPayload = payload;
    state.selectedFormId     = payload.form.id;
    state.selectedForm       = payload.form;
    document.getElementById("pdf-button").disabled = false;
    document.getElementById("print-button").disabled = false;
    state.formValues         = { ...(payload.prefill || {}) };
    state.templateImages     = payload.template_images || [];
    state.currentImageIndex  = 0;

    // 저신뢰 필드 맵 구축
    state.lowConfidenceMap = {};
    let regionIdx = 0;
    (payload.documents || []).forEach((doc) => {
      (doc.regions || []).forEach((region) => {
        if (region.low_confidence) {
          const key = region.field_name || `_ocr_${regionIdx}`;
          state.lowConfidenceMap[key] = {
            text:       region.text || "",
            candidates: region.candidates || [],
            confidence: region.confidence || 0,
          };
        }
        regionIdx++;
      });
    });

    renderFormPreview();
    renderCurrentImage();
    setProgress(100);
    const lowCount = Object.keys(state.lowConfidenceMap).length;
    setStatus(
      lowCount > 0
        ? `인식 완료 — 저신뢰 항목 ${lowCount}개 (빨간 박스를 클릭하여 수정)`
        : "인식 완료 — 모든 항목 정상 인식"
    );
  } catch (error) {
    state.documents = []; state.recognitionPayload = null;
    setProgress(0); setStatus(error.message, true);
    renderCurrentImage();
  }
}

/* ═══════════════════════════════════════════════════════════════════
   AI 교정 제안 기능
   ═══════════════════════════════════════════════════════════════════ */

/** 후보 칩 클릭 또는 외부에서 값을 적용할 때 */
function applyCandidate(fieldKey, value, stackEl) {
  state.formValues[fieldKey] = value;
  document.querySelectorAll(`[name="${fieldKey}"]`).forEach((el) => {
    el.value = value;
  });

  // 오버레이 박스도 초록으로 전환
  const overlay = document.getElementById("overlay");
  if (overlay) {
    const box = overlay.querySelector(`[data-field-key="${fieldKey}"]`);
    if (box && box.classList.contains("low")) {
      applyBoxCorrection(box, fieldKey, value, overlay);
      return; // applyBoxCorrection이 setStatus 호출
    }
  }

  if (stackEl) {
    stackEl.querySelectorAll(".clickable-chip, .ai-chip").forEach((c) => c.classList.remove("selected-chip"));
  }
  setStatus(`✅ "${value}" 로 수정되었습니다.`);
}

/** 고신뢰 텍스트들을 AI 문맥으로 수집 */
function collectContextTexts() {
  const texts = [];
  (state.documents || []).forEach((doc) => {
    (doc.regions || []).forEach((r) => {
      if (r.text && r.text.trim() && !r.low_confidence) {
        texts.push(r.text.trim());
      }
    });
  });
  return texts.slice(0, 8);
}

/** /api/suggest 호출 */
async function fetchSuggestions(text, fieldName, candidates, contextTexts) {
  try {
    const resp = await fetch("/api/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, field_name: fieldName, candidates, context: contextTexts }),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || "교정 요청 실패");
    return data;
  } catch (_err) {
    return { suggestions: candidates.slice(0, 3), source: "beam_search" };
  }
}

/** AI 교정 결과 칩들을 컨테이너에 렌더링 */
function renderSuggestionChips(container, fieldKey, suggestions, source) {
  container.querySelectorAll(".ai-chip, .ai-label").forEach((el) => el.remove());
  if (!suggestions || !suggestions.length) return;
  const label = document.createElement("span");
  label.className   = "ai-label";
  label.textContent = source === "gpt" ? "🤖 GPT 추천:" : "후보:";
  container.appendChild(label);
  suggestions.forEach((s) => {
    const chip = document.createElement("span");
    chip.className   = "ai-chip clickable-chip";
    chip.title       = "클릭하면 이 값으로 입력";
    chip.textContent = s;
    chip.addEventListener("click", () => applyCandidate(fieldKey, s, container));
    container.appendChild(chip);
  });
}

/** AI 교정 버튼 생성 (폼 에디터용) */
function makeAiSuggestBtn(text, fieldName, candidates, fieldKey, stackEl) {
  const btn = document.createElement("button");
  btn.type        = "button";
  btn.className   = "ai-suggest-btn";
  btn.textContent = "🤖 AI 교정";
  btn.addEventListener("click", async () => {
    btn.disabled    = true;
    btn.textContent = "⏳ 분석 중...";
    const ctx    = collectContextTexts();
    const result = await fetchSuggestions(text, fieldName, candidates, ctx);
    btn.remove();
    renderSuggestionChips(stackEl, fieldKey, result.suggestions, result.source);
  });
  return btn;
}

/** 폼 에디터에서 저신뢰 필드에 교정 UI 추가 */
function appendSuggestionRow(wrapper, field, lcInfo) {
  const row = document.createElement("div");
  row.className = "suggestion-row";
  (lcInfo.candidates || []).slice(0, 3).forEach((c) => {
    const chip = document.createElement("span");
    chip.className = "candidate clickable-chip";
    chip.title     = "클릭하면 이 값으로 입력";
    chip.textContent = c;
    chip.addEventListener("click", () => {
      applyCandidate(field.name, c, row);
      const inp = wrapper.querySelector("input, textarea");
      if (inp) inp.value = c;
    });
    row.appendChild(chip);
  });
  const btn = makeAiSuggestBtn(lcInfo.text, field.name, lcInfo.candidates || [], field.name, row);
  row.appendChild(btn);
  wrapper.appendChild(row);
}

/* ═══════════════════════════════════════════════════════════════════
   생년월일 모달 (저장 전 비밀번호 입력)
   ═══════════════════════════════════════════════════════════════════ */
function openBirthdateModal() {
  birthdateInput.value       = "";
  modalConfirmBtn.disabled   = true;
  birthdateModal.classList.remove("hidden");
  birthdateInput.focus();
}
function closeBirthdateModal() {
  birthdateModal.classList.add("hidden");
}

birthdateInput.addEventListener("input", () => {
  const digits = birthdateInput.value.replace(/\D/g, "");
  birthdateInput.value       = digits.slice(0, 6);
  modalConfirmBtn.disabled   = digits.length < 6;
});
modalCancelBtn.addEventListener("click", () => {
  closeBirthdateModal();
  setStatus("저장이 취소되었습니다.");
});
birthdateModal.addEventListener("click", (e) => {
  if (e.target === birthdateModal) closeBirthdateModal();
});

/* ── 실제 저장 ── */
async function doSave(birthdate) {
  const response = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id:  state.sessionId,
      form_id:     state.selectedFormId,
      values:      state.formValues,
      recognition: state.recognitionPayload.documents,
      birthdate,
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || "저장 실패");
  return payload;
}

/* ── 저장 결과 카드 표시 ── */
function showSaveResult(payload) {
  const publicName  = payload.public_path  ? payload.public_path.split(/[\\/]/).pop()  : "—";
  const privateName = payload.private_path ? payload.private_path.split(/[\\/]/).pop() : "—";
  publicFileName.textContent  = publicName;
  privateFileName.textContent = privateName;
  if (privateBadge) {
    privateBadge.textContent   = payload.encrypted ? "AES-256 암호화" : "저장됨(암호화 없음)";
    privateBadge.style.background = payload.encrypted ? "#ffe4cc" : "#eee";
  }
  saveResultCard.classList.add("visible");
}

/* ── PDF 저장 ── */
async function exportPdf() {
  if (!state.sessionId || !state.documents.length) {
    setStatus("먼저 인식을 완료해야 합니다.", true);
    return;
  }
  const pdfBtn = document.getElementById("pdf-button");
  pdfBtn.disabled = true;
  pdfBtn.textContent = "⏳ PDF 생성 중...";
  setStatus("PDF 생성 중...");

  try {
    // 각 페이지의 최종 텍스트 취합 (수정된 값 우선)
    const pages = state.documents.map((doc, pageIdx) => {
      const uploaded = state.uploadedFiles[pageIdx];
      return {
        image_url: uploaded ? uploaded.url : null,
        width: doc.width,
        height: doc.height,
        regions: doc.regions
          .filter((r) => r.source !== "empty_crop")
          .map((r, idx) => {
            const fk = r.field_name || `_ocr_${idx}`;
            const finalText = state.formValues[fk] !== undefined
              ? state.formValues[fk]
              : (r.text || "");
            return {
              bbox: r.bbox,
              text: finalText,
              low_confidence: r.low_confidence && Object.prototype.hasOwnProperty.call(state.lowConfidenceMap, fk),
            };
          }),
      };
    });

    const resp = await fetch("/api/export-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        pages,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || "PDF 생성 실패");
    }

    // 파일 다운로드
    const blob = await resp.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `인식결과_${new Date().toISOString().slice(0,10)}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus("✅ PDF 저장 완료");
  } catch (err) {
    setStatus("PDF 실패: " + err.message, true);
  } finally {
    pdfBtn.disabled = false;
    pdfBtn.textContent = "📄 PDF 저장";
  }
}

/* ── 저장 버튼 클릭 ── */
async function saveResult() {
  if (!state.sessionId || !state.recognitionPayload) {
    setStatus("먼저 인식을 완료해야 합니다.", true);
    return;
  }
  openBirthdateModal();
}

modalConfirmBtn.addEventListener("click", async () => {
  const birthdate = birthdateInput.value.replace(/\D/g, "");
  if (birthdate.length < 6) return;
  closeBirthdateModal();
  setStatus("저장 중...");
  saveResultCard.classList.remove("visible");
  try {
    const payload = await doSave(birthdate);
    setStatus("✅ 저장 완료 — 두 파일이 생성되었습니다.");
    showSaveResult(payload);
  } catch (err) {
    setStatus("저장 실패: " + err.message, true);
  }
});

/* ═══════════════════════════════════════════════════════════════════
   초기화 및 이벤트 바인딩
   ═══════════════════════════════════════════════════════════════════ */
function renderTemplateThumbs(images) {
  templateThumbs.innerHTML = "";
  images.forEach((url, idx) => {
    const thumb = document.createElement("div");
    thumb.className = "template-thumb";
    thumb.innerHTML = `<img src="${url}" alt="${idx + 1}페이지"><span>${idx + 1}페이지</span>`;
    templateThumbs.appendChild(thumb);
  });
}

async function loadFormImages() {
  try {
    const resp = await fetch("/api/form-images");
    const data = await resp.json();
    state.templateImages = data.images || [];
    renderTemplateThumbs(state.templateImages);
  } catch (_err) { /* form images may not be available */ }
}

async function loadForm() {
  try {
    const response = await fetch(`/api/forms/${encodeURIComponent(state.selectedFormId)}`);
    const payload  = await response.json();
    if (response.ok && payload.form) {
      state.selectedForm = payload.form;
      renderFormEditor();
    }
  } catch (_err) { /* form not yet seeded */ }
}

fileInput.addEventListener("change", (event) => { handleSelectedFiles(event.target.files); });
dropzone.addEventListener("dragover", (event) => { event.preventDefault(); });
dropzone.addEventListener("drop", (event) => { event.preventDefault(); handleSelectedFiles(event.dataTransfer.files); });

document.getElementById("prev-image").addEventListener("click", () => {
  const total = state.documents.length || state.localFiles.length;
  if (!total) return;
  state.currentImageIndex = (state.currentImageIndex - 1 + total) % total;
  renderCurrentImage();
  if (state.documents.length) renderDocumentWithBoxes(state.currentImageIndex);
});
document.getElementById("next-image").addEventListener("click", () => {
  const total = state.documents.length || state.localFiles.length;
  if (!total) return;
  state.currentImageIndex = (state.currentImageIndex + 1) % total;
  renderCurrentImage();
  if (state.documents.length) renderDocumentWithBoxes(state.currentImageIndex);
});

recognizeButton.addEventListener("click", recognize);
saveButton.addEventListener("click", saveResult);
document.getElementById("pdf-button").addEventListener("click", exportPdf);
document.getElementById("print-button").addEventListener("click", () => window.print());

loadFormImages();
loadForm().then(() => syncRecognizeButton());
