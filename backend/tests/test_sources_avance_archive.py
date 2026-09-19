"""Tests for tracking.sources — the dynamic `source.<name>` namespace
(SourceNamespace) and its first driver, AvanceArchiveSource (`url:
avance:<archive path>`), source.attachment/source.search's replacement.
Reads straight from Db at the automaton's own (project_name, revision)
(see Automaton.set_storage_location) — never automaton.attachments'
in-memory copy, so every test here seeds real Archive rows instead of
building a MemoryArchive.

select_rows_containing()/select_rows_where()/
select_rows_in_range()/value()/column()/row_where() are the only
methods this driver implements — read-only, straight through to the
real project files every time, with no per-session copy of anything
(see SourceDriver's own docstring on why a whole-file read isn't a
source.* capability at all)."""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Source, State
from tracking.env import Env
from tracking.sources import SourceNamespace
from tracking.sources.avance_archive import AvanceArchiveSource
from tracking.sources.base import MAX_SOURCE_RESULT_CHARS, SourceContext
from tracking.sources.url import parse_source_url
from tracking.project_files import PROJECT_FILE_CACHE

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

CSV = "city,country\nParis,France\nBerlin,Germany\nparis,Texas\nLondon,UK\n"
FLIGHTS = "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-16,2026-08-16 07:12\nVY3003,2026-08-17,2026-08-17 07:05\n"


def _seed(db, files: dict[str, bytes], content_types: dict[str, str]) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, files, content_types)
    PROJECT_FILE_CACHE.forget_project(PROJECT_ID)
    return db.get_project_revision(PROJECT_ID)


def _automaton(project_id: str, revision: int, sources: list[Source] | None = None) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], general_attachments={},
        autotracking_on_ai_message=False, project_id=project_id, sources=sources,
    )
    automaton.set_storage_location(revision)
    return automaton


def _driver(
    automaton: Automaton, db, archive_path: str, name: str = "pino", session_id: int | None = None,
) -> AvanceArchiveSource:
    context = SourceContext(db=db, automaton=automaton, session_id=session_id, env=Env())
    return AvanceArchiveSource(context, name, archive_path)


def _seeded_driver(db, name: str, content: str, content_type: str = "text/csv", session_id: int | None = None):
    revision = _seed(db, {name: content.encode()}, {name: content_type})
    return _driver(_automaton(PROJECT_ID, revision), db, name, session_id=session_id), revision


def test_parse_source_url_splits_scheme_and_path_rejecting_a_missing_scheme_or_path():
    assert parse_source_url("avance:behaviour/flights.csv") == ("avance", "behaviour/flights.csv")
    for bad in ("no-colon-here", ":no-scheme", "avance:"):
        with pytest.raises(ValueError):
            parse_source_url(bad)


def test_select_rows_containing_raises_for_an_unknown_or_binary_archive(db):
    revision = _seed(db, {"logo.png": b"\x89PNG"}, {"logo.png": "image/png"})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError):
        _driver(automaton, db, "notes.txt").select_rows_containing("x")
    with pytest.raises(ValueError):
        _driver(automaton, db, "logo.png").select_rows_containing("x")


def test_select_rows_containing_returns_the_header_plus_every_case_insensitive_match_or_the_empty_string_or_every_row(db):
    driver, _ = _seeded_driver(db, "cities.csv", CSV)

    assert driver.select_rows_containing("paris") == "city,country\nParis,France\nparis,Texas\n"
    assert driver.select_rows_containing("London") == "city,country\nLondon,UK\n"
    assert driver.select_rows_containing("Tokyo") == ""
    assert driver.select_rows_containing() == CSV


def test_select_rows_containing_result_beyond_the_char_limit_is_refused_with_the_header_still_attached(db):
    rows = "\n".join(f"paris-row-{i}" for i in range(MAX_SOURCE_RESULT_CHARS))
    driver, _ = _seeded_driver(db, "big.csv", f"header\n{rows}\n")

    assert driver.select_rows_containing("paris") == "error: response too long — restrict range with search strings and/or a specific column below:\nheader"


def test_select_rows_containing_ands_several_values_together_while_one_value_returns_every_match(db):
    driver, _ = _seeded_driver(db, "flights.csv", "code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\nVY4000,2026-06-01\n")

    assert driver.select_rows_containing("VY3003", "2026-06-01") == "code,date\nVY3003,2026-06-01\n"
    assert driver.select_rows_containing("VY3003") == "code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\n"


def test_create_delete_read_update_and_save_as_do_not_exist_on_the_archive_driver(db):
    driver, _ = _seeded_driver(db, "notes.txt", "hello", content_type="text/plain")

    assert not hasattr(driver, "create")
    assert not hasattr(driver, "delete")
    assert not hasattr(driver, "read")
    assert not hasattr(driver, "update")
    assert not hasattr(driver, "save_as")
    assert "update" not in AvanceArchiveSource.SUPPORTED_METHODS
    assert "save_as" not in AvanceArchiveSource.SUPPORTED_METHODS


def test_select_rows_containing_returns_whole_rows_and_takes_no_keys_argument(db):
    driver, _ = _seeded_driver(db, "flights.csv", FLIGHTS)

    assert driver.select_rows_containing("VY3003", "2026-08-16") == (
        "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-16,2026-08-16 07:12\n"
    )
    with pytest.raises(TypeError):
        driver.select_rows_containing("VY3003", keys=["data_partenza"])


def test_select_rows_where_compares_numbers_iso_dates_and_text_returning_whole_rows(db):
    driver, _ = _seeded_driver(db, "flights.csv", FLIGHTS)

    assert driver.select_rows_where("data_partenza", "=", "2026-08-17") == (
        "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-17,2026-08-17 07:05\n"
    )
    assert driver.select_rows_where("data_partenza", ">", "2026-08-16") == (
        "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-17,2026-08-17 07:05\n"
    )
    assert driver.select_rows_where("data_partenza", ">=", "2026-08-16").count("VY3003") == 2
    assert driver.select_rows_where("data_partenza", "<", "2026-08-16") == ""
    assert driver.select_rows_where("codice_volo", "!=", "VY3003") == ""
    assert driver.select_rows_where("datetime_partenza_reale", "<", "2026-08-17") == (
        "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-16,2026-08-16 07:12\n"
    )

    numbers, _ = _seeded_driver(db, "seats.csv", "flight,free_seats\nVY1,12\nVY2,9\nVY3,100\n")
    assert numbers.select_rows_where("free_seats", ">=", "12") == "flight,free_seats\nVY1,12\nVY3,100\n"
    assert numbers.select_rows_where("flight", "=", "vy2") == "flight,free_seats\nVY2,9\n"


def test_the_filtered_reads_accept_a_bare_number_where_a_script_passes_one(db):
    numbers, _ = _seeded_driver(db, "seats.csv", "flight,free_seats\nVY1,12\nVY2,9\nVY3,100\n")
    assert numbers.select_rows_where("free_seats", "=", 12) == "flight,free_seats\nVY1,12\n"
    assert numbers.select_rows_where("free_seats", ">", 9.5, 1) == "flight,free_seats\nVY1,12\nVY3,100\n"
    assert numbers.select_rows_in_range("free_seats", 9, 12) == "flight,free_seats\nVY1,12\nVY2,9\n"
    assert numbers.select_rows_containing(100) == "flight,free_seats\nVY3,100\n"
    assert numbers.value(9, key="flight") == "VY2"
    assert numbers.column("flight", 12) == ["VY1"]


def test_select_rows_where_reports_an_unknown_column_or_operator_as_text_never_an_exception(db):
    driver, _ = _seeded_driver(db, "flights.csv", FLIGHTS)

    unknown = driver.select_rows_where("nope", "=", "x")
    assert unknown.startswith("error: unknown column(s) 'nope'")
    assert "codice_volo, data_partenza, datetime_partenza_reale" in unknown

    assert driver.select_rows_where("codice_volo", "==", "VY3003").startswith("error: unknown operator '=='")


def test_select_rows_where_ands_additional_strings_with_the_column_condition(db):
    driver, _ = _seeded_driver(
        db, "flights.csv",
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\nVY4000,2026-08-16,Rome\nVY3003,2026-08-10,Barcelona\n",
    )

    assert driver.select_rows_where("data_partenza", "=", "2026-08-16", "Barcelona") == (
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\n"
    )
    assert driver.select_rows_where("data_partenza", "=", "2026-08-16", "Barcelona", "VY3003") == (
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\n"
    )
    assert driver.select_rows_where("data_partenza", "=", "2026-08-16", "Tokyo") == ""
    assert driver.select_rows_where("data_partenza", "=", "2026-08-10", "Rome") == ""
    assert driver.select_rows_where("data_partenza", "=", "2026-08-16") == (
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\nVY4000,2026-08-16,Rome\n"
    )


def test_select_rows_in_range_includes_both_bounds_over_numbers_and_iso_dates(db):
    driver, _ = _seeded_driver(db, "flights.csv", FLIGHTS)

    assert driver.select_rows_in_range("data_partenza", "2026-08-16", "2026-08-17") == FLIGHTS
    assert driver.select_rows_in_range("data_partenza", "2026-08-17", "2026-08-31") == (
        "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-17,2026-08-17 07:05\n"
    )
    assert driver.select_rows_in_range("data_partenza", "2026-09-01", "2026-09-30") == ""
    assert driver.select_rows_in_range("nope", "1", "2").startswith("error: unknown column(s) 'nope'")

    numbers, _ = _seeded_driver(db, "seats.csv", "flight,free_seats\nVY1,12\nVY2,9\nVY3,100\n")
    assert numbers.select_rows_in_range("free_seats", "9", "12") == "flight,free_seats\nVY1,12\nVY2,9\n"


def test_select_rows_in_range_ands_additional_strings_with_the_range_condition(db):
    driver, _ = _seeded_driver(
        db, "flights.csv",
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\nVY4000,2026-08-16,Rome\nVY3003,2026-08-10,Barcelona\n",
    )

    assert driver.select_rows_in_range("data_partenza", "2026-08-15", "2026-08-20", "Barcelona") == (
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\n"
    )
    assert driver.select_rows_in_range("data_partenza", "2026-08-15", "2026-08-20", "Tokyo") == ""
    assert driver.select_rows_in_range("data_partenza", "2026-08-15", "2026-08-20", "Barcelona", "2026-08-10") == ""
    assert driver.select_rows_in_range("data_partenza", "2026-08-15", "2026-08-20") == (
        "codice_volo,data_partenza,city\nVY3003,2026-08-16,Barcelona\nVY4000,2026-08-16,Rome\n"
    )


def test_the_column_filtered_reads_keep_the_files_own_delimiter_and_bound_their_result(db):
    semicolons, _ = _seeded_driver(db, "cities.csv", "code;city\nVY1;Paris\nVY2;Rome\n")
    assert semicolons.select_rows_where("city", "=", "rome") == "code;city\nVY2;Rome\n"

    rows = "\n".join(f"row-{i},1" for i in range(MAX_SOURCE_RESULT_CHARS))
    big, _ = _seeded_driver(db, "big.csv", f"name,n\n{rows}\n")
    assert big.select_rows_where("n", "=", "1") == (
        "error: response too long — restrict range with search strings and/or a specific column below:\nname,n"
    )


def test_value_returns_the_key_cell_of_the_first_matching_row_or_the_empty_string_reporting_an_unknown_column_as_text(db):
    driver, _ = _seeded_driver(db, "flights.csv", "codice_volo,data_partenza\nVY3003,2026-08-16\nVY3003,2026-08-17\nVY4000,2026-08-16\n")

    assert driver.value("VY3003", key="data_partenza") == "2026-08-16"
    assert driver.value("VY9999", key="data_partenza") == ""
    assert driver.value("VY3003", key="nope").startswith("error: unknown column(s) 'nope'")


def test_column_returns_every_cell_of_the_matching_rows_as_a_list_the_whole_column_with_no_filter_and_nothing_for_an_unknown_column(db):
    driver, _ = _seeded_driver(db, "flights.csv", "codice_volo,data_partenza\nVY3003,2026-08-16\nVY3003,2026-08-17\nVY4000,2026-08-16\n")

    assert driver.column("codice_volo") == ["VY3003", "VY3003", "VY4000"]
    assert driver.column("data_partenza", "VY3003") == ["2026-08-16", "2026-08-17"]
    assert driver.column("data_partenza", "VY9999") == []
    assert driver.column("nope") == []
    assert "VY4000" in driver.column("codice_volo")


def test_value_column_and_row_where_read_a_numeric_cell_as_a_number_not_a_string(db):
    driver, _ = _seeded_driver(db, "seats.csv", "flight,free_seats,gate\nVY1,012,3B\nVY2,9.5,7A\n")

    assert driver.value("VY1", key="free_seats") == 12
    assert driver.value("VY2", key="free_seats") == 9.5
    assert driver.value("VY1", key="gate") == "3B"

    assert driver.column("free_seats") == [12, 9.5]
    assert driver.column("gate") == ["3B", "7A"]

    assert driver.row_where("flight", "=", "VY1") == {"flight": "VY1", "free_seats": 12, "gate": "3B"}


def test_row_where_returns_the_first_matching_row_as_a_dict_and_an_empty_dict_for_no_match_or_an_unknown_column_or_operator(db):
    driver, _ = _seeded_driver(db, "casos.csv", 'caso,nombre,texto\n1,Manuel,"- Eres Manuel.\nDos lineas."\n2,Laura,corto\n1,Otro,x\n')

    assert driver.row_where("caso", "=", 1) == {"caso": 1, "nombre": "Manuel", "texto": "- Eres Manuel.\nDos lineas."}
    assert driver.row_where("caso", ">", 1) == {"caso": 2, "nombre": "Laura", "texto": "corto"}
    assert driver.row_where("caso", "=", 1, "Otro") == {"caso": 1, "nombre": "Otro", "texto": "x"}
    assert driver.row_where("caso", "=", 9) == {}
    assert driver.row_where("nope", "=", 1) == {}
    assert driver.row_where("caso", "~", 1) == {}


def test_row_where_over_the_char_limit_is_refused_as_an_empty_dict(db):
    driver, _ = _seeded_driver(db, "big.csv", "code,text\nA," + "x" * (MAX_SOURCE_RESULT_CHARS + 1) + "\nB,short\n")

    assert driver.row_where("code", "=", "A") == {}
    assert driver.row_where("code", "=", "B") == {"code": "B", "text": "short"}


def test_every_read_works_on_csv_records_not_physical_lines_when_a_quoted_field_spans_lines(db):
    content = 'archivo,texto\ncaso_01.md,"- Eres Manuel.\nPrimera linea.\nSegunda linea."\ncaso_02.md,"- Eres Ana.\nOtra linea."\n'
    driver, _ = _seeded_driver(db, "casos.csv", content)

    assert driver.column("archivo") == ["caso_01.md", "caso_02.md"]
    assert driver.value("Ana", key="archivo") == "caso_02.md"
    assert driver.select_rows_containing("Segunda") == 'archivo,texto\ncaso_01.md,"- Eres Manuel.\nPrimera linea.\nSegunda linea."\n'
    assert driver.select_rows_where("archivo", "=", "caso_02.md") == 'archivo,texto\ncaso_02.md,"- Eres Ana.\nOtra linea."\n'


def test_column_over_the_char_limit_is_refused_as_an_empty_list(db):
    rows = "".join(f"C{i:05d},x\n" for i in range(2000))
    driver, _ = _seeded_driver(db, "big.csv", "code,other\n" + rows)

    assert driver.column("code") == []
    assert driver.column("code", "C00001") == ["C00001"]


def test_source_namespace_resolves_a_declared_name_to_its_driver_and_raises_for_an_undeclared_one(db):
    revision = _seed(db, {"notes.txt": b"note\nhello from the archive\n"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision, sources=[Source(name="pino", url="avance:notes.txt", ui_label="pino")])

    resolved = SourceNamespace(db, automaton).pino
    assert isinstance(resolved, AvanceArchiveSource)
    assert resolved.select_rows_containing("archive") == "note\nhello from the archive\n"

    with pytest.raises(ValueError):
        SourceNamespace(db, automaton).nope


def test_a_read_goes_straight_to_the_real_project_file_with_no_session_scoped_copy_of_anything(db):
    """There is no per-session cache any more — every driver, whatever
    session_id it carries, reads (and re-reads, after a change) the
    same real project file everyone else does."""
    revision = _seed(db, {"notes.txt": b"note\noriginal\n"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision)

    assert _driver(automaton, db, "notes.txt", session_id=None).select_rows_containing("original") == "note\noriginal\n"
    assert _driver(automaton, db, "notes.txt", session_id=42).select_rows_containing("original") == "note\noriginal\n"
    assert not any(name.startswith("cache/") for name in db.list_archives(PROJECT_ID, revision=revision))

    db.save_project_files(PROJECT_ID, {"notes.txt": b"note\nedited\n"}, {"notes.txt": "text/plain"})
    PROJECT_FILE_CACHE.forget_project(PROJECT_ID)
    assert _driver(automaton, db, "notes.txt", session_id=42).select_rows_containing("edited") == "note\nedited\n"
    assert _driver(automaton, db, "notes.txt", session_id=None).select_rows_containing("edited") == "note\nedited\n"
