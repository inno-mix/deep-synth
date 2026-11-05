import os

# Isolate tests from any developer .env: in-memory-ish sqlite, no tracing, no real keys.
os.environ.update(
    {
        "LLM_PROVIDER": "ollama",
        "DATABASE_URL": "sqlite+aiosqlite:///./test_deep_synth.db",
        "PHOENIX_COLLECTOR_ENDPOINT": "",
        "TAVILY_API_KEY": "",
        "RATE_LIMIT_PER_HOUR": "1000",
    }
)
