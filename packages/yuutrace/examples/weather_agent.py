"""Weather Agent Example - Demonstrates yuutrace instrumentation.

This example shows a realistic multi-turn agent conversation with:
- Multiple LLM calls with token usage and cost tracking
- Tool calls (weather API, unit conversion, search)
- Error handling and retries
- Nested tool execution
- Cache token tracking

Run this example:
    1. Start the collector:
       ytrace server --db ./traces.db --port 4318

    2. In another terminal, run this script:
       python examples/weather_agent.py

    3. View traces in the UI:
       ytrace ui --db ./traces.db --port 8080
       Open http://localhost:8080 in your browser
"""

import asyncio
import json
import random
from typing import Any
from uuid import uuid4

import yuutrace as ytrace

type ChatMessage = dict[str, Any]


# ---------------------------------------------------------------------------
# Setup tracing
# ---------------------------------------------------------------------------


def setup_tracing():
    ytrace.init(service_name="weather-agent-example", service_version="1.0.0")
    print("✓ Tracing configured to export to http://localhost:4318/v1/traces")


# ---------------------------------------------------------------------------
# Mock Tool Functions
# ---------------------------------------------------------------------------


async def get_weather(city: str, units: str = "celsius") -> dict:
    """Simulate fetching weather data from an API."""
    await asyncio.sleep(0.3)  # Simulate API latency

    # Simulate occasional API errors
    if random.random() < 0.1:
        raise ValueError(f"Weather API error: City '{city}' not found")

    # Mock weather data
    temp = random.randint(15, 30) if units == "celsius" else random.randint(59, 86)
    conditions = random.choice(["sunny", "cloudy", "rainy", "partly cloudy"])

    # Record tool usage (API call count)
    ytrace.record_tool_usage(
        ytrace.ToolUsageDelta(
            name="get_weather",
            unit="api_calls",
            quantity=1.0,
        )
    )

    # Record tool cost (mock API pricing: $0.001 per call)
    ytrace.record_cost(
        category="tool",
        currency="USD",
        amount=0.001,
        tool_name="get_weather",
    )

    return {
        "city": city,
        "temperature": temp,
        "units": units,
        "conditions": conditions,
        "humidity": random.randint(40, 80),
        "wind_speed": random.randint(5, 25),
    }


async def convert_temperature(temp: float, from_unit: str, to_unit: str) -> float:
    """Convert temperature between celsius and fahrenheit."""
    await asyncio.sleep(0.1)

    if from_unit == "celsius" and to_unit == "fahrenheit":
        result = (temp * 9 / 5) + 32
    elif from_unit == "fahrenheit" and to_unit == "celsius":
        result = (temp - 32) * 5 / 9
    else:
        result = temp

    return round(result, 1)


async def search_web(query: str) -> list[dict]:
    """Simulate web search for additional context."""
    await asyncio.sleep(0.4)

    # Record search tool usage
    ytrace.record_tool_usage(
        ytrace.ToolUsageDelta(
            name="search_web",
            unit="queries",
            quantity=1.0,
        )
    )

    # Record search cost (mock pricing: $0.002 per query)
    ytrace.record_cost(
        category="tool",
        currency="USD",
        amount=0.002,
        tool_name="search_web",
    )

    # Mock search results
    return [
        {
            "title": f"Weather in {query}",
            "snippet": "Current weather conditions and forecast...",
            "url": f"https://weather.example.com/{query}",
        },
        {
            "title": f"Climate data for {query}",
            "snippet": "Historical weather patterns and statistics...",
            "url": f"https://climate.example.com/{query}",
        },
    ]


# ---------------------------------------------------------------------------
# Mock LLM Functions
# ---------------------------------------------------------------------------


async def call_llm(
    messages: list[dict],
    model: str = "gpt-4o",
    use_cache: bool = False,
) -> dict:
    """Simulate an LLM API call with realistic token usage."""
    await asyncio.sleep(0.5)  # Simulate API latency

    # Calculate mock token counts based on message content
    input_text = " ".join(str(m.get("content", "")) for m in messages)
    input_tokens = len(input_text.split()) * 2  # Rough approximation
    output_tokens = random.randint(50, 200)

    # Simulate cache hits (30% chance if cache enabled)
    cache_read_tokens = 0
    if use_cache and random.random() < 0.3:
        cache_read_tokens = int(input_tokens * 0.7)  # 70% cache hit
        input_tokens = int(input_tokens * 0.3)  # Only 30% new tokens

    # Record token usage
    ytrace.record_llm_usage(
        provider="openai",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
    )

    # Calculate cost based on model pricing
    if model == "gpt-4o":
        input_cost = input_tokens * 0.0025 / 1000
        output_cost = output_tokens * 0.01 / 1000
        cache_cost = cache_read_tokens * 0.00125 / 1000  # 50% discount for cache
    else:  # gpt-3.5-turbo
        input_cost = input_tokens * 0.0005 / 1000
        output_cost = output_tokens * 0.0015 / 1000
        cache_cost = cache_read_tokens * 0.00025 / 1000

    total_cost = input_cost + output_cost + cache_cost

    ytrace.record_cost(
        category="llm",
        currency="USD",
        amount=total_cost,
        llm_provider="openai",
        llm_model=model,
    )

    return {
        "content": "Mock LLM response",
        "tool_calls": [],
        "finish_reason": "stop",
    }


async def run_tool_batch(chat, calls: list[dict]) -> list[dict]:
    with chat.tool_batch() as tools:
        async def run_one(call: dict) -> dict:
            with tools.tool(
                name=call["name"],
                call_id=call["tool_call_id"],
                input=call["params"],
            ) as tool:
                try:
                    output = await call["tool"](**call["params"])
                except Exception as exc:
                    error = str(exc)
                    tool.fail(error)
                    return {"tool_call_id": call["tool_call_id"], "output": None, "error": error}
                tool.ok(output)
                return {"tool_call_id": call["tool_call_id"], "output": output, "error": None}

        return await asyncio.gather(*(run_one(call) for call in calls))


# ---------------------------------------------------------------------------
# Agent Logic
# ---------------------------------------------------------------------------


async def run_weather_agent():
    """Run a multi-turn weather agent conversation."""

    conversation_id = uuid4()
    agent_name = "weather-assistant"
    model = "gpt-4o"

    print(f"\n{'=' * 70}")
    print(f"Starting conversation: {conversation_id}")
    print(f"Agent: {agent_name} | Model: {model}")
    print(f"{'=' * 70}\n")

    with ytrace.conversation(
        id=conversation_id,
        agent=agent_name,
        model=model,
        tags=["demo", "user:user_123"],
    ) as chat:
        # System prompt
        system_prompt = (
            "You are a helpful weather assistant. You can check weather conditions, "
            "convert temperature units, and search for weather-related information."
        )

        tool_specs = [
            {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "city": {"type": "string", "required": True},
                    "units": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
            },
            {
                "name": "convert_temperature",
                "description": "Convert temperature between units",
                "parameters": {
                    "temp": {"type": "number", "required": True},
                    "from_unit": {"type": "string", "required": True},
                    "to_unit": {"type": "string", "required": True},
                },
            },
            {
                "name": "search_web",
                "description": "Search the web for information",
                "parameters": {
                    "query": {"type": "string", "required": True},
                },
            },
        ]

        chat.system(persona=system_prompt, tools=tool_specs)

        # User query
        user_query = "What's the weather like in Tokyo and San Francisco? Compare them."
        chat.user(user_query)
        print(f"👤 User: {user_query}\n")

        # Turn 1: Initial LLM call - decides to use tools
        print("🤖 Turn 1: Planning tool calls...")
        with chat.turn("assistant") as turn:
            messages: list[ChatMessage] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ]

            await call_llm(messages, model=model)

            tool_calls = [
                {
                    "id": "call_tokyo",
                    "function": "get_weather",
                    "arguments": {"city": "Tokyo", "units": "celsius"},
                },
                {
                    "id": "call_sf",
                    "function": "get_weather",
                    "arguments": {"city": "San Francisco", "units": "celsius"},
                },
            ]

            turn.add(
                {"type": "text", "text": "I'll check the weather in both cities."},
                {"type": "tool_calls", "tool_calls": tool_calls},
            )

        # Execute tools
        print("🔧 Executing tools: get_weather (Tokyo), get_weather (San Francisco)")
        results = await run_tool_batch(
            chat,
            [
                {
                    "tool_call_id": "call_tokyo",
                    "name": "get_weather",
                    "tool": get_weather,
                    "params": {"city": "Tokyo", "units": "celsius"},
                },
                {
                    "tool_call_id": "call_sf",
                    "name": "get_weather",
                    "tool": get_weather,
                    "params": {"city": "San Francisco", "units": "celsius"},
                },
            ],
        )

        tokyo_weather = results[0]["output"]
        sf_weather = results[1]["output"]
        print(
            f"   ✓ Tokyo: {tokyo_weather['temperature']}°C, {tokyo_weather['conditions']}"
        )
        print(
            f"   ✓ San Francisco: {sf_weather['temperature']}°C, {sf_weather['conditions']}\n"
        )

        # Turn 2: LLM synthesizes results with cache
        print("🤖 Turn 2: Synthesizing weather comparison (with cache)...")
        with chat.turn("assistant") as turn:
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "I'll check both cities.",
                        "tool_calls": tool_calls,
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_tokyo",
                        "content": json.dumps(tokyo_weather),
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_sf",
                        "content": json.dumps(sf_weather),
                    },
                ]
            )

            await call_llm(messages, model=model, use_cache=True)

            comparison = (
                f"Tokyo is currently {tokyo_weather['temperature']}°C and {tokyo_weather['conditions']}, "
                f"while San Francisco is {sf_weather['temperature']}°C and {sf_weather['conditions']}."
            )

            turn.add({"type": "text", "text": comparison})
            print(f"   {comparison}\n")

        # Turn 3: User asks for unit conversion
        followup = "Can you convert Tokyo's temperature to Fahrenheit?"
        chat.user(followup)
        print(f"👤 User: {followup}\n")

        print("🤖 Turn 3: Converting temperature...")
        with chat.turn("assistant") as turn:
            messages.append({"role": "user", "content": followup})
            await call_llm(messages, model=model, use_cache=True)

            tool_calls = [
                {
                    "id": "call_convert",
                    "function": "convert_temperature",
                    "arguments": {
                        "temp": tokyo_weather["temperature"],
                        "from_unit": "celsius",
                        "to_unit": "fahrenheit",
                    },
                }
            ]

            turn.add({"type": "tool_calls", "tool_calls": tool_calls})

        results = await run_tool_batch(
            chat,
            [
                {
                    "tool_call_id": "call_convert",
                    "name": "convert_temperature",
                    "tool": convert_temperature,
                    "params": {
                        "temp": tokyo_weather["temperature"],
                        "from_unit": "celsius",
                        "to_unit": "fahrenheit",
                    },
                },
            ],
        )

        temp_f = results[0]["output"]
        print(f"   ✓ {tokyo_weather['temperature']}°C = {temp_f}°F\n")

        # Turn 4: Final response
        print("🤖 Turn 4: Providing final answer...")
        with chat.turn("assistant") as turn:
            messages.extend(
                [
                    {"role": "assistant", "tool_calls": tool_calls},
                    {
                        "role": "tool",
                        "tool_call_id": "call_convert",
                        "content": str(temp_f),
                    },
                ]
            )

            await call_llm(messages, model=model, use_cache=True)

            final_response = f"Tokyo's temperature of {tokyo_weather['temperature']}°C is {temp_f}°F."
            turn.add({"type": "text", "text": final_response})
            print(f"   {final_response}\n")

        # Bonus: Demonstrate error handling with retry
        print("🤖 Bonus: Demonstrating error handling...")
        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                results = await run_tool_batch(
                    chat,
                    [
                        {
                            "tool_call_id": "call_invalid",
                            "name": "get_weather",
                            "tool": get_weather,
                            "params": {
                                "city": "InvalidCity123",
                                "units": "celsius",
                            },
                        },
                    ],
                )

                if results[0]["error"]:
                    print(f"   ⚠ Attempt {retry_count + 1}: {results[0]['error']}")
                    retry_count += 1
                    if retry_count < max_retries:
                        await asyncio.sleep(0.5)
                        continue
                break
            except Exception as e:
                print(f"   ⚠ Attempt {retry_count + 1} failed: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(0.5)

        print(f"\n{'=' * 70}")
        print("✓ Conversation complete!")
        print(f"  Conversation ID: {conversation_id}")
        print("  View in UI: http://localhost:8080")
        print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    """Run multiple example conversations."""
    setup_tracing()

    print("\n" + "=" * 70)
    print("Weather Agent Example - yuutrace Instrumentation Demo")
    print("=" * 70)
    print("\nThis example demonstrates:")
    print("  • Multi-turn agent conversations")
    print("  • LLM calls with token usage and cost tracking")
    print("  • Tool execution with usage metrics")
    print("  • Cache token tracking")
    print("  • Error handling and retries")
    print("\nMake sure ytrace server is running:")
    print("  ytrace server --db ./traces.db --port 4318")
    print("=" * 70)

    # Run the main conversation
    await run_weather_agent()

    # Give time for spans to be exported
    print("⏳ Waiting for traces to be exported...")
    await asyncio.sleep(2)

    print("\n✓ All traces exported!")
    print("\nNext steps:")
    print("  1. Start the UI: ytrace ui --db ./traces.db --port 8080")
    print("  2. Open http://localhost:8080 in your browser")
    print("  3. Explore the conversation traces, costs, and token usage\n")


if __name__ == "__main__":
    asyncio.run(main())
