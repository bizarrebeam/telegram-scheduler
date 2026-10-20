from app.parsing.basic import parse_captured_message


def test_duration_jam() -> None:
    parsed = parse_captured_message("Apply MT perusahaan X, mungkin butuh 2 jam.")
    assert parsed.estimated_duration_minutes == 120
    assert parsed.duration_source == "user"


def test_duration_menit() -> None:
    parsed = parse_captured_message("Review skripsi, 30 menit aja")
    assert parsed.estimated_duration_minutes == 30
    assert parsed.duration_source == "user"


def test_duration_absent_defaults_to_estimate() -> None:
    parsed = parse_captured_message("Beli token listrik")
    assert parsed.estimated_duration_minutes == 60
    assert parsed.duration_source == "estimate"


def test_title_is_first_line_truncated() -> None:
    parsed = parse_captured_message("Judul panjang\nbaris kedua")
    assert parsed.title == "Judul panjang baris kedua"


def test_title_capped_at_100_chars() -> None:
    parsed = parse_captured_message("x" * 250)
    assert len(parsed.title) == 100


def test_title_drops_raw_urls_but_keeps_description() -> None:
    url = "https://www.linkedin.com/jobs/search-results/?currentJobId=4454776280"
    parsed = parse_captured_message(f"daftar FE intern tiktok {url}")
    assert "http" not in parsed.title
    assert parsed.title == "daftar FE intern tiktok"
    assert url in parsed.description


def test_url_only_message_gets_readable_title() -> None:
    parsed = parse_captured_message("https://example.test/job/123")
    assert parsed.title == "Saved link"
    assert "https://example.test/job/123" in parsed.description
