"""Arize Phoenix tracing via OpenTelemetry + OpenInference LangChain auto-instrumentation.

Dev:  docker compose up phoenix  ->  PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
Prod: Phoenix Cloud             ->  PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com/s/<space>
                                    PHOENIX_API_KEY=...
"""

import logging

from app.config import get_settings

log = logging.getLogger(__name__)
_done = False


def setup_tracing() -> bool:
    global _done
    s = get_settings()
    if _done or not s.phoenix_collector_endpoint:
        return _done
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        provider = register(
            project_name=s.phoenix_project_name,
            endpoint=s.phoenix_collector_endpoint.rstrip("/") + "/v1/traces",
            api_key=s.phoenix_api_key,
            batch=True,
            verbose=False,
        )
        LangChainInstrumentor().instrument(tracer_provider=provider)
        _done = True
        log.info("Phoenix tracing enabled -> %s", s.phoenix_collector_endpoint)
    except Exception as e:  # tracing must never break the app
        log.warning("Phoenix tracing disabled: %s", e)
    return _done
