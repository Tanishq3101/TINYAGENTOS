from agents.base import Agent, AgentConfig  # noqa: F401  (AgentConfig re-exported for convenience)


class SummarizerAgent(Agent):
    """Condenses input text into a concise summary."""

    def build_prompt(self, input_data: str, **kwargs) -> str:
        # Limit input to prevent context overflow
        truncated = input_data[:3000]

        return f"""You are an expert summarizer. Your task is to create a clear, concise summary of the following text.

Guidelines:
- Preserve key information and main points
- Use clear, direct language
- Keep the summary to 2-3 sentences maximum
- Focus on what matters most

Text to summarize:
{truncated}

Provide ONLY the summary, no additional commentary."""

    def _execute_task(self, input_data: str, **kwargs) -> str:
        prompt = self.build_prompt(input_data, **kwargs)

        response = self.llm.generate(
            prompt,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        return response.strip()
