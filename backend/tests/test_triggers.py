from lorapedia.triggers import Parsed, parse_message


def test_dm_mode_accepts_plain_text():
    assert parse_message("what is a quasar?", True) == Parsed("question", "what is a quasar?")


def test_dm_mode_prefix_is_optional_and_stripped():
    assert parse_message("? what is a quasar", True) == Parsed("question", "what is a quasar")
    assert parse_message("!wiki quasar", True) == Parsed("question", "quasar")


def test_a_public_message_is_never_a_question_or_a_command_whatever_it_says():
    for text in ("?quasar", "!wiki quasar", "quasar", "?help", "?MORE", "!wiki stats", "?ping", "Ask: tides", "?"):
        assert parse_message(text, False).kind == "ignore", text
        assert parse_message(text, False, ["ask:", "?"]).kind == "ignore", text  # not even with every prefix configured


def test_the_prefix_is_optional_in_a_direct_message():
    assert parse_message("quasar", True) == Parsed("question", "quasar")
    assert parse_message("?quasar", True) == Parsed("question", "quasar")
    assert parse_message("!wiki quasar", True) == Parsed("question", "quasar")


def test_word_prefix_needs_a_boundary():
    assert parse_message("!wikipedia", True) == Parsed("question", "!wikipedia")  # not the prefix "!wiki": the whole text is the question


def test_commands():
    assert parse_message("?help", True) == Parsed("command", "help")
    assert parse_message("?MORE", True) == Parsed("command", "more")
    assert parse_message("!wiki stats", True) == Parsed("command", "stats")
    assert parse_message("help", True) == Parsed("command", "help")


def test_question_starting_with_command_word_is_still_a_question():
    assert parse_message("help me understand quasars", True).kind == "question"


def test_empty_and_prefix_only_ignored():
    assert parse_message("   ", True).kind == "ignore"
    assert parse_message("?", True).kind == "ignore"


def test_custom_prefixes_case_insensitive():
    assert parse_message("Ask: tides", True, ["ask:"]) == Parsed("question", "tides")
