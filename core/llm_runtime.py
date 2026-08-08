import json
from typing import Optional
from llama_cpp import Llama
from infrastructure.config import get_settings
from infrastructure.logging import logger


class LLMRuntime:
    _instance: Optional["LLMRuntime"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return

        self.settings = get_settings()
        logger.info("Initializing LLM Runtime...")

        self.model = Llama(
            model_path=self.settings.MODEL_PATH,
            n_ctx=self.settings.N_CTX,
            n_threads=self.settings.N_THREADS,
            n_gpu_layers=self.settings.N_GPU_LAYERS,
            verbose=False,
        )

        logger.info("LLM model loaded successfully")
        self._initialized = True

    # ============================================================
    # STOP TOKENS / TURN MARKERS
    # ============================================================
    # These MUST match the actual special tokens phi-3-instruct was
    # trained on. Plain text like "User:" / "Assistant:" is never
    # emitted by the model, so it was never actually stopping
    # generation early -> it kept running to max_tokens and
    # hallucinating fake multi-turn transcripts.
    _STOP_TOKENS = ["<|end|>", "<|user|>", "<|system|>"]

    def generate(self, prompt: str, max_tokens=None, temperature=None) -> str:
        max_tokens = max_tokens or self.settings.MAX_TOKENS
        temperature = temperature or self.settings.TEMPERATURE

        logger.debug(f"Generating response | Prompt length: {len(prompt)}")

        try:
            # ✅ FIX: use phi-3's real chat template.
            # Without this, the model doesn't know where the user's
            # turn ends and its own turn begins, so it "free
            # associates" fake <|assistant|> / USER / OUTPUT blocks.
            formatted_prompt = (
                f"<|user|>\n{prompt.strip()}<|end|>\n<|assistant|>\n"
            )

            output = self.model(
                formatted_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=self._STOP_TOKENS,
            )

            # ✅ SAFE LOGGING (prevents Windows encoding crash)
            try:
                safe_output = str(output).encode("ascii", "ignore").decode()
                logger.debug(f"RAW OUTPUT: {safe_output[:500]}")
            except Exception:
                logger.debug("RAW OUTPUT: [unprintable]")

            # ✅ SAFE EXTRACTION
            if isinstance(output, dict):
                choices = output.get("choices", [{}])
                raw_text = choices[0].get("text", "") if choices else ""
                finish_reason = choices[0].get("finish_reason") if choices else None
                logger.debug(f"Finish reason: {finish_reason}")
            else:
                raw_text = str(output)

            # ✅ CLEAN RESPONSE
            # Only cut on genuine turn-boundary markers. We deliberately
            # do NOT cut on a bare "<|" anymore: that used to delete
            # real answer text whenever the model echoed a stray
            # "<|assistant|>" mid-generation (the exact bug that broke
            # the electricity-explanation test).
            for marker in self._STOP_TOKENS:
                if marker in raw_text:
                    raw_text = raw_text.split(marker)[0]

            response = raw_text.strip()

            # ✅ OPTIONAL CLEANUP (safe)
            if getattr(self.settings, "CLEAN_RESPONSE", False):
                response = self._clean_response(response)

            if not response:
                logger.warning("Empty response from model")
                return "Empty response"

            return response

        except Exception as e:
            logger.error(f"Inference failed: {e}")
            raise

    # ✅ CLEAN RESPONSE HELPER
    def _clean_response(self, text: str) -> str:
        text = text.strip()

        # remove incomplete sentence endings
        if "." in text:
            text = text[: text.rfind(".") + 1]

        return text