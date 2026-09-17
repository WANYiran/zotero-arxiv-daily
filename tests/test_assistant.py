"""Offline checks for persistence, source selection, auth, queuing and no-LLM mode."""

from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from omegaconf import open_dict
import pytest

from zotero_arxiv_daily.assistant.api import create_app
from zotero_arxiv_daily.assistant.core import Assistant, apply_preferences, paper_numbers
from zotero_arxiv_daily.assistant.store import Store

TOKEN = "t" * 40
HEADERS = {"Authorization": "Bearer " + TOKEN}


def papers(prefix="old"):
    return [{"title": f"{prefix} paper {i}", "abstract": f"abstract {i}",
             "url": f"https://arxiv.org/abs/2609.0000{i}", "full_text": None} for i in range(1, 6)]


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "assistant.sqlite3"))


def test_immutable_digest_and_persistent_followup(store):
    first = store.put_digest("2026-09-17", papers())
    assert store.put_digest("2026-09-17", papers())["id"] == first["id"]
    assistant = Assistant(store)
    assert "old paper 3" in assistant.chat("1", "第3篇")
    later = store.put_digest("2026-09-18", papers("new"))
    # Merely importing a new digest doesn't change a conversation already in progress.
    restarted = Assistant(Store(store.path))
    assert "old paper 3" in restarted.chat("2", "它的链接")
    assert "new paper 1" in restarted.chat("3", "日报")
    assert "new paper 3" in restarted.chat("4", "第3篇")
    assert "old paper 3" in restarted.chat("5", f"日报编号 {first['id']} 第3篇")
    assert first["id"] != later["id"]


def test_rejected_delivery_release_is_fenced_and_does_not_mark_delivered(store):
    store.enqueue("test", "a message")
    first = store.claim()
    assert not store.release(first["id"], "wrong-token")
    assert store.release(first["id"], first["lease_token"])
    second = store.claim()
    assert second["id"] == first["id"]
    assert second["lease_token"] != first["lease_token"]
    assert not store.acknowledge(first["id"], first["lease_token"])
    assert not store.release(first["id"], first["lease_token"])
    assert store.acknowledge(second["id"], second["lease_token"])
    assert not store.release(second["id"], second["lease_token"])


def test_same_day_revision_keeps_original_reference(store):
    old = store.put_digest("2026-09-17", papers())
    new = store.put_digest("2026-09-17", papers("revised"))
    assert old["id"] != new["id"]
    assert store.digest(digest_id=old["id"])["papers"][0]["title"] == "old paper 1"


def test_preferences_persist_and_apply_to_future_ranking(store):
    store.put_digest("2026-09-17", papers())
    assistant = Assistant(store)
    before = store.digest()
    assistant.chat("a", "关注 AI Agent")
    assistant.chat("b", "减少 benchmark")
    assistant.chat("c", "关注 AI Agent")
    prefs = Assistant(Store(store.path)).preferences()
    assert prefs["interests"].count("AI Agent") == 1
    ranked = apply_preferences([
        SimpleNamespace(title="benchmark", abstract="", score=9.0),
        SimpleNamespace(title="AI Agent", abstract="", score=8.5),
    ], prefs)
    assert ranked[0].title == "AI Agent"
    assert before == store.digest()
    assistant.chat("d", "取消关注 AI Agent")
    assert "AI Agent" not in assistant.preferences()["interests"]


def test_comparison_passes_only_selected_evidence_with_history(store):
    store.put_digest("2026-09-17", papers())
    calls = []
    def model(question, evidence, history):
        calls.append((question, evidence, history))
        return "依据摘要，无法确认参与者人数。[2][5]"
    assistant = Assistant(store, model)
    reply = assistant.chat("1", "比较第2篇和第5篇")
    assert [p["number"] for p in calls[0][1]] == [2, 5]
    assert "https://arxiv.org/abs/2609.00002" in reply
    assert "https://arxiv.org/abs/2609.00005" in reply
    assistant.chat("2", "它们有哪些局限？")
    assert len(calls[1][2]) == 2
    assistant.chat("3", "第1篇")
    assert calls[2][2] == []


def test_missing_model_invalid_selection_and_read_only_behavior(store):
    store.put_digest("2026-09-17", papers())
    assistant = Assistant(store)
    assert "尚未配置模型" in assistant.chat("1", "第3篇")
    assert "原始摘要" in assistant.chat("2", "比较第2篇和第5篇")
    assert "有效编号" in assistant.chat("3", "第99篇")
    assert "有效编号" in assistant.chat("4", "第0篇")
    assert "只读" in assistant.chat("5", "把第3篇存入Zotero")
    assert paper_numbers("比较第二篇和第十五篇") == [2, 15]


def test_provider_error_is_not_exposed_and_raw_abstract_still_works(store):
    store.put_digest("2026-09-17", papers())
    def broken(*args):
        raise RuntimeError("secret-key-in-provider-exception")
    assistant = Assistant(store, broken)
    answer = assistant.chat("1", "第3篇")
    assert "secret-key" not in answer
    assert "暂时不可用" in answer
    assert "abstract 3" in assistant.chat("2", "第3篇原始摘要")


def test_duplicate_message_does_not_call_model_or_enqueue_twice(store):
    store.put_digest("2026-09-17", papers())
    calls = []
    assistant = Assistant(store, lambda *args: calls.append(args) or "A short answer")
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: assistant.chat("same", "第3篇", True), range(2)))
    assert replies[0] == replies[1]
    assert len(calls) == 1
    item = store.claim()
    assert item
    assert store.claim() is None
    assert store.acknowledge(item["id"], item["lease_token"])
    assert store.claim() is None
    with pytest.raises(ValueError):
        assistant.chat("same", "第4篇", True)


def test_queue_reclaim_stale_ack_and_delivered_digest_changes_context(store):
    digest = store.put_digest("2026-09-17", papers())
    assistant = Assistant(store)
    assistant.chat("a", "第3篇")
    new = store.put_digest("2026-09-18", papers("new"))
    store.enqueue("digest:" + new["id"], "digest notice")
    first = store.claim()
    with store.connection() as db:
        db.execute("UPDATE outbox SET lease_until=0")
    second = store.claim()
    assert not store.acknowledge(first["id"], first["lease_token"])
    assert store.acknowledge(second["id"], second["lease_token"])
    assert "new paper 3" in assistant.chat("b", "第3篇")
    assert store.digest(digest_id=digest["id"]) is not None


def test_api_auth_limits_validation_and_bridge_roundtrip(store, monkeypatch):
    monkeypatch.delenv("ASSISTANT_MODEL", raising=False)
    client = TestClient(create_app(store=store, token=TOKEN))
    for route in ["/digests", "/chat", "/outbox/claim", "/outbox/ack"]:
        assert client.post(route, json={}).status_code == 401
    assert client.get("/preferences").status_code == 401
    assert client.get("/health").json()["model_enabled"] is False
    assert client.post("/digests", headers=HEADERS, json={"date": "bad", "papers": []}).status_code == 422
    unsafe = papers()
    unsafe[0]["url"] = "javascript:alert(1)"
    assert client.post("/digests", headers=HEADERS, json={"date": "2026-09-17", "papers": unsafe}).status_code == 422
    response = client.post("/digests", headers=HEADERS, json={"date": "2026-09-17", "papers": papers()})
    assert response.status_code == 200
    assert response.json()["queued"]
    while item := client.post("/outbox/claim", headers=HEADERS).json()["item"]:
        assert client.post("/outbox/ack", headers=HEADERS, json={k: item[k] for k in ("id", "lease_token")}).status_code == 200
    answer = client.post("/chat", headers=HEADERS, json={"message_id": "test", "text": "第3篇", "deliver": True})
    assert "old paper 3" in answer.json()["reply"]
    item = client.post("/outbox/claim", headers=HEADERS).json()["item"]
    assert "原始摘要" in item["text"]
    assert client.post("/chat", headers=HEADERS, json={"message_id": "test", "text": "第4篇"}).status_code == 409
    assert client.post("/chat", headers=HEADERS, content=b"x" * 2_000_001).status_code == 413


def test_weak_service_token_is_rejected(store):
    with pytest.raises(ValueError):
        create_app(store=store, token="short")


def test_executor_without_llm_credentials_or_email(config, monkeypatch):
    from zotero_arxiv_daily.executor import Executor
    from tests.canned_responses import make_sample_corpus, make_sample_paper
    with open_dict(config):
        config.llm.enabled = False
        config.llm.api.key = "???"
        config.llm.api.base_url = "???"
        config.email.enabled = False
        config.executor.reranker = "local"
        config.assistant.enabled = True
        config.assistant.url = "https://assistant.example.org"
        config.assistant.token = TOKEN
    def forbidden(*args, **kwargs):
        pytest.fail("No LLM or SMTP should be used")
    published = []
    fake_publisher = SimpleNamespace(preferences=lambda: {"interests": [], "avoid": []},
        publish=lambda p, d: published.extend(p) or {"id": "test", "queued": d})
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", forbidden)
    monkeypatch.setattr("zotero_arxiv_daily.executor.send_email", forbidden)
    monkeypatch.setattr("zotero_arxiv_daily.executor.Publisher", lambda *args: fake_publisher)
    executor = Executor(config)
    monkeypatch.setattr(executor, "fetch_zotero_corpus", make_sample_corpus)
    paper = make_sample_paper()
    monkeypatch.setattr(paper, "generate_tldr", forbidden)
    monkeypatch.setattr(paper, "generate_affiliations", forbidden)
    executor.retrievers = {"arxiv": SimpleNamespace(retrieve_papers=lambda: [paper])}
    executor.reranker = SimpleNamespace(rerank=lambda p, c: p)
    executor.run()
    assert published == [paper]


def test_abstract_only_retriever_never_downloads_fulltext(config, monkeypatch):
    from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever
    with open_dict(config):
        config.source.arxiv.fetch_full_text = False
    def forbidden(*args):
        pytest.fail("Abstract-only mode must not download/extract full text")
    for name in ["tar", "html", "pdf"]:
        monkeypatch.setattr(f"zotero_arxiv_daily.retriever.arxiv_retriever.extract_text_from_{name}", forbidden)
    raw = SimpleNamespace(title="Test", authors=[], summary="Abstract", pdf_url=None, entry_id="https://arxiv.org/abs/2609.00001")
    result = ArxivRetriever(config).convert_to_paper(raw)
    assert result.abstract == "Abstract"
    assert result.full_text is None
