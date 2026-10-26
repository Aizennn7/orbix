"""Tests for the CSV upload caps on /api/import and /api/candles/append.

The import cap used to be a hard-coded 1 GiB. At roughly 75 bytes per one-minute
candle that is ~14 million rows, or about 26 years of continuous data - orders of
magnitude past the largest window this app reads (backtest 2500, model training
5000, stored tail 10000). A cap that large is not a limit, it is an invitation to
fill the disk.

Both caps are now environment-driven, and a byte cap alone is not sufficient: the
stream is spooled to disk before it is parsed and the parsed candles are then
written to the store, so the same bytes cost roughly twice over. Without a
free-space check an oversized upload fails *mid-write*, leaving a truncated
temp file and a possibly corrupted store behind. 507 refuses up front instead.

These paths had no test coverage when they shipped.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import orbix.api as api


class _FakeRequest:
    """Minimal stand-in for a Starlette Request: only .stream() is used."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def run_upload(chunks, max_bytes, free_bytes=None, processor=None):
    """Drive _with_streamed_csv against a temp data dir.

    Returns (result, paths handed to the default processor), or raises
    HTTPException exactly as the endpoint would.
    """
    calls: list[Path] = []

    def default_processor(path: Path) -> dict:
        calls.append(path)
        return {"count": 1, "calls": len(calls)}

    with tempfile.TemporaryDirectory(prefix="orbix-upload-") as tmp:
        runtime_stub = SimpleNamespace(data_dir=Path(tmp))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(api, "runtime", runtime_stub))
            if free_bytes is not None:
                # The endpoint multiplies the cap by two to cover spool + store.
                stack.enter_context(
                    patch.object(
                        shutil,
                        "disk_usage",
                        lambda _path: SimpleNamespace(
                            total=free_bytes * 10, used=0, free=free_bytes
                        ),
                    )
                )
            return asyncio.run(
                api._with_streamed_csv(
                    _FakeRequest(chunks),
                    max_bytes,
                    processor or default_processor,
                )
            ), calls


class MbEnvTest(unittest.TestCase):
    """_mb_env parsing: megabytes in, bytes out, with a safe fallback."""

    MB = 1024 * 1024

    def read(self, raw, default_mb=256):
        with patch.dict("os.environ", {"ORBIX_TEST_CAP": raw}, clear=False):
            return api._mb_env("ORBIX_TEST_CAP", default_mb)

    def test_unset_uses_the_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(api._mb_env("ORBIX_TEST_CAP", 256), 256 * self.MB)

    def test_empty_and_whitespace_use_the_default(self) -> None:
        for raw in ("", "   "):
            with self.subTest(raw=raw):
                self.assertEqual(self.read(raw), 256 * self.MB)

    def test_integer_megabytes_are_converted_to_bytes(self) -> None:
        self.assertEqual(self.read("10"), 10 * self.MB)
        self.assertEqual(self.read("1", default_mb=64), 1 * self.MB)

    def test_float_string_is_truncated_not_rejected(self) -> None:
        # Operators write "1.5" expecting it to work; int(float(x)) accepts it.
        self.assertEqual(self.read("1.5"), 1 * self.MB)

    def test_garbage_falls_back_to_the_default(self) -> None:
        for raw in ("abc", "10MB", "ten", "1,000"):
            with self.subTest(raw=raw):
                self.assertEqual(self.read(raw, default_mb=64), 64 * self.MB)

    def test_values_below_one_mb_fall_back(self) -> None:
        # A 0 or negative cap would reject every upload, including empty ones,
        # which reads as a broken endpoint rather than a configured limit.
        for raw in ("0", "-1", "-100", "0.4"):
            with self.subTest(raw=raw):
                self.assertEqual(self.read(raw, default_mb=64), 64 * self.MB)

    def test_module_defaults_match_the_documented_values(self) -> None:
        # .env.example documents 256 MB for import and 64 MB for append. If these
        # drift, the documentation silently lies about the deployed limits.
        self.assertEqual(api.MAX_IMPORT_BYTES, 256 * self.MB)
        self.assertEqual(api.MAX_APPEND_BYTES, 64 * self.MB)
        self.assertGreater(api.MAX_IMPORT_BYTES, api.MAX_APPEND_BYTES)


class StreamedCsvLimitTest(unittest.TestCase):
    def test_oversized_body_is_rejected_with_413(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            run_upload([b"x" * 11], max_bytes=10, free_bytes=10_000)
        self.assertEqual(ctx.exception.status_code, 413)

    def test_body_exactly_at_the_cap_is_accepted(self) -> None:
        # The check is `> max_bytes`, so the boundary value must pass. An
        # off-by-one here would reject legitimately-sized uploads.
        result, calls = run_upload([b"x" * 10], max_bytes=10, free_bytes=10_000)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(calls), 1)

    def test_size_is_accumulated_across_chunks(self) -> None:
        # Each chunk is small; only the running total exceeds the cap. A per-chunk
        # check would let an unbounded upload through.
        with self.assertRaises(HTTPException) as ctx:
            run_upload([b"x" * 4] * 5, max_bytes=10, free_bytes=10_000)
        self.assertEqual(ctx.exception.status_code, 413)

    def test_empty_body_is_rejected_with_400(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            run_upload([], max_bytes=10, free_bytes=10_000)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("empty", str(ctx.exception.detail).lower())

    def test_insufficient_free_disk_is_refused_up_front_with_507(self) -> None:
        # required = max_bytes * 2; one byte short must refuse before any write.
        with self.assertRaises(HTTPException) as ctx:
            run_upload([b"x" * 5], max_bytes=10, free_bytes=19)
        self.assertEqual(ctx.exception.status_code, 507)
        self.assertIn("MB", str(ctx.exception.detail))

    def test_exactly_enough_free_disk_proceeds(self) -> None:
        result, _ = run_upload([b"x" * 5], max_bytes=10, free_bytes=20)
        self.assertEqual(result["count"], 1)

    def test_processor_receives_a_real_file_with_the_uploaded_bytes(self) -> None:
        seen: dict = {}

        def processor(path: Path) -> dict:
            seen["exists"] = path.is_file()
            seen["bytes"] = path.read_bytes()
            seen["name"] = path.name
            return {"ok": True}

        run_upload([b"abc", b"def"], max_bytes=100, free_bytes=10_000, processor=processor)
        self.assertTrue(seen["exists"])
        self.assertEqual(seen["bytes"], b"abcdef")
        self.assertEqual(seen["name"], "candles.csv")

    def test_processor_value_error_becomes_400_not_500(self) -> None:
        # A malformed CSV is a client error. Letting ValueError escape would
        # surface as an unhandled 500 and hide the actual parse problem.
        def processor(path: Path) -> dict:
            raise ValueError("missing required column: close")

        with self.assertRaises(HTTPException) as ctx:
            run_upload([b"abc"], max_bytes=100, free_bytes=10_000, processor=processor)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("close", str(ctx.exception.detail))

    def test_temp_upload_directory_is_cleaned_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orbix-upload-") as tmp:
            runtime_stub = SimpleNamespace(data_dir=Path(tmp))
            with patch.object(api, "runtime", runtime_stub), patch.object(
                shutil,
                "disk_usage",
                lambda _p: SimpleNamespace(total=10**9, used=0, free=10**6),
            ):
                asyncio.run(
                    api._with_streamed_csv(
                        _FakeRequest([b"abc"]), 100, lambda p: {"ok": True}
                    )
                )
            leftovers = [p.name for p in Path(tmp, "tmp").iterdir()] if Path(tmp, "tmp").is_dir() else []
            self.assertEqual(
                leftovers, [], f"spooled upload dirs were not removed: {leftovers}"
            )


if __name__ == "__main__":
    unittest.main()
