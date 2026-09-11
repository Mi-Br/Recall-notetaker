from recall_notetaker.transcript import fmt_ts, render_text, speakers, stats, to_paragraphs, to_segments


def test_segments_skip_empty_and_sort(transcript_json):
    segs = to_segments(transcript_json)
    assert [s.speaker for s in segs] == ["Ada", "Ada", "Bob", "Ada"]
    assert segs[0].start_ms == 1000 and segs[0].end_ms == 1900
    assert segs[0].email == "ada@example.com"
    assert segs[2].text == "I'll send the budget Friday."


def test_paragraphs_merge_same_speaker_short_gap(transcript_json):
    paras = to_paragraphs(to_segments(transcript_json))
    assert [p.speaker for p in paras] == ["Ada", "Bob", "Ada"]
    assert paras[0].text == "Morning everyone. Let's start."
    assert paras[0].end_ms == 2900


def test_render_and_stats(transcript_json):
    paras = to_paragraphs(to_segments(transcript_json))
    text = render_text(paras)
    assert text.splitlines()[0] == "[00:01] Ada: Morning everyone. Let's start."
    assert "[01:05] Bob:" in text
    st = stats(paras)
    assert st.duration_ms == 70500
    assert st.turns == {"Ada": 2, "Bob": 1}
    assert speakers(paras) == ["Ada", "Bob"]


def test_fmt_ts():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(65_000) == "01:05"
    assert fmt_ts(3_725_000) == "1:02:05"
