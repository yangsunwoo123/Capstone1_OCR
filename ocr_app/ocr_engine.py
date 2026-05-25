from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image

from .config import FINE_TUNED_CHECKPOINT_DIR, ModelConfig

if TYPE_CHECKING:
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel


class MissingModelDependencyError(RuntimeError):
    """Raised when transformers or torch are unavailable."""


@dataclass(slots=True)
class OCRPrediction:
    text: str
    confidence: float
    candidates: list[str]
    token_confidences: list[float]
    source: str


class KoTrOCREngine:
    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()
        self._processor: TrOCRProcessor | None = None
        self._model: VisionEncoderDecoderModel | None = None
        self._torch: torch | None = None
        self._device: str | None = None

    @staticmethod
    def dependencies_available() -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ModuleNotFoundError:
            return False
        return True

    def load(self) -> None:
        if self._model is not None and self._processor is not None:
            return
        if not self.dependencies_available():
            raise MissingModelDependencyError(
                "torch/transformers가 설치되지 않아 ddobokki/ko-trocr를 로드할 수 없습니다."
            )
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        self._torch = torch
        try:
            model_name = str(FINE_TUNED_CHECKPOINT_DIR) if FINE_TUNED_CHECKPOINT_DIR.exists() else self.config.model_name
            self._processor = TrOCRProcessor.from_pretrained(model_name, local_files_only=True)
            self._model = VisionEncoderDecoderModel.from_pretrained(model_name, local_files_only=True)
        except OSError as error:
            raise MissingModelDependencyError(
                "ddobokki/ko-trocr 모델 파일을 현재 환경에서 사용할 수 없습니다. "
                "네트워크 접근이 없으면 annotation fallback으로 동작합니다."
            ) from error
        if torch.cuda.is_available():
            self._device = "cuda"
        elif torch.backends.mps.is_available():
            self._device = "mps"
        else:
            self._device = "cpu"
        self._model.to(self._device)
        self._model.eval()

    def predict_crop(self, image: Image.Image) -> OCRPrediction:
        self.load()
        assert self._processor is not None
        assert self._model is not None
        assert self._torch is not None

        pixel_values = self._processor(images=image, return_tensors="pt").pixel_values.to(self._device)
        beam_count = max(self.config.candidate_count, 3)
        with self._torch.no_grad():
            output = self._model.generate(
                pixel_values,
                num_beams=beam_count,
                num_return_sequences=self.config.candidate_count,
                return_dict_in_generate=True,
                output_scores=True,
            )
        decoded = self._processor.batch_decode(output.sequences, skip_special_tokens=True)
        unique_candidates: list[str] = []
        for candidate in decoded:
            candidate = candidate.strip()
            if candidate and candidate not in unique_candidates:
                unique_candidates.append(candidate)
        main_text = unique_candidates[0] if unique_candidates else ""
        token_confidences: list[float] = []
        try:
            transition_scores = self._model.compute_transition_scores(
                output.sequences[:1],
                output.scores,
                normalize_logits=True,
            )
            token_confidences = transition_scores[0].exp().tolist()
        except Exception:
            token_confidences = []
        confidence = (
            sum(token_confidences) / len(token_confidences)
            if token_confidences
            else (1.0 if main_text else 0.0)
        )
        return OCRPrediction(
            text=main_text,
            confidence=float(confidence),
            candidates=unique_candidates[: self.config.candidate_count],
            token_confidences=[float(value) for value in token_confidences],
            source="model",
        )


def annotation_prediction(text: str) -> OCRPrediction:
    clean_text = text.strip()
    return OCRPrediction(
        text=clean_text,
        confidence=1.0 if clean_text else 0.0,
        candidates=[clean_text] if clean_text else [],
        token_confidences=[1.0] * len(clean_text),
        source="annotation_fallback",
    )


def unavailable_prediction() -> OCRPrediction:
    return OCRPrediction(
        text="",
        confidence=0.0,
        candidates=[],
        token_confidences=[],
        source="model_unavailable",
    )
