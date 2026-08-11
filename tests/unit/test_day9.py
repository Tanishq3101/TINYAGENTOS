from core.llm_runtime import LLMRuntime
from agents.base import AgentConfig
from agents.summarizer import SummarizerAgent
from agents.extractor import ExtractorAgent
from agents.critic import CriticAgent
from infrastructure.retry import RetryPolicy, retry_on_exception

SAMPLE_TEXT = """
Artificial intelligence is transforming how software is built. Small,
efficient language models can now run directly on consumer laptops,
enabling developers to build offline-first AI applications. Companies
like Anthropic and OpenAI continue to push the frontier of what large
models can do, while open-weight models make local inference
increasingly practical.
"""


def test_critic_agent_full_pipeline():
    """Runs Summarizer -> Extractor -> Critic end to end, the way an
    orchestrator eventually will."""
    llm = LLMRuntime()

    summarizer = SummarizerAgent(
        AgentConfig(name="summarizer", description="Test summarizer", max_tokens=256),
        llm,
    )
    extractor = ExtractorAgent(
        AgentConfig(name="extractor", description="Test extractor", max_tokens=384),
        llm,
    )
    critic = CriticAgent(
        AgentConfig(name="critic", description="Test critic", max_tokens=400),
        llm,
    )

    summary_result = summarizer.execute(SAMPLE_TEXT)
    assert summary_result["status"] == "success", summary_result
    summary = summary_result["output"]

    extraction_result = extractor.execute(SAMPLE_TEXT)
    assert extraction_result["status"] == "success", extraction_result
    extraction = extraction_result["output"]

    critic_result = critic.execute(SAMPLE_TEXT, summary=summary, extraction=extraction)
    assert critic_result["status"] == "success", critic_result

    output = critic_result["output"]
    assert "evaluation" in output
    assert isinstance(output["evaluation"], str)
    assert len(output["evaluation"]) > 0
    # score may be None if parsing failed, but the key must exist
    assert "score" in output

    print(f"✅ Critic score: {output['score']}")
    print(
        f"✅ Critic feedback: {output['feedback'][:80] if output['feedback'] else '(none parsed)'}"
    )


def test_critic_agent_missing_inputs():
    """CriticAgent should fail gracefully (not raise) if summary or
    extraction weren't provided, since execute() wraps _execute_task
    in try/except."""
    llm = LLMRuntime()
    critic = CriticAgent(
        AgentConfig(name="critic", description="Test critic"),
        llm,
    )

    result = critic.execute(SAMPLE_TEXT)  # no summary/extraction kwargs

    assert result["status"] == "error"
    assert "summary" in result["error"] or "extraction" in result["error"]
    print(f"✅ Missing inputs handled: {result['error']}")


def test_retry_succeeds_after_transient_failures():
    """A function that fails twice then succeeds should still return
    successfully under the retry policy."""
    attempts = {"count": 0}

    policy = RetryPolicy(max_retries=3, base_delay=0.01, exponential_base=2.0)

    @retry_on_exception(policy, exceptions=(ValueError,))
    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ValueError("transient failure")
        return "ok"

    result = flaky()

    assert result == "ok"
    assert attempts["count"] == 3
    print(f"✅ Retry succeeded after {attempts['count']} attempts")


def test_retry_raises_after_exhausting_attempts():
    """A function that always fails should raise the underlying
    exception once max_retries is exhausted, not swallow it."""
    policy = RetryPolicy(max_retries=2, base_delay=0.01, exponential_base=2.0)

    @retry_on_exception(policy, exceptions=(RuntimeError,))
    def always_fails():
        raise RuntimeError("permanent failure")

    try:
        always_fails()
        assert False, "Expected RuntimeError to be raised"
    except RuntimeError as e:
        assert "permanent failure" in str(e)
        print(f"✅ Retry correctly exhausted and raised: {e}")


def test_retry_delay_calculation():
    """Exponential backoff math should be correct and capped at
    max_delay."""
    policy = RetryPolicy(max_retries=5, base_delay=1.0, max_delay=10.0, exponential_base=2.0)

    assert policy.calculate_delay(0) == 1.0
    assert policy.calculate_delay(1) == 2.0
    assert policy.calculate_delay(2) == 4.0
    assert policy.calculate_delay(3) == 8.0
    assert policy.calculate_delay(4) == 10.0  # capped at max_delay (would be 16.0)

    print("✅ Retry delay calculation correct (including max_delay cap)")


if __name__ == "__main__":
    test_critic_agent_full_pipeline()
    test_critic_agent_missing_inputs()
    test_retry_succeeds_after_transient_failures()
    test_retry_raises_after_exhausting_attempts()
    test_retry_delay_calculation()
    print("\nAll Day 9 tests passed.")
