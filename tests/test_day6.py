from core.agent import BaseAgent


def test_day6():
    print("DAY 6 TEST STARTED")

    agent = BaseAgent()

    print("\n--- TEST 1 (MATH) ---")
    print(agent.act("What is 25 * 18?"))

    print("\n--- TEST 2 (NORMAL) ---")
    print(agent.act("Explain electricity simply"))

    print("\nDAY 6 TEST FINISHED")


if __name__ == "__main__":
    test_day6()
