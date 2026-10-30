"""The architecture rules, enforced. Offline.

1. app/llm/client.py is the only file that talks to a model: no other file imports a model SDK or names a model API host, and the only
   other HTTP clients are the allowlisted news fetcher and its wiring.
2. The numbers routes never call a model: every route outside MODEL_ROUTES is called with a model that fails the test if it is asked.
3. Inventory, Recommender and Daily go through one grounding check with one retry; Router and Market keep their own code checks.
"""

import ast
from pathlib import Path

import pytest

from app.agents import base, daily, inventory, market, recommender, router
from app.api.main import create_app
from auth import AuthedClient
from conftest import OfflineModel

REPO = Path(__file__).resolve().parent.parent
MODEL_CLIENT = "app/llm/client.py"
# scripts/api_check.py is a one-off probe of the Meta API's tool calling, run by hand; nothing in the app imports or runs it.
SDK_EXCEPTIONS = {MODEL_CLIENT, "scripts/api_check.py"}
MODEL_SDKS = {"openai", "anthropic", "google.generativeai", "google.genai", "mistralai", "cohere", "ollama", "litellm", "groq", "together",
              "langchain", "langchain_openai", "llama_index"}
MODEL_HOSTS = ("api.meta.ai", "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com", "api.mistral.ai",
               "api.groq.com", "api.together.xyz", "/chat/completions")
HTTP_LIBS = {"httpx", "requests", "urllib.request", "aiohttp", "http.client"}
# news fetching (allowlisted hosts only) and the scripts that drive it; none of them may talk to a model
HTTP_ALLOWED = {"app/watch/feeds.py", "app/watch/scan.py", "app/api/deps_ai.py", "scripts/capture_demo_news.py"}


def python_files():
    for folder in ("app", "scripts", "data"):
        for path in sorted((REPO / folder).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path.relative_to(REPO).as_posix(), path


def imported(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def under(name: str, roots: set[str]) -> bool:
    return any(name == r or name.startswith(r + ".") for r in roots)


def test_only_the_model_client_imports_a_model_sdk():
    offenders = {rel: sorted(n for n in imported(p) if under(n, MODEL_SDKS)) for rel, p in python_files() if rel not in SDK_EXCEPTIONS}
    assert not {k: v for k, v in offenders.items() if v}


def test_only_the_model_client_names_a_model_api_host():
    found = {rel: [h for h in MODEL_HOSTS if h in p.read_text(encoding="utf-8")] for rel, p in python_files() if rel not in SDK_EXCEPTIONS}
    assert not {k: v for k, v in found.items() if v}


def test_http_clients_appear_only_in_the_news_fetcher():
    users = {rel for rel, p in python_files() if any(under(n, HTTP_LIBS) for n in imported(p)) and rel != MODEL_CLIENT}
    assert users <= HTTP_ALLOWED, users - HTTP_ALLOWED


def test_the_model_client_is_where_the_sdk_is():
    assert "openai" in imported(REPO / MODEL_CLIENT)


# ---- numbers routes never call a model ---------------------------------------------------------

MODEL_ROUTES = {("post", "/api/ask"), ("post", "/api/daily/run"), ("post", "/api/alerts/{alert_id}/plan"), ("post", "/api/watch/scan")}


class Sentinel:
    """A model that must never be asked."""
    is_mock = False
    calls = 0

    def complete_json(self, **kw):
        Sentinel.calls += 1
        raise AssertionError("a numbers route asked a model")

    complete_text = stream_text = complete_json


@pytest.fixture
def numbers_app(catalog_dir, tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / "t.sqlite", as_of="2026-10-26", llm=OfflineModel())
    c = AuthedClient(app)
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        upload = c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).json()
    alert = c.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    c.post(f"/api/alerts/{alert}/help")
    recs = c.post(f"/api/alerts/{alert}/plan").json()["recommendations"]           # the plan route may ask a model; it is not under test
    from app.llm import client as llm_client                                        # from here on, building a model client fails too
    monkeypatch.setattr(llm_client.LLMClient, "__init__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model client built")))
    app.state.llm = Sentinel()
    Sentinel.calls = 0
    return c, {"alert_id": alert, "rec_id": recs[0]["id"], "upload_id": upload["upload_id"], "notification_id": 1}


def test_every_route_outside_the_model_routes_runs_without_a_model(numbers_app, catalog_dir):
    c, ids = numbers_app
    routes = {(m, p) for p, ops in c.app.openapi()["paths"].items() for m in ops}
    assert MODEL_ROUTES <= routes
    for method, path in sorted(routes - MODEL_ROUTES):
        if path in ("/api/logout",):
            continue                                                                # would end the session the rest of the walk needs
        url = path.format(**ids)
        kw = {}
        if path == "/api/upload":
            kw["files"] = {"file": ("d.csv", (catalog_dir / "upload_demo_200.csv").read_bytes(), "text/csv")}
        elif path in ("/api/signup", "/api/login"):
            kw["json"] = {}
        r = getattr(c, method)(url, **kw)
        assert r.status_code < 500, (method, path, r.status_code, r.text[:200])
    assert Sentinel.calls == 0


# ---- one grounding check with one retry --------------------------------------------------------

def test_inventory_recommender_and_daily_use_the_shared_grounding_check():
    for module in (inventory, recommender, daily):
        assert module.run_grounded is base.run_grounded, module.__name__


def test_router_and_market_keep_their_own_code_checks():
    import inspect
    assert "AGENT_FOR_INTENT[d.intent]" in inspect.getsource(router.route) and "known_skus" in inspect.getsource(router.route)
    src = inspect.getsource(market.read_article)
    assert "_enforce(" in src and "wrap_untrusted(" in src
