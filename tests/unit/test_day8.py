import json

from core.llm_runtime import LLMRuntime
from agents.base import AgentConfig
from agents.summarizer import SummarizerAgent
from agents.extractor import ExtractorAgent

SAMPLE_TEXT = """
Artificial intelligence is transforming how software is built. Small,
efficient language models can now run directly on consumer laptops,
enabling developers to build offline-first AI applications. Companies
like Anthropic and OpenAI continue to push the frontier of what large
models can do, while open-weight models make local inference
increasingly practical.
"""


def test_summarizer_agent():
    llm = LLMRuntime()
    config = AgentConfig(name="summarizer", description="Test summarizer", max_tokens=256)
    agent = SummarizerAgent(config, llm)

    result = agent.execute(SAMPLE_TEXT)

    assert result["status"] == "success", result
    output = result["output"]
    assert isinstance(output, str)
    assert len(output.strip()) > 0
    # Summary should be meaningfully shorter than the source text
    assert len(output) < len(SAMPLE_TEXT)

    print(f"✅ Summarizer: {output[:80]}...")


def test_extractor_agent():
    llm = LLMRuntime()
    config = AgentConfig(name="extractor", description="Test extractor", max_tokens=384)
    agent = ExtractorAgent(config, llm)

    result = agent.execute(SAMPLE_TEXT)

    assert result["status"] == "success", result
    output = result["output"]

    # Output must always be valid JSON, even on model failure
    # (ExtractorAgent guarantees this via its fallback default)
    parsed = json.loads(output)
    assert "key_points" in parsed
    assert "entities" in parsed
    assert "sentiment" in parsed
    assert "topics" in parsed
    assert parsed["sentiment"] in ("positive", "neutral", "negative")

    print(f"✅ Extractor: {output[:80]}...")


def test_agent_empty_input_handled():
    """Agents should fail gracefully (not raise) on empty input."""
    llm = LLMRuntime()
    config = AgentConfig(name="summarizer", description="Test summarizer")
    agent = SummarizerAgent(config, llm)

    result = agent.execute("")

    assert result["status"] == "error"
    assert "error" in result
    print(f"✅ Empty input handled: {result['error']}")


if __name__ == "__main__":
    test_summarizer_agent()
    test_extractor_agent()
    test_agent_empty_input_handled()
    print("\nAll Day 8 tests passed.")
