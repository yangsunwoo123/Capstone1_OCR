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

const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const formEditor = document.getElementById("form-editor");
const formPreviewCanvas = document.getElementById("form-preview-canvas");
const selectedFormDescription = document.getElementById("selected-form-description");
const recognizeButton = document.getElementById("recognize-button");
const imageStage = document.getElementById("image-stage");
const imagePager = document.getElementById("image-pager");
const progressFill = document.getElementById("progress-fill");
const progressLabel = document.getElementById("progress-label");
const imageCounter = document.getElementById("image-counter");
const saveButton = document.getElementById("save-button");
const saveStatus = document.getElementById("save-status");
const birthdateModal  = document.getElementById("birthdate-modal");
const birthdateInput  = document.getElementById("birthdate-input");
const modalConfirmBtn = document.getElementById("modal-confirm");
const modalCancelBtn  = document.getElementById("modal-cancel");
const saveResultCard  = document.getElementById("save-result-card");
const publicFileName  = document.getElementById("public-file-name");
const privateFileName = document.getElementById("private-file-name");
const privateBadge    = document.getElementById("private-badge");
const uploadSummary = document.getElementById("upload-summary");
const templateThumbs = document.getElementById("template-thumbs");

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
  const element = field.type === "textarea" ? document.createElement("textarea") : document.createElement("input");
  element.name = field.name;
  element.className = className;
  element.placeholder = field.placeholder || "";
  element.value = value || "";
  if (field.type === "textarea") {
    element.rows = 3;
  } else {
    element.type = "text";
  }
  element.addEventListener("input", (event) => {
    state.formValues[field.name] = event.target.value;
    syncFieldInputs(field.name, event.target.value, event.target);
  });
  return element;
}

function syncFieldInputs(fieldName, value, sourceElement) {
  document.querySelectorAll(`[name="${fieldName}"]`).forEach((element) => {
    if (element === sourceElement) {
      return;
    }
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
    const lcInfo = state.lowConfidenceMap[field.name];
    wrapper.className = `stacked-field${lcInfo ? " low-confidence-field" : ""}`;
    wrapper.innerHTML = `<span>${field.label}${lcInfo ? ' <span class="low-badge">⚠ 저신뢰</span>' : ""}</span>`;
    wrapper.appendChild(createFieldInput(field, state.formValues[field.name]));
    // 저신뢰 필드에는 교정 후보 행 추가
    if (lcInfo) {
      appendSuggestionRow(wrapper, field, lcInfo);
    }
    formEditor.appendChild(wrapper);
  });
}

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
      wrapper.className = "overlay-field";
      wrapper.style.left = `${dispX}px`;
      wrapper.style.top = `${dispY}px`;
      wrapper.style.width = `${dispW}px`;
      wrapper.style.height = `${dispH}px`;
      const tag = document.createElement("span");
      tag.className = "overlay-label";
      tag.textContent = region.field_name || `영역 ${idx + 1}`;
      wrapper.appendChild(tag);
      const fieldKey = region.field_name || `_ocr_${idx}`;
      const currentValue = state.formValues[fieldKey] !== undefined ? state.formValues[fieldKey] : region.text;
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
    const natW = image.naturalWidth || 1;
    const natH = image.naturalHeight || 1;
    const scaleX = clientW / natW;
    const scaleY = clientH / natH;
    const rect = overlay.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const clickY = event.clientY - rect.top;
    const natX = Math.round(clickX / scaleX);
    const natY = Math.round(clickY / scaleY);
    const defaultW = 200;
    const defaultH = 80;
    const regionX = Math.max(0, natX - Math.round(defaultW / 2));
    const regionY = Math.max(0, natY - Math.round(defaultH / 2));
    try {
      setStatus("클릭 영역 인식 중...");
      const resp = await fetch("/api/recognize-region", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.sessionId,
          image_index: state.currentImageIndex,
          form_id: state.selectedFormId,
          x: regionX,
          y: regionY,
          width: defaultW,
          height: defaultH,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        setStatus(data.error || "영역 인식 실패", true);
        return;
      }
      setStatus(data.text ? `인식 완료: "${data.text}"` : "해당 영역에 손글씨가 없습니다.");
      const fieldKey = `_click_${Date.now()}`;
      state.formValues[fieldKey] = data.text || "";
      const dispX2 = regionX * scaleX;
      const dispY2 = regionY * scaleY;
      const dispW2 = defaultW * scaleX;
      const dispH2 = defaultH * scaleY;
      const wrapper = document.createElement("label");
      wrapper.className = "overlay-field";
      wrapper.style.left = `${dispX2}px`;
      wrapper.style.top = `${dispY2}px`;
      wrapper.style.width = `${dispW2}px`;
      wrapper.style.height = `${dispH2}px`;
      const tag = document.createElement("span");
      tag.className = "overlay-label";
      tag.textContent = "클릭 영역";
      wrapper.appendChild(tag);
      wrapper.appendChild(createFieldInput(
        { name: fieldKey, type: "input", placeholder: "인식 결과" },
        data.text || "",
        "overlay-input",
      ));
      overlay.appendChild(wrapper);
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
    _tmplImg = null;
    _tmplOverlay = null;
    return;
  }

  formPreviewCanvas.className = "form-preview-canvas";
  const shell = document.createElement("div");
  shell.className = "template-shell";
  const image = document.createElement("img");
  image.src = tmplUrl;
  image.alt = `양식 템플릿 ${state.currentImageIndex + 1}페이지`;
  const overlay = document.createElement("div");
  overlay.className = "template-overlay";
  shell.appendChild(image);
  shell.appendChild(overlay);
  formPreviewCanvas.innerHTML = "";
  formPreviewCanvas.appendChild(shell);
  _tmplImg = image;
  _tmplOverlay = overlay;

  _addTemplateClickHandler(image, overlay);

  const onLoad = () => _buildOverlayContent(image, overlay);
  if (image.complete && image.naturalWidth) {
    requestAnimationFrame(onLoad);
  } else {
    image.addEventListener("load", onLoad);
  }
}

function renderFormPreview() {
  renderTemplatePreview();
  renderFormEditor();
}

function renderImagePager() {
  const total = state.documents.length || state.localFiles.length;
  imagePager.innerHTML = "";
  if (!total) {
    return;
  }
  for (let index = 0; index < total; index += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `pager-button ${state.currentImageIndex === index ? "active" : ""}`;
    button.textContent = String(index + 1);
    button.addEventListener("click", () => {
      state.currentImageIndex = index;
      renderCurrentImage();
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

function renderDocument(index) {
  const documentResult = state.documents[index];
  const uploaded = state.uploadedFiles[index];
  imageStage.className = "image-stage";
  imageStage.innerHTML = `<img id="stage-image" src="${uploaded.url}" alt="uploaded image"><div class="overlay" id="overlay"></div>`;
  const img = imageStage.querySelector("#stage-image");
  img.addEventListener("load", () => {
    const overlay = imageStage.querySelector("#overlay");
    const scaleX = img.clientWidth / documentResult.width;
    const scaleY = img.clientHeight / documentResult.height;
    documentResult.regions.forEach((region) => {
      if (!region.text && region.source === "empty_crop") {
        return;
      }
      const box = document.createElement("div");
      box.className = `box ${region.low_confidence ? "low" : ""}`;
      box.style.left = `${region.bbox.x1 * scaleX}px`;
      box.style.top = `${region.bbox.y1 * scaleY}px`;
      box.style.width = `${region.bbox.width * scaleX}px`;
      box.style.height = `${region.bbox.height * scaleY}px`;
      overlay.appendChild(box);
      if (region.low_confidence) {
        const fieldKey = region.field_name || `_ocr_${idx}`;
        const stack = document.createElement("div");
        stack.className = "candidate-stack";
        stack.style.left = `${region.bbox.x1 * scaleX}px`;
        stack.style.top = `${(region.bbox.y2 + 6) * scaleY}px`;

        // 기존 TrOCR 후보 칩 (클릭 시 해당 필드 값 교체)
        (region.candidates || []).forEach((candidate) => {
          const chip = document.createElement("span");
          chip.className = "candidate clickable-chip";
          chip.title = "클릭하면 이 값으로 입력";
          chip.textContent = candidate;
          chip.addEventListener("click", () => applyCandidate(fieldKey, candidate, stack));
          stack.appendChild(chip);
        });

        // AI 교정 버튼
        const aiBtn = makeAiSuggestBtn(region.text || "", region.field_name || "", region.candidates || [], fieldKey, stack);
        stack.appendChild(aiBtn);
        overlay.appendChild(stack);
      }
    });
  });
}

function renderCurrentImage() {
  const total = state.documents.length || state.localFiles.length;
  if (!total) {
    imageStage.className = "image-stage empty";
    imageStage.innerHTML = "<p>업로드한 이미지가 여기에 표시됩니다.</p>";
    updateImageCounter(0);
    return;
  }
  if (state.currentImageIndex >= total) {
    state.currentImageIndex = 0;
  }
  if (state.documents.length) {
    renderDocument(state.currentImageIndex);
    renderTemplatePreview();
    requestAnimationFrame(refreshTemplateOverlay);
  } else {
    renderLocalPreview(state.currentImageIndex);
  }
  updateImageCounter(total);
}

function setLocalFiles(files) {
  revokeLocalUrls();
  state.localFiles = Array.from(files);
  state.localPreviewUrls = state.localFiles.map((file) => URL.createObjectURL(file));
  state.uploadedFiles = [];
  state.sessionId = null;
  state.documents = [];
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
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || "업로드에 실패했습니다.");
  }
  return payload;
}

async function ensureUploaded({ force = false } = {}) {
  if (!state.localFiles.length) {
    throw new Error("업로드할 이미지를 먼저 선택해야 합니다.");
  }
  const alreadyUploaded = !!state.sessionId && state.uploadedFiles.length === state.localFiles.length;
  if (!force && alreadyUploaded) {
    return { session_id: state.sessionId, files: state.uploadedFiles };
  }
  if (!force && state.uploadPromise) {
    return state.uploadPromise;
  }
  const uploadToken = ++state.uploadToken;
  setStatus("이미지 업로드 중...");
  setProgress(15);
  state.uploadPromise = uploadFiles()
    .then((payload) => {
      if (uploadToken !== state.uploadToken) {
        return payload;
      }
      state.sessionId = payload.session_id;
      state.uploadedFiles = payload.files || [];
      updateUploadSummary();
      setProgress(30);
      setStatus(`${state.uploadedFiles.length}개 이미지 업로드 완료`);
      return payload;
    })
    .catch((error) => {
      if (uploadToken === state.uploadToken) {
        state.sessionId = null;
        state.uploadedFiles = [];
        state.documents = [];
        state.recognitionPayload = null;
        updateUploadSummary();
        setProgress(0);
        setStatus(error.message, true);
      }
      throw error;
    })
    .finally(() => {
      if (uploadToken === state.uploadToken) {
        state.uploadPromise = null;
      }
    });
  return state.uploadPromise;
}

async function handleSelectedFiles(files) {
  setLocalFiles(files);
  if (!state.localFiles.length) {
    state.uploadToken += 1;
    state.uploadPromise = null;
    return;
  }
  try {
    await ensureUploaded({ force: true });
  } catch (_error) {
    return;
  }
}

async function recognize() {
  try {
    setStatus("");
    state.documents = [];
    state.recognitionPayload = null;
    setProgress(10);
    await ensureUploaded();
    setProgress(45);
    const response = await fetch("/api/recognize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        form_id: state.selectedFormId,
      }),
    });
    const payload = await response.json();
    if (!response.ok || payload.error || !payload.form) {
      throw new Error(payload.error || "인식 요청에 실패했습니다.");
    }
    state.documents = payload.documents || [];
    state.recognitionPayload = payload;
    state.selectedFormId = payload.form.id;
    state.selectedForm = payload.form;
    state.formValues = { ...(payload.prefill || {}) };
    state.templateImages = payload.template_images || [];
    state.currentImageIndex = 0;

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
    setStatus(lowCount > 0 ? `인식 완료 — 저신뢰 항목 ${lowCount}개 (빨간 박스 확인)` : "인식 완료");
  } catch (error) {
    state.documents = [];
    state.recognitionPayload = null;
    setProgress(0);
    setStatus(error.message, true);
    renderCurrentImage();
  }
}

/* ═══════════════════════════════════════════════
   AI 교정 제안 기능
   ═══════════════════════════════════════════════ */

/** 후보 클릭 시 해당 필드 값 적용 */
function applyCandidate(fieldKey, value, stackEl) {
  state.formValues[fieldKey] = value;
  document.querySelectorAll(`[name="${fieldKey}"]`).forEach((el) => {
    el.value = value;
  });
  // 클릭된 칩을 선택 표시
  if (stackEl) {
    stackEl.querySelectorAll(".clickable-chip, .ai-chip").forEach((c) => c.classList.remove("selected-chip"));
  }
  setStatus(`✏️ "${value}" 로 수정되었습니다.`);
}

/** 현재 인식된 텍스트들을 문맥으로 수집 */
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
      body: JSON.stringify({
        text,
        field_name: fieldName,
        candidates,
        context: contextTexts,
      }),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || "교정 요청 실패");
    return data;
  } catch (err) {
    return { suggestions: candidates.slice(0, 3), source: "beam_search" };
  }
}

/** AI 교정 결과 칩들을 컨테이너에 렌더링 */
function renderSuggestionChips(container, fieldKey, suggestions, source) {
  // 기존 AI 칩 제거
  container.querySelectorAll(".ai-chip, .ai-label").forEach((el) => el.remove());

  if (!suggestions || !suggestions.length) return;

  const label = document.createElement("span");
  label.className = "ai-label";
  label.textContent = source === "claude" ? "🤖 AI 추천:" : "후보:";
  container.appendChild(label);

  suggestions.forEach((s) => {
    const chip = document.createElement("span");
    chip.className = "ai-chip clickable-chip";
    chip.title = "클릭하면 이 값으로 입력";
    chip.textContent = s;
    chip.addEventListener("click", () => applyCandidate(fieldKey, s, container));
    container.appendChild(chip);
  });
}

/** AI 교정 버튼 생성 */
function makeAiSuggestBtn(text, fieldName, candidates, fieldKey, stackEl) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ai-suggest-btn";
  btn.textContent = "🤖 AI 교정";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "⏳ 분석 중...";
    const ctx = collectContextTexts();
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

  // 기존 TrOCR 후보 칩
  (lcInfo.candidates || []).slice(0, 3).forEach((c) => {
    const chip = document.createElement("span");
    chip.className = "candidate clickable-chip";
    chip.title = "클릭하면 이 값으로 입력";
    chip.textContent = c;
    chip.addEventListener("click", () => {
      applyCandidate(field.name, c, row);
      // 폼 에디터 입력도 업데이트
      wrapper.querySelector("input, textarea").value = c;
    });
    row.appendChild(chip);
  });

  // AI 교정 버튼
  const btn = makeAiSuggestBtn(lcInfo.text, field.name, lcInfo.candidates || [], field.name, row);
  row.appendChild(btn);

  wrapper.appendChild(row);
}

/* ── 모달 열기/닫기 ── */
function openBirthdateModal() {
  birthdateInput.value = "";
  modalConfirmBtn.disabled = true;
  birthdateModal.classList.remove("hidden");
  birthdateInput.focus();
}

function closeBirthdateModal() {
  birthdateModal.classList.add("hidden");
}

/* 6자리 숫자 입력될 때만 확인 버튼 활성화 */
birthdateInput.addEventListener("input", () => {
  const digits = birthdateInput.value.replace(/\D/g, "");
  birthdateInput.value = digits.slice(0, 6);
  modalConfirmBtn.disabled = digits.length < 6;
});

modalCancelBtn.addEventListener("click", () => {
  closeBirthdateModal();
  setStatus("저장이 취소되었습니다.");
});

/* 모달 바깥 클릭 시 닫기 */
birthdateModal.addEventListener("click", (e) => {
  if (e.target === birthdateModal) closeBirthdateModal();
});

/* ── 실제 저장 호출 ── */
async function doSave(birthdate) {
  const response = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id:  state.sessionId,
      form_id:     state.selectedFormId,
      values:      state.formValues,
      recognition: state.recognitionPayload.documents,
      birthdate:   birthdate,
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || "저장 실패");
  }
  return payload;
}

/* ── 저장 결과 카드 표시 ── */
function showSaveResult(payload) {
  const publicName  = payload.public_path  ? payload.public_path.split(/[\\/]/).pop()  : "—";
  const privateName = payload.private_path ? payload.private_path.split(/[\\/]/).pop() : "—";
  publicFileName.textContent  = publicName;
  privateFileName.textContent = privateName;
  if (privateBadge) {
    privateBadge.textContent = payload.encrypted ? "AES-256 암호화" : "저장됨(암호화 없음)";
    privateBadge.style.background = payload.encrypted ? "#ffe4cc" : "#eee";
  }
  saveResultCard.classList.add("visible");
}

/* ── 저장 버튼 클릭 → 모달 표시 ── */
async function saveResult() {
  if (!state.sessionId || !state.recognitionPayload) {
    setStatus("먼저 인식을 완료해야 합니다.", true);
    return;
  }
  openBirthdateModal();
}

/* ── 모달 확인 → 저장 실행 ── */
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
  } catch (_err) {
    // form images may not be available
  }
}

async function loadForm() {
  try {
    const response = await fetch(`/api/forms/${encodeURIComponent(state.selectedFormId)}`);
    const payload = await response.json();
    if (response.ok && payload.form) {
      state.selectedForm = payload.form;
      renderFormEditor();
    }
  } catch (_err) {
    // form not yet seeded
  }
}

fileInput.addEventListener("change", (event) => {
  handleSelectedFiles(event.target.files);
});

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
});

dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  handleSelectedFiles(event.dataTransfer.files);
});

document.getElementById("prev-image").addEventListener("click", () => {
  const total = state.documents.length || state.localFiles.length;
  if (!total) {
    return;
  }
  state.currentImageIndex = (state.currentImageIndex - 1 + total) % total;
  renderCurrentImage();
});

document.getElementById("next-image").addEventListener("click", () => {
  const total = state.documents.length || state.localFiles.length;
  if (!total) {
    return;
  }
  state.currentImageIndex = (state.currentImageIndex + 1) % total;
  renderCurrentImage();
});

recognizeButton.addEventListener("click", recognize);
saveButton.addEventListener("click", saveResult);

loadFormImages();
loadForm().then(() => syncRecognizeButton());
