import sys

sys.stdout.reconfigure(encoding="utf-8")
from core.agent import BaseAgent


def test_day5():
    print("DAY 5 TEST STARTED")

    agent = BaseAgent()

    # 🔹 TOOL TEST
    print("\n--- TOOL TEST ---")
    result1 = agent.act("25 * 18")
    print(result1)

    # 🔹 LLM TEST
    print("\n--- LLM TEST ---")
    result2 = agent.act("Explain electricity simply")
    print(result2)

    print("\nDAY 5 TEST FINISHED")


# 🔥 THIS IS THE FIX
if __name__ == "__main__":
    test_day5()
