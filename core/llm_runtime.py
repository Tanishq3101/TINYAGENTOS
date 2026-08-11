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

import os
import threading
from typing import Optional
from llama_cpp import Llama
from infrastructure.config import get_settings
from infrastructure.logging import logger


class ModelNotLoadedError(RuntimeError):
    """Raised by generate() when LLMRuntime was constructed without a
    loaded model (missing MODEL_PATH, or TINYAGENT_SKIP_LLM_LOAD=1 --
    see LLMRuntime.__init__). Callers (e.g. api routes) should catch
    this and return a clean 503, rather than letting it propagate as
    an unhandled 500."""


class LLMRuntime:
    _instance: Optional["LLMRuntime"] = None
    # MYPY FIX (was: "Cannot determine type of _initialized" [has-type]):
    # previously this attribute was only ever set at the very end of
    # __init__, so mypy had no annotation to check the hasattr()/truthy
    # check above against. A class-level default makes the type explicit
    # and turns the singleton short-circuit check into a plain attribute
    # read instead of hasattr().
    _initialized: bool = False

    # MYPY FIX (was: "Call to untyped function LLMRuntime in typed context"
    # [no-untyped-call] at orchestrator.py:795 and agent.py:18): __new__/
    # __init__ had no signature types, so mypy couldn't verify any call
    # into this class from elsewhere. Annotating both closes that gap at
    # its source instead of silencing it at every call site.
    def __new__(cls) -> "LLMRuntime":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self.settings = get_settings()
        logger.info("Initializing LLM Runtime...")

        # PATCHED: one lock, created exactly once (this __init__ body only
        # runs on the first real construction -- the early return above
        # short-circuits every subsequent LLMRuntime() call on the same
        # singleton instance, so this never gets recreated/reset).
        self._inference_lock = threading.Lock()

        # CI/SMOKE-TEST FIX: previously this unconditionally called
        # Llama(model_path=...), which raises ValueError and kills the
        # whole app (see api/app.py's lifespan -- it imports
        # core.orchestrator.orchestrator eagerly at boot) whenever the
        # multi-GB .gguf isn't present. That's the correct behavior for
        # a real deployment, but CI has no reason to download real model
        # weights just to prove the API boots and routes respond.
        #
        # TINYAGENT_SKIP_LLM_LOAD=1 is the explicit "smoke-test mode"
        # opt-in (set it in ci.yml's `docker compose up` step, not in
        # docker-compose.yml's defaults, so real deployments never set
        # it by accident). We ALSO fall back to skip-with-warning if the
        # configured path just doesn't exist on disk -- that keeps a
        # genuinely misconfigured MODEL_PATH from crash-looping the
        # entire process, and instead surfaces as `model_loaded: false`
        # on /health where it's actually visible and debuggable.
        skip_requested = os.environ.get("TINYAGENT_SKIP_LLM_LOAD") == "1"
        model_path = self.settings.MODEL_PATH
        model_missing = not (model_path and os.path.exists(model_path))

        if skip_requested or model_missing:
            self.model = None
            if skip_requested:
                logger.warning(
                    "TINYAGENT_SKIP_LLM_LOAD=1 set -- skipping LLM load "
                    "(smoke-test mode; inference endpoints will 503)."
                )
            else:
                logger.warning(
                    f"MODEL_PATH not found at '{model_path}' -- skipping "
                    "LLM load. Inference endpoints will return 503 until "
                    "a valid model file is present."
                )
            self._initialized = True
            return

        self.model = Llama(
            model_path=model_path,
            n_ctx=self.settings.N_CTX,
            n_threads=self.settings.N_THREADS,
            n_gpu_layers=self.settings.N_GPU_LAYERS,
            verbose=False,
        )

        logger.info("LLM model loaded successfully")
        self._initialized = True

    @property
    def is_loaded(self) -> bool:
        """Used by /health (and anything else that wants to report real
        model status) to distinguish 'app is up' from 'app can actually
        run inference' -- see ModelNotLoadedError docstring above."""
        return self.model is not None

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

        # See ModelNotLoadedError docstring: __init__ may have skipped
        # loading (missing MODEL_PATH, or TINYAGENT_SKIP_LLM_LOAD=1).
        # Fail with a clear, catchable error here rather than letting
        # `self.model(...)` below raise a confusing TypeError on None.
        if self.model is None:
            raise ModelNotLoadedError(
                "LLM model is not loaded (missing MODEL_PATH or "
                "TINYAGENT_SKIP_LLM_LOAD=1) -- inference is unavailable."
            )

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
                # MYPY FIX (was: "Invalid 'type: ignore' comment [syntax]"):
                # this explanatory block used to *start* with the text
                # "type: ignore[...]", which mypy parses as an ignore-directive
                # attempt on ANY comment line starting with "# type:", not just
                # ones attached to code -- reworded so only the real directive
                # below (attached to the `output.get(...)` line) is parsed as
                # an ignore comment.
                #
                # Ignore justification -- llama_cpp's CompletionChoice
                # TypedDict requires text/index/logprobs/finish_reason, but this
                # [{}] is only ever a defensive empty-fallback that's read right
                # below via .get(key, default) on every key we touch, so a
                # "short" dict here can never KeyError. Not a real bug -- see
                # Tier 3 triage notes above -- silenced rather than restructured
                # so we don't change working, safe behavior for a typing-only gap.
                choices = output.get("choices", [{}])  # type: ignore[typeddict-item]
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
