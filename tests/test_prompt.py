from datetime import datetime

from jarvis.prompt import briefing_request, Context, briefing_day, system_instruction


def test_sidekick_persona_is_casual_but_keeps_safety_rules():
    text = system_instruction(google_search=True, user="alex")
    assert "sidekick" in text.lower()
    assert "spoken yes" in text and "off-limits" in text and "sudo" in text


def test_memory_tools_are_explained_with_a_privacy_rule():
    text = system_instruction(google_search=False, user="alex")
    assert "remember" in text and "forget" in text
    assert "credentials" in text and "sent to Google" in text


def test_context_adds_time_profile_memories_and_activity():
    ctx = Context(now=datetime(2026, 11, 7, 9, 5), profile="Handle: sam",
                  memories="- [2026-11-06] drinks tea", activity="kitty 1h 5m, brave-browser 20m")
    text = system_instruction(google_search=True, user="alex", context=ctx)
    assert "Saturday 7 November 2026, 09:05" in text
    assert "Handle: sam" in text
    assert "drinks tea" in text
    assert "kitty 1h 5m" in text


def test_empty_context_sections_are_left_out():
    text = system_instruction(google_search=True, user="alex", context=Context(now=datetime(2026, 11, 7, 9, 5)))
    assert "About the user" not in text and "Things you remember" not in text and "Screen time" not in text


def test_briefing_request_mentions_weather_only_when_search_is_on():
    assert "weather" in briefing_request(google_search=True)
    assert "weather" not in briefing_request(google_search=False)


def test_briefing_day_rolls_over_at_4am_not_midnight():
    assert str(briefing_day(datetime(2026, 11, 8, 1, 30))) == "2026-11-07"  # still last night
    assert str(briefing_day(datetime(2026, 11, 8, 4, 0))) == "2026-11-08"


def test_time_is_given_with_timezone_and_spoken_as_local_time():
    from datetime import timezone, timedelta
    now = datetime(2026, 11, 7, 23, 35, tzinfo=timezone(timedelta(hours=1), "CET"))
    text = system_instruction(google_search=True, user="f", context=Context(now=now))
    assert "23:35 local time (CET, UTC+01:00)" in text
    assert "never convert to UTC" in text


def test_tool_examples_cannot_be_mistaken_for_real_facts():
    from jarvis.tools import DECLARATIONS
    assert "Dentist" not in str(DECLARATIONS)


def test_always_on_prompt_tells_jarvis_to_ignore_side_talk_and_that_goodbye_turns_it_off():
    text = system_instruction(google_search=True, user="f", always_on=True)
    assert "always listening" in text and "stay silent" in text
    assert "turns Jarvis off" in text
    assert "always listening" not in system_instruction(google_search=True, user="f")


def test_local_backend_prompt_has_no_google_wording_and_says_memories_stay_here():
    text = system_instruction(google_search=True, user="alex", backend="local")
    assert "Google" not in text and "stay on this machine" in text


def test_language_is_pinned_unless_auto():
    assert "Always speak French" in system_instruction(google_search=False, user="a", language="fr")
    assert "whatever language the user speaks" in system_instruction(google_search=False, user="a")


def test_personas_differ_and_unknown_ones_fail_loudly():
    friendly = system_instruction(google_search=False, user="a", persona="friendly")
    concise = system_instruction(google_search=False, user="a", persona="concise")
    assert "warm" in friendly and "terse" in concise and friendly != concise
    import pytest
    with pytest.raises(KeyError):
        system_instruction(google_search=False, user="a", persona="pirate")


def test_hyprland_hints_only_appear_on_hyprland():
    assert "hyprctl" in system_instruction(google_search=False, user="a", desktop="Hyprland")
    assert "hyprctl" not in system_instruction(google_search=False, user="a", desktop="GNOME")
    assert "Omarchy" not in system_instruction(google_search=False, user="a", desktop="Hyprland")


def test_dictation_tool_is_explained_to_the_model():
    text = system_instruction(google_search=False, user="alex")
    assert "type_text" in text and "Enter only if" in text


def test_tunisian_prompt_asks_for_the_dialect_not_standard_arabic():
    text = system_instruction(google_search=False, user="a", language="ar-tn")
    assert "Tunisian" in text and "Derja" in text and "not Modern Standard Arabic" in text
