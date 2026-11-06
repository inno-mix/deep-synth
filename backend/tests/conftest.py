import os

# Isolate tests from any developer .env: in-memory-ish sqlite, no tracing, no real keys.
os.environ.update(
    {
        "DEEP_SYNTH_IGNORE_DOTENV": "1",
        "LLM_PROVIDER": "ollama",
        "DATABASE_URL": "sqlite+aiosqlite:///./test_deep_synth.db",
        "PHOENIX_COLLECTOR_ENDPOINT": "",
        "TAVILY_API_KEY": "",
        "RATE_LIMIT_PER_HOUR": "1000",
    }
)

# Start every test session with empty app and checkpoint databases.
for _f in ("test_deep_synth.db", "test_deep_synth.checkpoints.db"):
    if os.path.exists(_f):
        os.remove(_f)
