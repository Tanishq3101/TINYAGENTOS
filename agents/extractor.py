import json
import re

from agents.base import Agent, AgentConfig  # noqa: F401
from infrastructure.logging import logger

# Safe default returned when the model fails to produce valid JSON,
# so downstream consumers (e.g. CriticAgent, orchestrator) never have
# to handle a malformed/missing extraction as a special case.
DEFAULT_EXTRACTION = json.dumps(
    {
        "key_points": [],
        "entities": {"person": [], "organization": [], "location": []},
        "sentiment": "neutral",
        "topics": [],
    }
)


class ExtractorAgent(Agent):
    """Extracts key points, entities, sentiment, and topics from text
    and returns them as a JSON string."""

    def build_prompt(self, input_data: str, **kwargs) -> str:
        truncated = input_data[:3000]

        return f"""You are an expert information extractor. Extract key information from the text below.

Extract and return as JSON:
{{
    "key_points": ["point1", "point2", ...],
    "entities": {{"person": [...], "organization": [...], "location": [...]}},
    "sentiment": "positive|neutral|negative",
    "topics": ["topic1", "topic2", ...]
}}

Text:
{truncated}

Return ONLY valid JSON, no other text."""

    def _execute_task(self, input_data: str, **kwargs) -> str:
        prompt = self.build_prompt(input_data, **kwargs)

        response = self.llm.generate(
            prompt,
            max_tokens=self.config.max_tokens,
            temperature=0.3,  # lower temp for structured, deterministic output
        )

        text = response.strip()

        parsed = self._parse_json(text)
        if parsed is not None:
            return json.dumps(parsed)

        logger.warning(
            f"Agent {self.config.name}: invalid JSON from extractor, "
            f"falling back to default. Raw: {text[:150]}"
        )
        return DEFAULT_EXTRACTION

    @staticmethod
    def _parse_json(text: str):
        """Try direct parsing first, then fall back to pulling the
        first {...} block out of the response. The model sometimes
        wraps JSON in prose or markdown code fences despite explicit
        instructions not to."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip common markdown code-fence wrapping
        fenced = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        if fenced != text:
            try:
                return json.loads(fenced)
            except json.JSONDecodeError:
                pass

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None
