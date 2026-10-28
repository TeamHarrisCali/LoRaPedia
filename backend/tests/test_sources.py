from lorapedia.retrieval.base import Chunk
from lorapedia.sources import order_sources


def test_the_article_the_answer_is_written_from_comes_first_not_the_best_match_for_the_question():
    chunks = [Chunk("Île-de-France", "Île-de-France is a region around the capital of France, whose centre is the city of Paris."),
              Chunk("France", "France is a country in Western Europe. Its capital is Paris."),
              Chunk("Paris", "Paris is the capital and most populous city of France.")]
    assert order_sources("Paris", chunks)[0] == "Paris"  # the answer names the article


def test_ties_keep_retrieval_order_and_titles_are_unique():
    chunks = [Chunk("Quasar", "quasar first chunk"), Chunk("Quasar", "quasar second chunk"), Chunk("Black hole", "a black hole")]
    assert order_sources("", chunks) == ["Quasar", "Black hole"]


def test_an_article_that_supports_more_of_the_answer_beats_one_that_only_ranked_higher():
    chunks = [Chunk("Hiking", "People go hiking in the mountains."),
              Chunk("Mount Everest", "Everest is the highest mountain above sea level, 8,849 metres.")]
    assert order_sources("Everest is 8,849 metres above sea level", chunks)[0] == "Mount Everest"


def test_nothing_retrieved_is_nothing_listed():
    assert order_sources("anything", []) == []
