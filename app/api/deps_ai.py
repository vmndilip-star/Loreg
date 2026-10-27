"""Wiring for the routes that may use an agent (Loreg Watch, ask, daily). The numbers routes never import this."""

import httpx
from fastapi import Request

from app.llm.client import LLMClient


def get_llm(request: Request) -> LLMClient:
    """The app's model client (set by create_app or built from LLM_BACKEND). The mock backend needs no network."""
    state = request.app.state
    if state.llm is None:
        state.llm = LLMClient()
    return state.llm


def get_http(request: Request):
    factory = request.app.state.http_factory
    client = factory() if factory else httpx.Client()
    try:
        yield client
    finally:
        client.close()
