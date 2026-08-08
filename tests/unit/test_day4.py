from core.agent import BaseAgent


def test_agent():
    print("DAY 4 TEST STARTED")

    agent = BaseAgent()

    prompt = "Explain electricity in 2 lines"

    response = agent.act(prompt)

    print("\n--- AGENT RESPONSE ---")
    print(response)

    assert isinstance(response, str)
    assert len(response) > 0

    print("DAY 4 TEST FINISHED")


if __name__ == "__main__":
    test_agent()
