// Purpose
// Validate the data boundary between the Python pipeline and the C++ engine.
//
// What should live here
// - Parsing of a tiny fixture quotes.csv.
// - Correct split between calls and puts.
// - Explicit behavior on malformed rows.
// - Explicit behavior when spot/rates/divs files are absent.
//
// Suggested first tests
// 1. load_reads_minimal_valid_fixture
// 2. malformed_rows_are_counted_or_reported_not_silently_ignored
// 3. missing_optional_files_fall_back_to_documented_defaults
//
// The main point is to make data ingestion failures visible, not silent.
