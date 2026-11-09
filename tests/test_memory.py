import stat
from datetime import date

import pytest

from jarvis.memory import MAX_FACT_CHARS, MAX_FACTS, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "jarvis" / "memory.md", today=lambda: date(2026, 11, 7))


def test_remember_then_recall_round_trips_with_date(store):
    assert store.remember("prefers dark roast coffee")["status"] == "remembered"
    assert store.recall()["facts"] == ["[2026-11-07] prefers dark roast coffee"]


def test_memory_file_is_private_and_human_editable(store):
    store.remember("likes lofi while hacking")
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600
    assert "- [2026-11-07] likes lofi while hacking" in store.path.read_text()


def test_recall_filters_by_every_query_word_case_insensitively(store):
    store.remember("The dentist appointment is in December")
    store.remember("favourite editor is neovim")
    assert store.recall("dentist DECEMBER")["facts"] == ["[2026-11-07] The dentist appointment is in December"]
    assert store.recall("nothing matches")["facts"] == []


def test_duplicate_facts_are_not_stored_twice(store):
    store.remember("uses Brave")
    assert store.remember("  uses   brave ")["status"] == "already known"
    assert len(store.facts()) == 1


def test_forget_removes_matching_facts_and_reports_them(store):
    store.remember("gym on Mondays")
    store.remember("gym on Thursdays")
    store.remember("drinks tea")
    result = store.forget("gym")
    assert result["forgot"] == ["[2026-11-07] gym on Mondays", "[2026-11-07] gym on Thursdays"]
    assert store.facts() == ["[2026-11-07] drinks tea"]


def test_forget_without_a_match_is_an_error(store):
    assert "error" in store.forget("unicorns")


def test_forget_refuses_an_empty_query_that_would_wipe_everything(store):
    store.remember("drinks tea")
    assert "error" in store.forget("   ")
    assert store.facts()


def test_secrets_are_refused_not_stored(store):
    result = store.remember("my github token is ghp_" + "a" * 40)
    assert "error" in result
    assert store.facts() == []


@pytest.mark.parametrize("fact", ["", "   ", "x" * (MAX_FACT_CHARS + 1)])
def test_empty_or_oversized_facts_are_rejected(store, fact):
    assert "error" in store.remember(fact)


def test_fact_newlines_are_flattened_so_one_fact_stays_one_line(store):
    store.remember("line one\n- [2020-01-01] injected")
    assert store.facts() == ["[2026-11-07] line one - [2020-01-01] injected"]


def test_oldest_facts_drop_off_past_the_cap(store):
    for i in range(MAX_FACTS + 3):
        store.remember(f"fact number {i}")
    facts = store.facts()
    assert len(facts) == MAX_FACTS
    assert facts[0].endswith("fact number 3")


def test_as_prompt_lists_recent_facts_or_nothing(store):
    assert store.as_prompt() == ""
    store.remember("drinks tea")
    assert store.as_prompt() == "- [2026-11-07] drinks tea"


def test_missing_file_means_no_facts(tmp_path):
    assert MemoryStore(tmp_path / "nope.md").facts() == []


def test_hand_edited_lines_without_dash_are_ignored(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("# My notes\n- real fact\nrandom text\n")
    assert store.facts() == ["real fact"]


@pytest.mark.parametrize("fact", [
    "my wifi password is correct horse battery staple",
    "the VPN pin is 4821",
    "backup codes for github are in the drawer",
    "card number 4111111111111111",
])
def test_spoken_secrets_without_key_value_syntax_are_refused(store, fact):
    assert "error" in store.remember(fact)
    assert store.facts() == []


@pytest.mark.parametrize("fact", ["The dentist appointment is on 2026-12-12", "pinned the repo", "spinach for dinner"])
def test_ordinary_facts_with_dates_or_lookalike_words_are_kept(store, fact):
    assert store.remember(fact)["status"] == "remembered"


def test_hand_edited_secrets_are_scrubbed_before_reaching_the_prompt(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("- api_key=sk-proj-" + "a" * 30 + "\n")
    assert "sk-proj" not in store.as_prompt()


@pytest.mark.parametrize("query", ["a", "th"])
def test_forget_refuses_tiny_queries(store, query):
    store.remember("drinks tea")
    assert "error" in store.forget(query)
    assert store.facts()


def test_forget_refuses_to_mass_delete_and_lists_matches(store):
    for day in ("Mon", "Tue", "Wed", "Thu"):
        store.remember(f"gym on {day}")
    result = store.forget("gym")
    assert "error" in result and len(result["matches"]) == 4
    assert len(store.facts()) == 4
