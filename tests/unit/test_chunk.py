import pytest

from bookstack_mcp.content.chunk import Chunk, InvalidChunkRequest, chunk


def _join(text: str, max_chars: int) -> list[Chunk]:
    chunks = [chunk(text, 0, max_chars)]
    while chunks[-1].next_offset is not None:
        chunks.append(chunk(text, chunks[-1].next_offset, max_chars))
    return chunks


def test_short_text_in_one_chunk() -> None:
    assert chunk("hello") == Chunk("hello", 0, 5, 5, None, False)


def test_empty_text() -> None:
    assert chunk("") == Chunk("", 0, 0, 0, None, False)


def test_exact_fit_is_not_truncated() -> None:
    result = chunk("abcd", max_chars=4)
    assert result.next_offset is None
    assert not result.truncated


def test_cut_at_max_chars_without_blank_line() -> None:
    result = chunk("a" * 25, max_chars=10)
    assert result == Chunk("a" * 10, 0, 10, 25, 10, True)


def test_cut_after_blank_line_in_final_ten_percent() -> None:
    text = "a" * 95 + "\n\n" + "b" * 50
    result = chunk(text, max_chars=100)
    assert result.content == "a" * 95 + "\n\n"
    assert result.next_offset == 97
    assert result.returned_chars == 97


def test_last_blank_line_wins() -> None:
    text = "a" * 91 + "\n\n" + "b" + "\n\n" + "c" * 50
    assert chunk(text, max_chars=100).next_offset == 96


def test_blank_line_before_final_ten_percent_is_ignored() -> None:
    text = "a" * 50 + "\n\n" + "b" * 100
    assert chunk(text, max_chars=100).next_offset == 100


def test_blank_line_straddling_the_window_end_is_ignored() -> None:
    text = "a" * 99 + "\n\n" + "b" * 10
    assert chunk(text, max_chars=100).next_offset == 100


def test_offset_starts_mid_text() -> None:
    result = chunk("0123456789", offset=4, max_chars=3)
    assert result == Chunk("456", 4, 3, 10, 7, True)
    assert chunk("0123456789", offset=10) == Chunk("", 10, 0, 10, None, False)


@pytest.mark.parametrize("max_chars", [1, 7, 100, 1000])
def test_chunks_join_back_to_full_text(max_chars: int) -> None:
    text = "\n\n".join(f"Paragraph {i} " + "word " * (i % 13) for i in range(80))
    chunks = _join(text, max_chars)
    assert "".join(c.content for c in chunks) == text
    assert all(0 < c.returned_chars <= max_chars for c in chunks)
    assert all(c.total_chars == len(text) for c in chunks)
    assert chunks[-1].next_offset is None
    assert not chunks[-1].truncated
    assert all(c.truncated for c in chunks[:-1])


@pytest.mark.parametrize(("offset", "max_chars"), [(-1, 10), (4, 10), (0, 0)])
def test_invalid_requests(offset: int, max_chars: int) -> None:
    with pytest.raises(InvalidChunkRequest):
        chunk("abc", offset, max_chars)
