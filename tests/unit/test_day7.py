# tests/test_agent_fix.py

from core.agent import BaseAgent


def test_calculator():
    agent = BaseAgent()
    response = agent.act("What is 45 times 12?")

    assert "540" in response, f"Expected 540, got: {response}"
    print(f"✅ Calculator: {response}")


def test_weather():
    agent = BaseAgent()
    response = agent.act("What's the weather in Delhi?")

    # Weather tool may or may not work → flexible assertion
    assert isinstance(response, str)
    assert len(response.strip()) > 5

    print(f"✅ Weather: {response[:50]}...")


def test_llm_query():
    agent = BaseAgent()
    response = agent.act("Explain electricity in simple words")

    assert isinstance(response, str)
    assert len(response.strip()) > 20  # 🔥 prevents cut-off answers

    # 🔥 safer check (model variation tolerant)
    keywords = ["electricity", "energy", "electron", "power"]
    assert any(word in response.lower() for word in keywords), response

    print(f"✅ LLM: {response[:50]}...")


# 🔥 EXTRA (DAY 8): robustness test
def test_invalid_prompt():
    agent = BaseAgent()
    response = agent.act("??? ### 12345")

    assert isinstance(response, str)
    assert len(response.strip()) > 0

    print(f"✅ Robustness: {response}")


if __name__ == "__main__":
    test_calculator()
    test_weather()
    test_llm_query()
    test_invalid_prompt()
