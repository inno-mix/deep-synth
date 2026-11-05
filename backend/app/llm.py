"""LLM provider factory + schema-enforced structured output with validation-feedback retries."""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class StructuredOutputError(RuntimeError):
    pass


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    s = settings or get_settings()
    if s.llm_provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not s.google_api_key:
            raise RuntimeError("LLM_PROVIDER=google requires GOOGLE_API_KEY (Google AI Studio)")
        return ChatGoogleGenerativeAI(
            model=s.gemini_model,
            google_api_key=s.google_api_key,
            temperature=s.llm_temperature,
            max_retries=4,  # backs off on free-tier 429s
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=s.ollama_model,
        base_url=s.ollama_base_url,
        temperature=s.llm_temperature,
        num_ctx=s.ollama_num_ctx,
    )


async def structured[T: BaseModel](
    schema: type[T],
    system: str,
    user: str,
    *,
    llm: BaseChatModel | None = None,
    run_name: str | None = None,
) -> T:
    """Call the LLM constrained to `schema`; on invalid output, feed the error back and retry.

    Both Ollama and Gemini support native JSON-schema constrained decoding, but small local
    models still occasionally violate field constraints, so we validate and self-correct.
    """
    s = get_settings()
    model = (llm or get_llm()).with_structured_output(schema, method="json_schema", include_raw=True)
    messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
    last_err: Exception | None = None

    for attempt in range(s.structured_retries + 1):
        out = await model.ainvoke(messages, config={"run_name": run_name or schema.__name__})
        parsed, raw, err = out.get("parsed"), out.get("raw"), out.get("parsing_error")
        if parsed is not None and err is None:
            return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        raw_text = getattr(raw, "content", "") if raw is not None else ""
        if err is None and raw_text:
            try:  # some providers return raw JSON without parsing it
                return schema.model_validate_json(raw_text)
            except ValidationError as e:
                err = e
        last_err = err or StructuredOutputError("empty response")
        log.warning("structured output invalid (attempt %d): %s", attempt + 1, last_err)
        messages += [
            AIMessage(str(raw_text)[:4000] or "<empty>"),
            HumanMessage(
                "Your previous output did not match the required JSON schema.\n"
                f"Error: {str(last_err)[:1500]}\nReturn ONLY corrected JSON matching the schema."
            ),
        ]
    raise StructuredOutputError(f"{schema.__name__}: {last_err}")
