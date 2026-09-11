from recall_notetaker.state import BotState


def test_state_roundtrip(tmp_path):
    p = tmp_path / "s" / "bots.json"
    st = BotState(p)
    st.record("b1", event_id="ev1", status="scheduled")
    st.record("b2", status="done", notes_path="notes/x.md")
    st2 = BotState(p)
    assert st2.by_event("ev1") == "b1"
    assert st2.by_event("nope") is None
    assert st2.pending() == ["b1"]
    assert "created_at" in st2.bots["b1"]
