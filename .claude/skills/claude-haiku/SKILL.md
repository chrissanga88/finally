---
name: claude-haiku
description: Write Python code that calls an LLM using Claude Haiku 5.5 through the official Anthropic SDK, for both plain text and Pydantic Structured Outputs responses. Use whenever the app needs to call an LLM.
---

# Calling an LLM via Claude Haiku 5.5

These instructions allow you to write code that calls an LLM, with Claude Haiku 5.5 as the model.
This method uses the official Anthropic Python SDK directly (no LiteLLM, no OpenRouter).

## Setup

The ANTHROPIC_API_KEY must be set in the .env file and loaded in as an environment variable (for example with `python-dotenv`'s `load_dotenv()`). The SDK reads it automatically, so never hard-code the key.

The uv project must include anthropic, pydantic and python-dotenv. `uv add anthropic pydantic python-dotenv`

## Code snippets

Use code like these examples in order to call Claude Haiku 5.5.

### Imports and constants

```python
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 16000
OUTPUT_CONFIG = {"effort": "low"}  # equivalent of reasoning_effort="low"
```

### Helper: split out the system prompt

Unlike OpenAI-style APIs, Claude takes the system prompt as a separate `system` argument, not as a message with `"role": "system"` at the start of `messages`. Use this helper so `messages` can stay in the same format as before.

```python
def split_system(messages):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    chat = [m for m in messages if m["role"] != "system"]
    return system, chat
```

### Code to call Claude Haiku 5.5 for a text response

```python
system, chat = split_system(messages)
response = client.messages.create(
    model=MODEL,
    max_tokens=MAX_TOKENS,
    system=system,
    messages=chat,
    output_config=OUTPUT_CONFIG,
)
result = "".join(block.text for block in response.content if block.type == "text")
```

### Code to call Claude Haiku 5.5 for a Structured Outputs response

```python
system, chat = split_system(messages)
response = client.messages.parse(
    model=MODEL,
    max_tokens=MAX_TOKENS,
    system=system,
    messages=chat,
    output_config=OUTPUT_CONFIG,
    output_format=MyBaseModelSubclass,
)
result_as_object = response.parsed_output  # already a validated MyBaseModelSubclass instance
```

## Rules

- `messages` must alternate `user` / `assistant` and must start with a `user` message after the system prompt is removed.
- Do not end `messages` with an `assistant` message to "prefill" the answer. Claude Haiku 5.5 rejects it. Use Structured Outputs or the system prompt to control the format.
- Do not pass `temperature`, `top_p`, `top_k`, `reasoning_effort` or `extra_body`. Use `output_config={"effort": ...}` instead (`"low"`, `"medium"`, `"high"`). Raise effort to `"medium"` or `"high"` if output quality is not good enough, for example for drafting long legal documents.
- Check `response.stop_reason`: `"max_tokens"` means the output was cut off, and `"refusal"` means the model declined the request.
