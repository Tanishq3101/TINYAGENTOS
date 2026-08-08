import re

from agents.base import Agent, AgentConfig  # noqa: F401
from infrastructure.logging import logger


class CriticAgent(Agent):
    """Evaluates the quality of a summary + extraction pair produced by
    SummarizerAgent / ExtractorAgent, and returns structured feedback.

    Unlike the other agents, CriticAgent needs more than the raw input
    text — it needs the summary and extraction to judge. Those are
    passed as keyword arguments to execute()/build_prompt(), e.g.:

        critic.execute(
            original_text,
            summary=summary_text,
            extraction=extraction_json,
        )
    """

    def build_prompt(
        self, input_data: str, summary: str = "", extraction: str = "", **kwargs
    ) -> str:
        original = input_data[:1500]
        summary = (summary or "")[:1000]
        extraction = (extraction or "")[:1000]

        return f"""You are an expert evaluator. Rate the quality of the provided summary and extraction.

Original text:
{original}

Summary:
{summary}

Extracted information:
{extraction}

Provide a detailed evaluation in this exact format:

Score: <a number from 0 to 10>
Feedback: <one or two sentences of overall feedback>
Strengths: <comma-separated list>
Weaknesses: <comma-separated list>
Recommendations: <comma-separated list>

Be objective and specific."""

    def _execute_task(
        self, input_data: str, summary: str = "", extraction: str = "", **kwargs
    ) -> dict:
        if not summary or not extraction:
            raise ValueError(
                "CriticAgent requires both 'summary' and 'extraction' "
                "keyword arguments to evaluate against."
            )

        prompt = self.build_prompt(input_data, summary=summary, extraction=extraction, **kwargs)

        response = self.llm.generate(
            prompt,
            max_tokens=self.config.max_tokens,
            temperature=0.5,
        )

        raw_text = response.strip()
        parsed = self._parse_evaluation(raw_text)

        return {
            "evaluation": raw_text,
            **parsed,
        }

    @staticmethod
    def _parse_evaluation(text: str) -> dict:
        """Best-effort structured parse of the Score/Feedback/etc.
        fields out of the model's free-text evaluation. Falls back to
        safe defaults for any field it can't find rather than raising
        -- the raw 'evaluation' text is always preserved regardless,
        so nothing is lost if parsing misses a field."""

        def _find(pattern: str) -> str:
            match = re.search(pattern, text, re.IGNORECASE)
            return match.group(1).strip() if match else ""

        score_str = _find(r"score\s*:\s*([\d.]+)")
        try:
            score = float(score_str) if score_str else None
        except ValueError:
            score = None

        def _split_list(raw: str) -> list:
            if not raw:
                return []
            return [item.strip() for item in raw.split(",") if item.strip()]

        feedback = _find(r"feedback\s*:\s*(.+)")
        strengths = _split_list(_find(r"strengths\s*:\s*(.+)"))
        weaknesses = _split_list(_find(r"weaknesses\s*:\s*(.+)"))
        recommendations = _split_list(_find(r"recommendations\s*:\s*(.+)"))

        if score is None:
            logger.warning("CriticAgent: could not parse a numeric score from evaluation")

        return {
            "score": score,
            "feedback": feedback,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
        }
