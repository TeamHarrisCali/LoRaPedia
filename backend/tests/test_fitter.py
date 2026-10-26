from lorapedia import fitter
from lorapedia.fitter import byte_len, split_parts, truncate_bytes

LONG = ("Quasars are extremely luminous active galactic nuclei powered by supermassive black holes. " * 12).strip()


def test_byte_len_counts_bytes_not_chars():
    assert byte_len("abc") == 3
    assert byte_len("café") == 5  # e-acute is 2 bytes
    assert byte_len("日本") == 6  # CJK is 3 bytes each
    assert byte_len("\U0001f600") == 4  # emoji is 4 bytes


def test_truncate_noop_when_it_fits():
    assert truncate_bytes("short text", 200) == "short text"


def test_truncate_word_boundary_and_ellipsis():
    out = truncate_bytes("the quick brown fox jumps over the lazy dog", 20)
    assert out.endswith("...")
    assert byte_len(out) <= 20
    assert out == "the quick brown..."  # did not cut "fox" in half


def test_truncate_never_splits_multibyte_characters():
    text = "é" * 100  # 200 bytes
    for limit in range(4, 60):
        out = truncate_bytes(text, limit)
        assert byte_len(out) <= limit
        out.encode("utf-8")  # valid UTF-8, no half characters
        assert "�" not in out


def test_truncate_tiny_limits():
    assert truncate_bytes("hello world", 0) == ""
    assert byte_len(truncate_bytes("hello world", 2)) <= 2


def test_truncate_emoji_boundary():
    out = truncate_bytes("ok \U0001f600\U0001f600\U0001f600\U0001f600", 9)
    assert byte_len(out) <= 9


def test_clean_text_strips_markdown_and_folds_punctuation():
    assert fitter.clean_text("**Bold** `code` it’s “quoted” — done…") == (
        'Bold code it\'s "quoted" - done...'
    )


def test_split_returns_single_message_unchanged_when_it_fits():
    assert split_parts("A short answer.", 200, 3) == ["A short answer."]


def test_split_numbers_parts_and_respects_limit():
    parts = split_parts(LONG, 200, 3)
    assert 1 < len(parts) <= 3
    for i, p in enumerate(parts, 1):
        assert p.startswith(f"({i}/{len(parts)}) ")
        assert byte_len(p) <= 200


def test_split_caps_parts_and_truncates_overflow():
    parts = split_parts(LONG * 5, 200, 3)
    assert len(parts) == 3
    assert parts[-1].endswith("...")
    assert all(byte_len(p) <= 200 for p in parts)


def test_split_max_parts_one_truncates():
    parts = split_parts(LONG, 200, 1)
    assert len(parts) == 1 and byte_len(parts[0]) <= 200 and parts[0].endswith("...")


def test_split_multibyte_text_stays_within_limit():
    text = " ".join(["naïve café 日本語 \U0001f600"] * 40)
    for limit in (50, 100, 200, 233):
        for max_parts in (1, 2, 3, 9):
            for p in split_parts(text, limit, max_parts):
                assert byte_len(p) <= limit, (limit, max_parts, p)


def test_split_hard_splits_a_word_longer_than_a_part():
    parts = split_parts("x" * 500, 100, 9)
    assert len(parts) > 1 and all(byte_len(p) <= 100 for p in parts)


def test_split_handles_characters_wider_than_the_room():
    # limit so small that a 4-byte emoji cannot fit in a part: must not loop forever
    parts = split_parts("\U0001f600" * 30, 50, 9)
    assert all(byte_len(p) <= 50 for p in parts)


def test_total_budget():
    assert fitter.total_budget(200, 1) == 200
    assert fitter.total_budget(200, 3) == 3 * (200 - len("(3/3) "))


def test_append_tag_only_when_it_fits():
    assert fitter.append_tag(["abc"], "[wp:X]", 50) == ["abc [wp:X]"]
    full = "x" * 195
    assert fitter.append_tag([full], "[wp:Photosynthesis]", 200) == [full]


def test_fit_answer_always_within_limit():
    for limit in (50, 120, 200, 233):
        for max_parts in (1, 3):
            for p in fitter.fit_answer("**" + LONG + "** — café", limit, max_parts):
                assert byte_len(p) <= limit
