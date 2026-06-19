# yuutrace Examples

This directory contains example code demonstrating how to use yuutrace for instrumenting LLM agent applications.

## Weather Agent Example

**File:** `weather_agent.py`

A comprehensive example showing a multi-turn weather assistant agent with:

- **Multiple LLM calls** with realistic token usage and cost tracking
- **Tool execution** (weather API, temperature conversion, web search)
- **Cache token tracking** (simulating prompt caching)
- **Error handling** with retries
- **Nested tool calls** and concurrent execution
- **Cost and usage metrics** for both LLM and tool operations

### Running the Example

#### 1. Start the trace collector

In one terminal, start the ytrace server to collect traces:

```bash
ytrace server --db ./traces.db --port 4318
```

This starts an OTLP/HTTP JSON collector that stores traces to SQLite.

#### 2. Run the example

In another terminal, run the weather agent example:

```bash
python examples/weather_agent.py
```

You should see output showing the agent's conversation flow, tool calls, and trace export confirmation.

#### 3. View traces in the UI

Start the web UI to visualize the collected traces:

```bash
ytrace ui --db ./traces.db --port 8080
```

Then open http://localhost:8080 in your browser.

### What to Look For in the UI

Once you open the UI, you'll be able to:

1. **Browse conversations** - See all collected conversation traces in a searchable list
2. **View conversation flow** - See the waterfall of LLM calls and tool executions
3. **Inspect costs** - See cost breakdown by category (LLM vs tools) and by model
4. **Analyze token usage** - View input/output/cache token counts for each LLM call
5. **Examine timing** - See the duration and timeline of each operation
6. **Debug errors** - Identify failed tool calls and error messages

### Example Output

The example simulates a realistic agent conversation:

```
👤 User: What's the weather like in Tokyo and San Francisco? Compare them.

���� Turn 1: Planning tool calls...
🔧 Executing tools: get_weather (Tokyo), get_weather (San Francisco)
   ✓ Tokyo: 24°C, sunny
   ✓ San Francisco: 18°C, cloudy

🤖 Turn 2: Synthesizing weather comparison (with cache)...
   Tokyo is currently 24°C and sunny, while San Francisco is 18°C and cloudy.

👤 User: Can you convert Tokyo's temperature to Fahrenheit?

🤖 Turn 3: Converting temperature...
   ✓ 24°C = 75.2°F

🤖 Turn 4: Providing final answer...
   Tokyo's temperature of 24°C is 75.2°F.
```

### Key Instrumentation Patterns

The example demonstrates several important patterns:

#### 0. Tracing Setup (Required)

```python
import yuutrace as ytrace

ytrace.init(service_name="weather-agent-example", service_version="1.0.0")
```

#### 1. Conversation Context

```python
with ytrace.conversation(
    id=uuid4(),
    agent="weather-assistant",
    model="gpt-4o",
    tags=["demo", "user:user_123"],
) as chat:
    chat.system(persona=system_prompt, tools=tool_specs)
    chat.user(user_query)
    # ... rest of conversation
```

#### 2. LLM Generation Tracking

```python
with chat.turn("assistant") as turn:
    response = await call_llm(messages, model=model)
    turn.add(*response_items)
    
    # Either use the typed wrappers inside the active turn...
    ytrace.record_llm_usage(
        provider="openai",
        model=model,
        input_tokens=150,
        output_tokens=42,
        cache_read_tokens=50,
    )
    ytrace.record_cost(
        category="llm",
        currency="USD",
        amount=0.0023,
        llm_provider="openai",
        llm_model=model,
    )
    # ...or call turn.usage(response_usage, cost=response_cost)
```

#### 3. Tool Execution

```python
with chat.tool_batch() as tools:
    with tools.tool(
        name="get_weather",
        call_id="call_1",
        input={"city": "Tokyo", "units": "celsius"},
    ) as tool:
        tokyo = await get_weather(city="Tokyo", units="celsius")
        tool.ok(tokyo)
```

#### 4. Tool Usage and Cost Tracking

```python
# Inside tool function
ytrace.record_tool_usage(
    ytrace.ToolUsageDelta(
        name="get_weather",
        unit="api_calls",
        quantity=1.0,
    )
)

ytrace.record_cost(
    category="tool",
    currency="USD",
    amount=0.001,
    tool_name="get_weather",
)
```

## Creating Your Own Examples

To instrument your own agent:

1. **Initialize tracing** (recommended): `ytrace.init(...)`
2. **Wrap conversations** with `ytrace.conversation()`
3. **Track LLM calls** with `chat.turn("assistant")` and record usage/cost
4. **Track tool calls** with `chat.tool_batch()` and record usage/cost
5. **Run your agent** and view traces in the UI

See `weather_agent.py` for a complete working example.
