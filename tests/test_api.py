from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn
from fastapi.testclient import TestClient

from mortal.api.app import create_app
from mortal.config import Settings
from mortal.engine.store import LifeStore

BODY = {
    "person": {"name": "Ayo", "age": 34, "occupation": "clerk", "description": "quiet, loves maps",
               "traits": {"openness": 8, "extraversion": 3}, "wage_per_hour": 14},
    "npcs": [{"name": "Mara", "role": "sister", "note": "worries"}],
    "god": {"mood": "mischievous", "objectives": ["find real friendship"]},
    "llm": {"provider": "stub"},
    "seed": 11,
}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, persist=False)) as c:
        yield c


def make_life(client) -> str:
    res = client.post("/api/lives", json=BODY)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_health_and_meta(client):
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert meta["providers"][0]["id"] == "stub" and "world" in meta["divine_kinds"]
    assert meta["world_sizes"]["medium"] == {"width": 56, "height": 20}
    assert "benevolent" in meta["moods"]


def test_index_and_static_assets_are_served(client):
    html = client.get("/")
    assert html.status_code == 200 and "MORTAL" in html.text
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/app.css").status_code == 200


def test_create_and_describe_a_life(client):
    life_id = make_life(client)
    frame = client.get(f"/api/lives/{life_id}").json()
    assert frame["person"]["name"] == "Ayo" and frame["person"]["alive"]
    assert frame["god"] == {"mood": "mischievous", "objectives": ["find real friendship"]}
    assert [p["name"] for p in frame["people"]] == ["Mara"]
    me = [e for e in frame["entities"] if e["kind"] == "me"]
    assert len(me) == 1 and frame["terrain"][me[0]["y"]][me[0]["x"]] != " "  # the person stands on the map
    assert frame["world"]["name"] == "the town" and frame["world"]["source"] == "builtin"
    assert {e["kind"] for e in frame["entities"]} <= {"me", "npc", "dead"}
    assert len({len(r) for r in frame["terrain"]}) == 1 and "." in frame["styles"]
    assert any(item["name"] == "river" and item["style"] == "water" for item in frame["legend"])
    assert frame["runtime"]["running"] is False and frame["runtime"]["provider"] == "offline"
    assert [x["id"] for x in client.get("/api/lives").json()] == [life_id]


def test_create_validates_input(client):
    bad = {**BODY, "person": {**BODY["person"], "name": ""}}
    assert client.post("/api/lives", json=bad).status_code == 422
    bad = {**BODY, "person": {**BODY["person"], "traits": {"openness": 11}}}
    assert client.post("/api/lives", json=bad).status_code == 422
    bad = {**BODY, "llm": {"provider": "skynet"}}
    assert client.post("/api/lives", json=bad).status_code == 422


def test_unknown_life_is_404(client):
    for method, path in [("get", "/api/lives/deadbeef"), ("post", "/api/lives/deadbeef/play"),
                         ("delete", "/api/lives/deadbeef"), ("get", "/api/lives/deadbeef/stream")]:
        assert getattr(client, method)(path).status_code == 404


def test_step_advances_one_tick(client):
    life_id = make_life(client)
    t0 = client.get(f"/api/lives/{life_id}").json()["clock"]["t"]
    client.post(f"/api/lives/{life_id}/step")
    f = client.get(f"/api/lives/{life_id}").json()
    assert f["clock"]["t"] == t0 + 10 and f["log"]


def test_play_runs_in_real_time_and_pause_stops_it(client):
    life_id = make_life(client)
    assert client.put(f"/api/lives/{life_id}/speed", json={"speed": 60}).json()["speed"] == 60
    assert client.post(f"/api/lives/{life_id}/play").json()["running"] is True
    assert wait_for(lambda: client.get(f"/api/lives/{life_id}").json()["clock"]["t"] > 360 + 100)
    client.post(f"/api/lives/{life_id}/pause")
    time.sleep(0.1)
    t = client.get(f"/api/lives/{life_id}").json()["clock"]["t"]
    time.sleep(0.2)
    assert client.get(f"/api/lives/{life_id}").json()["clock"]["t"] == t


def test_speed_is_bounded(client):
    life_id = make_life(client)
    assert client.put(f"/api/lives/{life_id}/speed", json={"speed": 0}).status_code == 422
    assert client.put(f"/api/lives/{life_id}/speed", json={"speed": 1000}).status_code == 422


def test_god_is_heard_immediately_even_while_paused(client):
    life_id = make_life(client)
    res = client.post(f"/api/lives/{life_id}/god", json={"kind": "event", "text": "Ayo finds a gold coin in the park"})
    assert res.status_code == 202
    f = client.get(f"/api/lives/{life_id}").json()
    kinds = [e["kind"] for e in f["log"]]
    assert "divine" in kinds and "demigod" in kinds and f["runtime"]["running"] is False
    divine = next(e for e in f["log"] if e["kind"] == "divine")
    assert divine["text"] == "[event] Ayo finds a gold coin in the park"


def test_god_rejects_blank_or_oversized_words(client):
    life_id = make_life(client)
    for text in ("", "   ", "x" * 601):
        assert client.post(f"/api/lives/{life_id}/god", json={"kind": "event", "text": text}).status_code == 422
    assert client.post(f"/api/lives/{life_id}/god", json={"kind": "prayer", "text": "hi"}).status_code == 422


def test_demigod_mood_and_objectives_can_be_changed(client):
    life_id = make_life(client)
    res = client.put(f"/api/lives/{life_id}/demigod", json={"mood": " wrathful ", "objectives": ["  test patience ", "", "seek truth"]})
    assert res.json() == {"mood": "wrathful", "objectives": ["test patience", "seek truth"]}
    f = client.get(f"/api/lives/{life_id}").json()
    assert f["god"]["mood"] == "wrathful"
    assert any("wrathful" in e["text"] for e in f["log"] if e["kind"] == "system")
    assert client.put(f"/api/lives/{life_id}/demigod", json={"mood": "", "objectives": []}).status_code == 422
    assert client.put(f"/api/lives/{life_id}/demigod", json={"mood": "x", "objectives": ["y" * 161]}).status_code == 422


def test_switching_backend_is_recorded(client):
    life_id = make_life(client)
    res = client.put(f"/api/lives/{life_id}/llm", json={"provider": "openai", "model": "llama3.1"})
    assert res.json()["provider"].startswith("openai:llama3.1")
    assert client.get(f"/api/lives/{life_id}").json()["llm"] == {"provider": "openai", "model": "llama3.1"}


def test_unreachable_local_model_degrades_gracefully(settings):
    cfg = Settings.from_env({"MORTAL_OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "MORTAL_LLM_TIMEOUT": "1"})
    with TestClient(create_app(cfg, persist=False)) as c:
        life_id = c.post("/api/lives", json={**BODY, "llm": {"provider": "openai"}}).json()["id"]
        c.post(f"/api/lives/{life_id}/step")  # the dawn decision hits a closed port
        f = c.get(f"/api/lives/{life_id}").json()
        assert f["runtime"]["last_source"] == "rule" and len(f["log"]) > 3
        assert any("falls silent" in e["text"] for e in f["log"])


def test_export_and_delete(client):
    life_id = make_life(client)
    exported = client.get(f"/api/lives/{life_id}/export")
    assert exported.status_code == 200 and exported.json()["person"]["name"] == "Ayo"
    assert "attachment" in exported.headers["content-disposition"]
    assert client.delete(f"/api/lives/{life_id}").status_code == 204
    assert client.get(f"/api/lives/{life_id}").status_code == 404


# ----------------------------------------------------------------------------- persistence
def test_lives_survive_a_restart(settings):
    with TestClient(create_app(settings)) as c:
        life_id = c.post("/api/lives", json=BODY).json()["id"]
        for _ in range(30):
            c.post(f"/api/lives/{life_id}/step")
        c.put(f"/api/lives/{life_id}/demigod", json={"mood": "melancholic", "objectives": ["be still"]})
        before = c.get(f"/api/lives/{life_id}").json()
    with TestClient(create_app(settings)) as c:
        after = c.get(f"/api/lives/{life_id}").json()
        assert after["clock"] == before["clock"] and after["entities"] == before["entities"]
        assert after["terrain"] == before["terrain"] and after["world"] == before["world"]
        assert after["person"]["needs"] == before["person"]["needs"]
        assert after["god"] == {"mood": "melancholic", "objectives": ["be still"]}
        assert after["runtime"]["running"] is False
        assert [e["seq"] for e in after["log"]] == [e["seq"] for e in before["log"]]
        c.post(f"/api/lives/{life_id}/step")  # and the story carries on
        assert c.get(f"/api/lives/{life_id}").json()["clock"]["t"] == before["clock"]["t"] + 10


def test_store_refuses_path_tricks(tmp_path):
    store = LifeStore(tmp_path)
    for bad in ("../x", "abc", "zzzzzzzz", "AAAAAAAA", ""):
        with pytest.raises(ValueError):
            store.delete(bad)


def test_store_skips_corrupt_files(tmp_path):
    (tmp_path / "deadbeef.json").write_text("{not json", encoding="utf-8")
    assert LifeStore(tmp_path).load_all() == []


# ----------------------------------------------------------------------------- live stream
async def test_subscriber_cursor_delivers_each_log_line_exactly_once(settings):
    from mortal.core.models import LifeSpec
    from mortal.engine.registry import LifeRegistry

    registry = LifeRegistry(settings, persist=False)
    runner = await registry.create(LifeSpec.model_validate(BODY))
    assert runner.frame_for(runner.subscribe(history=0))["log"] == []  # no replay requested
    sub = runner.subscribe(history=10)
    first = runner.frame_for(sub)
    assert [e["seq"] for e in first["log"]] == [1]  # replays the opening line
    assert runner.frame_for(sub)["log"] == []  # nothing new
    runner.state.add_log("event", "something happened")
    again = runner.frame_for(sub)
    assert [e["text"] for e in again["log"]] == ["something happened"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sse_stream_pushes_frames_over_real_http(settings):
    app = create_app(settings, persist=False)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        assert wait_for(lambda: server.started, 10)
        base = f"http://127.0.0.1:{port}"

        def call(method, path, body=None):
            req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read() or "null")

        life_id = call("POST", "/api/lives", BODY)["id"]
        call("PUT", f"/api/lives/{life_id}/speed", {"speed": 30})
        resp = urllib.request.urlopen(base + f"/api/lives/{life_id}/stream", timeout=5)
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        frames = []

        def read_frame():
            line = resp.readline().decode()
            while line and not line.startswith("data: "):
                line = resp.readline().decode()
            return json.loads(line[6:])

        frames.append(read_frame())  # the hello frame, immediately
        call("POST", f"/api/lives/{life_id}/play")
        for _ in range(6):
            frames.append(read_frame())
        call("POST", f"/api/lives/{life_id}/pause")
        resp.close()
        times = [f["clock"]["t"] for f in frames]
        assert times == sorted(times) and times[-1] > times[0]
        seqs = [e["seq"] for f in frames for e in f["log"]]
        assert seqs == sorted(set(seqs))  # no duplicates, no reordering
    finally:
        server.should_exit = True
        thread.join(timeout=10)
