import json
import re
from typing import Optional

from core.llm_runtime import LLMRuntime
from core.memory import ConversationMemory
from infrastructure.logging import logger
from core.tools.registry import TOOLS


class BaseAgent:
    def __init__(
        self,
        session_id: str = "default",
        memory: Optional[ConversationMemory] = None,
        use_memory: bool = True,
    ):
        self.llm = LLMRuntime()

        # A memory can be injected directly (useful for tests, or for
        # sharing one memory instance across multiple agents), or one
        # is created automatically per session_id. use_memory=False
        # gives fully stateless behavior identical to before this
        # feature existed.
        if memory is not None:
            self.memory = memory
        elif use_memory:
            self.memory = ConversationMemory(session_id=session_id)
        else:
            self.memory = None

    # ========================================
    # 🧠 THINK
    # ========================================
    def think(self, prompt: str) -> str:
        """Generate LLM response without tools, using recent
        conversation history as context when memory is enabled."""
        logger.debug("Agent thinking...")

        context = self.memory.get_context() if self.memory else ""

        if context:
            thought_prompt = (
                "Continue this conversation. Use the prior turns for "
                "context, then answer the latest message clearly and "
                "completely.\n\n"
                f"Conversation so far:\n{context}\n\n"
                f"User: {prompt}\n"
                "Assistant:"
            )
        else:
            thought_prompt = f"Answer clearly and completely:\n\n{prompt}"

        response = self.llm.generate(thought_prompt)

        if self.memory:
            self.memory.add("user", prompt)
            self.memory.add("assistant", response)

        return response

    # ========================================
    # 🧰 FORMAT TOOLS
    # ========================================
    def _format_tools(self) -> str:
        """Format available tools for decision prompt"""
        return "\n".join(f"- {name}: {meta['description']}" for name, meta in TOOLS.items())

    # ========================================
    # 🧠 EXTRACT JSON (ROBUST)
    # ========================================
    def _extract_json(self, text: str) -> dict:
        if not text:
            return None

        logger.debug(f"Extracting JSON from: {text[:100]}")

        # FIX 1: Add missing brace
        if "{" in text and "}" not in text:
            text = text + "}"
            logger.debug("Added missing closing brace")

        # FIX 2: Extract substring
        first = text.find("{")
        last = text.rfind("}")

        if first != -1 and last != -1 and first < last:
            json_str = text[first : last + 1]
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    logger.info(f"Parsed JSON: {parsed}")
                    return parsed
            except Exception:
                pass

        # FIX 3: Regex fallback
        patterns = [
            r"\{[^{}]*\"action\"[^{}]*\}",
            r"\{[^{}]*\"tool_name\"[^{}]*\}",
            r"\{[^{}]*\}",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for m in matches:
                try:
                    return json.loads(m)
                except Exception:
                    continue

        # FIX 4: Salvage
        action = re.search(r'"action"\s*:\s*"(\w+)"', text)
        tool = re.search(r'"tool_name"\s*:\s*"([\w-]+)"', text)
        inp = re.search(r'"tool_input"\s*:\s*"([^"]*)"', text)

        if action:
            salvaged = {
                "action": action.group(1),
                "tool_name": tool.group(1) if tool else None,
                "tool_input": inp.group(1) if inp else "",
            }
            logger.info(f"Salvaged JSON: {salvaged}")
            return salvaged

        logger.error("JSON extraction failed")
        return None

    # ========================================
    # 🔧 NORMALIZE DECISION
    # ========================================
    def _normalize_decision(self, decision: dict) -> dict:
        if not decision:
            return {"action": "llm", "tool_name": None, "tool_input": ""}

        normalized = decision.copy()
        action = str(normalized.get("action", "")).lower().strip()
        tool_name = normalized.get("tool_name", "")

        tool_name = tool_name.lower().strip() if tool_name else ""

        # FIX 1: action is actually a tool name
        if action in TOOLS and action not in ["tool", "llm"]:
            normalized["tool_name"] = action
            normalized["action"] = "tool"

        # FIX 2: missing action but tool_name present
        elif tool_name and "action" not in decision:
            normalized["action"] = "tool"

        # FIX 3: invalid/unknown action -> default to llm
        if normalized.get("action") not in ["tool", "llm"]:
            normalized["action"] = "llm"

        # FIX 4: validate tool exists
        if normalized["action"] == "tool":
            if not tool_name or tool_name not in TOOLS:
                logger.warning(f"Invalid tool: {tool_name}")
                normalized["action"] = "llm"
                normalized["tool_name"] = None
            else:
                normalized["tool_name"] = tool_name

        # FIX 5: ensure string input
        if not isinstance(normalized.get("tool_input"), str):
            normalized["tool_input"] = str(normalized.get("tool_input", ""))

        return normalized

    # ========================================
    # 🧹 CLEAN TOOL INPUT
    # ========================================
    def _clean_tool_input(self, tool_name: str, tool_input: str) -> str:
        if not isinstance(tool_input, str):
            tool_input = str(tool_input)
        tool_input = tool_input.strip()

        if tool_name == "weather":
            stopwords = {
                "what",
                "is",
                "the",
                "weather",
                "in",
                "today",
                "now",
                "tell",
                "me",
                "can",
                "you",
                "please",
                "like",
            }
            words = tool_input.lower().split()
            filtered = [w for w in words if w not in stopwords]
            cleaned = " ".join(filtered).strip()
            return cleaned or tool_input

        elif tool_name == "calculator":
            expr = tool_input.lower()

            # Handle common natural-language phrasings before stripping
            # characters, otherwise "15% of 200" / "square root of 144"
            # collapse into meaningless leftovers like "15 200".
            pct_match = re.search(r"([\d.]+)\s*%\s*of\s*([\d.]+)", expr)
            if pct_match:
                x, y = pct_match.groups()
                expr = f"({x}/100)*{y}"

            sqrt_match = re.search(r"square root of\s*([\d.]+)", expr)
            if sqrt_match:
                expr = f"{sqrt_match.group(1)}**0.5"

            # Word-based operators -> symbols. Without this, words like
            # "times" get silently stripped by the allowed-chars filter
            # below, turning "45 times 12" into the invalid "45  12".
            word_ops = [
                (r"\bmultiplied by\b", "*"),
                (r"\btimes\b", "*"),
                (r"\bx\b", "*"),
                (r"\bdivided by\b", "/"),
                (r"\bover\b", "/"),
                (r"\bplus\b", "+"),
                (r"\badded to\b", "+"),
                (r"\bminus\b", "-"),
                (r"\bsubtracted by\b", "-"),
                (r"\bsubtract\b", "-"),
            ]
            for pattern, symbol in word_ops:
                expr = re.sub(pattern, symbol, expr)

            allowed = "0123456789+-*/(). "
            cleaned = "".join(c for c in expr if c in allowed)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned or tool_input

        return tool_input

    # ========================================
    # 🚀 ACT
    # ========================================
    def act(self, prompt: str) -> str:
        logger.info("Agent received prompt")
        logger.debug(f"Prompt: {prompt[:100]}...")

        tools_text = self._format_tools()

        decision_prompt = f"""You are a strict decision engine.

AVAILABLE TOOLS:
{tools_text}

RULES:
1. Use a tool only if it is clearly needed for the request below.
2. Otherwise choose the llm action.
3. Output ONLY a single valid JSON object. No extra text, no examples.
4. The JSON must be complete and properly closed.

FORMAT:
{{"action": "tool" OR "llm", "tool_name": "...", "tool_input": "..."}}

REQUEST:
{prompt}

JSON:"""

        # ========================================
        # 🔁 RETRY MECHANISM
        # ========================================
        decision = None
        for attempt in range(2):  # try twice
            decision_raw = self.llm.generate(decision_prompt)
            logger.debug(f"Decision raw: {decision_raw}")

            decision = self._extract_json(decision_raw)

            if decision:
                break

            logger.warning(f"Retrying decision generation (attempt {attempt + 1})")

        if not decision:
            logger.warning("Fallback to LLM (no valid JSON)")
            return self.think(prompt)

        # Normalize
        decision = self._normalize_decision(decision)
        logger.info(f"Normalized decision: {decision}")

        action = decision.get("action")
        tool_name = decision.get("tool_name")
        tool_input = decision.get("tool_input", "")

        # ========================================
        # 🔧 TOOL EXECUTION WITH SAFETY
        # ========================================
        if action == "tool" and tool_name in TOOLS:
            logger.info(f"Using tool: {tool_name} | Input: {tool_input}")

            try:
                tool_input = self._clean_tool_input(tool_name, tool_input)

                if not tool_input:
                    logger.warning("Empty tool input after cleaning -> fallback to LLM")
                    return self.think(prompt)

                result = TOOLS[tool_name]["tool"].run(tool_input)

                if not result:
                    logger.warning("Empty tool result -> fallback to LLM")
                    return self.think(prompt)

                answer = f"Answer: {result}"

                # Tool results are stored in memory too, so a
                # follow-up like "was that hotter than yesterday?"
                # still has the earlier answer available as context.
                if self.memory:
                    self.memory.add("user", prompt)
                    self.memory.add("assistant", answer)

                return answer

            except Exception as e:
                logger.error(f"Tool error: {e}")
                return self.think(prompt)

        # ========================================
        # 🧠 LLM FALLBACK
        # ========================================
        logger.info("Using LLM fallback")
        return self.think(prompt)
