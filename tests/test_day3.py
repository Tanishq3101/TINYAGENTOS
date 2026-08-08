from core.llm_runtime import LLMRuntime

def test_llm_runtime():
    print("TEST STARTED")

    llm = LLMRuntime()

    prompt = "Explain what is electricity in simple terms."

    response = llm.generate(prompt)

    print("\n--- RESPONSE ---")
    print(response)

    assert isinstance(response, str)
    assert len(response) > 0

    print("TEST FINISHED")


if __name__ == "__main__":
    test_llm_runtime()