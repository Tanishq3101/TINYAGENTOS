"""
core/llm_runtime.py

PATCHED: added a threading.Lock() around the single non-thread-safe
resource -- the actual self.model(...) inference call.

Root cause of the crash you hit:
  GGML_ASSERT(buf != NULL && "tensor buffer not set") failed

LLMRuntime is a true singleton (__new__ returns the same instance every
call), so self.model is ONE llama_cpp.Llama object shared by every agent.
Orchestrator._run_full_pipeline() deliberately submits the summarizer and
extractor steps to a thread pool concurrently (that's a real, intentional
latency optimization -- see its docstring). Both of those steps call
LLMRuntime.generate(), and until this patch, generate() called
self.model(...) with no synchronization at all. Two threads hitting
llama.cpp's decode path on the same context at (almost) the same
millisecond is exactly how you get a native buffer-not-set assertion --
this aborts the whole process rather than raising a catchable Python
exception, which is why the traceback looked truncated.

Fix: a lock scoped to just the self.model(...) call. Everything else in
generate() (prompt formatting, logging, response cleanup) still runs
without contention -- only the actual native inference call is
serialized, since that's the one thing that isn't safe to run from two
threads at once.

This does NOT change Orchestrator's behavior or the fact that summarizer
and extractor are submitted concurrently -- they'll now just queue up on
this lock for the ~1 model call each takes, rather than crashing the
interpreter.
"""

import threading
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

        # PATCHED: one lock, created exactly once (this __init__ body only
        # runs on the first real construction -- the early return above
        # short-circuits every subsequent LLMRuntime() call on the same
        # singleton instance, so this never gets recreated/reset).
        self._inference_lock = threading.Lock()

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
            formatted_prompt = f"<|user|>\n{prompt.strip()}<|end|>\n<|assistant|>\n"

            # PATCHED: serialize the actual native inference call. This is
            # the one call in this method that touches the shared
            # llama_cpp.Llama context -- concurrent calls into it from
            # multiple threads (e.g. Orchestrator running summarizer and
            # extractor in parallel) previously crashed the whole process
            # with a native GGML_ASSERT rather than raising a Python
            # exception.
            with self._inference_lock:
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
