from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PIL import Image


@dataclass
class ZeroShotPrediction:
    text: str


class KoTrocrZeroShot:
    def __init__(self, model_name: str = "ddobokki/ko-trocr", device: str = "auto", max_new_tokens: int = 64):
        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("transformers/torch are required for zero-shot inference") from exc

        self._torch = torch
        self._processor_cls = TrOCRProcessor
        self._model_cls = VisionEncoderDecoderModel
        self.model_name = model_name
        self.max_new_tokens = max_new_tokens

        if device == "auto":
            if torch.cuda.is_available():
                self.device = "cuda"
            elif torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        else:
            self.device = device

        try:
            try:
                self.processor = TrOCRProcessor.from_pretrained(model_name, local_files_only=True)
                self.model = VisionEncoderDecoderModel.from_pretrained(model_name, local_files_only=True)
            except OSError:
                self.processor = TrOCRProcessor.from_pretrained(model_name)
                self.model = VisionEncoderDecoderModel.from_pretrained(model_name)
        except OSError as exc:  # pragma: no cover
            raise RuntimeError(
                f"failed to load '{model_name}'. The Hugging Face files are not available in this offline environment."
            ) from exc
        self.model.to(self.device)
        self.model.eval()

    def predict_batch(self, images: Sequence[Image.Image]) -> list[ZeroShotPrediction]:
        if not images:
            return []

        encoding = self.processor(images=list(images), return_tensors="pt")
        pixel_values = encoding.pixel_values.to(self.device)

        with self._torch.inference_mode():
            generated_ids = self.model.generate(
                pixel_values,
                max_length=self.max_new_tokens + 1,
            )

        decoded = self.processor.batch_decode(generated_ids, skip_special_tokens=True)
        return [ZeroShotPrediction(text=item.strip()) for item in decoded]
