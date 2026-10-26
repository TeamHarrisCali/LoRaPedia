from lorapedia.triggers import Parsed, parse_message


def test_dm_mode_accepts_plain_text():
    assert parse_message("what is a quasar?", True) == Parsed("question", "what is a quasar?")


def test_dm_mode_prefix_is_optional_and_stripped():
    assert parse_message("? what is a quasar", True) == Parsed("question", "what is a quasar")
    assert parse_message("!wiki quasar", True) == Parsed("question", "quasar")


def test_dm_mode_ignores_channel_messages():
    assert parse_message("?quasar", False).kind == "ignore"


def test_channel_mode_requires_prefix():
    assert parse_message("quasar", False, "channel").kind == "ignore"
    assert parse_message("?quasar", False, "channel") == Parsed("question", "quasar")
    assert parse_message("!wiki quasar", False, "channel") == Parsed("question", "quasar")


def test_channel_mode_ignores_dms():
    assert parse_message("quasar", True, "channel").kind == "ignore"


def test_both_mode():
    assert parse_message("quasar", True, "both").kind == "question"
    assert parse_message("quasar", False, "both").kind == "ignore"
    assert parse_message("?quasar", False, "both").kind == "question"


def test_word_prefix_needs_a_boundary():
    assert parse_message("!wikipedia", False, "channel").kind == "ignore"


def test_commands():
    assert parse_message("?help", True) == Parsed("command", "help")
    assert parse_message("?MORE", False, "channel") == Parsed("command", "more")
    assert parse_message("!wiki stats", False, "channel") == Parsed("command", "stats")
    assert parse_message("help", True) == Parsed("command", "help")


def test_question_starting_with_command_word_is_still_a_question():
    assert parse_message("help me understand quasars", True).kind == "question"


def test_empty_and_prefix_only_ignored():
    assert parse_message("   ", True).kind == "ignore"
    assert parse_message("?", True).kind == "ignore"
    assert parse_message("?", False, "channel").kind == "ignore"


def test_custom_prefixes_case_insensitive():
    assert parse_message("Ask: tides", False, "channel", ["ask:"]) == Parsed("question", "tides")
