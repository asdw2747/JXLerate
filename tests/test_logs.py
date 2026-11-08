import codecs
import os
import tempfile
import threading
import unittest
from unittest import mock

from jxlerate.core import NullReporter, RunOptions, process_one, run_batch
from jxlerate.logs import log_search_roots, patch_archive_logs


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class LogPatchingTests(unittest.TestCase):
    def test_replaces_all_supported_image_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "discord.html")
            names = ["alpha-abcdef123456.png", "beta-abcdef123456.jpg",
                     "gamma-abcdef123456.jpeg", "delta-abcdef123456.webp",
                     "epsilon-abcdef123456.gif"]
            write_bytes(log, "\n".join(
                f'<img src="{name}">' for name in names).encode("utf-8"))

            for name in names:
                stem = os.path.splitext(name)[0]
                patched, failed = patch_archive_logs(
                    [(directory, False)], name, stem + ".jxl")
                self.assertEqual((patched, failed), (1, 0))

            content = read_bytes(log).decode("utf-8")
            for name in names:
                stem = os.path.splitext(name)[0]
                self.assertNotIn(name, content)
                self.assertIn(f'src="{stem}.jxl"', content)

    def test_matches_whole_filenames_only(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "index.html")
            content = (
                'ok1="cat.png" ok2="photos/cat.png" ok3="cat.png?x=1" '
                'ok4="cat.png#frag" ok5="CAT.PNG" '
                'bad1="copycat.png" bad2="my-cat.png" bad3="cat.png.bak" '
                'bad4="cat.pngx" '
                'h1="cat-abcdef123456.png" h2="photos/cat-abcdef123456.png" '
                'h3="cat-abcdef123456.png?x=1" '
                'h4="cat-abcdef123456.png#frag" '
                'h5="CAT-ABCDEF123456.PNG" '
                'hb1="copycat-abcdef123456.png" '
                'hb2="my-cat-abcdef123456.png" '
                'hb3="cat-abcdef123456.png.bak" '
                'hb4="cat-abcdef123456.pngx"')
            write_bytes(log, content.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))

            expected = (
                'ok1="cat.jxl" ok2="photos/cat.jxl" ok3="cat.jxl?x=1" '
                'ok4="cat.jxl#frag" ok5="cat.jxl" '
                'bad1="copycat.png" bad2="my-cat.png" bad3="cat.png.bak" '
                'bad4="cat.pngx" '
                'h1="cat-abcdef123456.jxl" '
                'h2="photos/cat-abcdef123456.jxl" '
                'h3="cat-abcdef123456.jxl?x=1" '
                'h4="cat-abcdef123456.jxl#frag" '
                'h5="cat-ABCDEF123456.jxl" '
                'hb1="copycat-abcdef123456.png" '
                'hb2="my-cat-abcdef123456.png" '
                'hb3="cat-abcdef123456.png.bak" '
                'hb4="cat-abcdef123456.pngx"')
            self.assertEqual(read_bytes(log).decode("utf-8"), expected)

    def test_recursive_and_non_recursive_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = os.path.join(directory, "logs")
            os.makedirs(nested)
            top_log = os.path.join(directory, "top.txt")
            nested_log = os.path.join(nested, "nested.txt")
            write_bytes(top_log, b"top cat-abcdef123456.png")
            write_bytes(nested_log, b"nested cat-abcdef123456.png")

            self.assertEqual(
                patch_archive_logs([(directory, False)],
                                   "cat.png", "cat.jxl"),
                (1, 0))
            self.assertIn(b"cat-abcdef123456.jxl", read_bytes(top_log))
            self.assertIn(b"cat-abcdef123456.png", read_bytes(nested_log))

            self.assertEqual(
                patch_archive_logs([(directory, True)],
                                   "cat.png", "cat.jxl"),
                (1, 0))
            self.assertIn(b"cat-abcdef123456.jxl", read_bytes(nested_log))

    def test_file_inputs_scan_their_parent_without_recursing(self):
        with tempfile.TemporaryDirectory() as directory:
            image = os.path.join(directory, "photo.png")
            write_bytes(image, b"data")

            roots = log_search_roots([image], recursive=True)
            self.assertEqual(
                roots, [(os.path.abspath(directory), False)])

    def test_utf8_bom_crlf_and_unicode_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.html")
            text = 'héllo 😀\r\n<img src="cat-abcdef123456.png">\r\n'
            write_bytes(log, codecs.BOM_UTF8 + text.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))

            raw = read_bytes(log)
            self.assertTrue(raw.startswith(codecs.BOM_UTF8))
            decoded = raw[len(codecs.BOM_UTF8):].decode("utf-8")
            self.assertEqual(
                decoded,
                text.replace("cat-abcdef123456.png",
                             "cat-abcdef123456.jxl"))
            self.assertIn("\r\n", decoded)

    def test_utf16_bom_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.json")
            text = '{"image": "cat-abcdef123456.png", "note": "猫"}'
            write_bytes(log, codecs.BOM_UTF16_LE + text.encode("utf-16-le"))

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))

            raw = read_bytes(log)
            self.assertTrue(raw.startswith(codecs.BOM_UTF16_LE))
            decoded = raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le")
            self.assertEqual(
                decoded,
                text.replace("cat-abcdef123456.png",
                             "cat-abcdef123456.jxl"))

    def test_undecodable_file_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "broken.txt")
            raw = b"\xc3\x28 cat-abcdef123456.png"
            write_bytes(log, raw)

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (0, 0))
            self.assertEqual(read_bytes(log), raw)

    def test_patching_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.txt")
            write_bytes(log, b"cat-abcdef123456.png")

            self.assertEqual(
                patch_archive_logs([(directory, False)],
                                   "cat.png", "cat.jxl"),
                (1, 0))
            self.assertEqual(
                patch_archive_logs([(directory, False)],
                                   "cat.png", "cat.jxl"),
                (0, 0))
            self.assertEqual(read_bytes(log), b"cat-abcdef123456.jxl")

    def test_concurrent_patches_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.json")
            names = [f"image{index}-abcdef123456.png" for index in range(8)]
            write_bytes(log, " ".join(names).encode("utf-8"))

            threads = [
                threading.Thread(
                    target=patch_archive_logs,
                    args=([(directory, False)], name,
                          os.path.splitext(name)[0] + ".jxl"))
                for name in names
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            content = read_bytes(log).decode("utf-8")
            for name in names:
                self.assertNotIn(name, content)
                self.assertIn(
                    os.path.splitext(name)[0] + ".jxl", content)

    def test_discord_hash_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "messages.html")
            text = ('<a href="IMG_7320-5eb75cfcf2d6578914.jpg">'
                    '<img src="IMG_7320-5eb75cfcf2d6578914.jpg"></a>')
            write_bytes(log, text.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], "IMG_7320.jpg", "IMG_7320.jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                text.replace("IMG_7320-5eb75cfcf2d6578914.jpg",
                             "IMG_7320-5eb75cfcf2d6578914.jxl"))

    def test_hash_and_extension_case_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.txt")
            write_bytes(log, b"CAT-ABCDEF123456.PNG cat-abcdef123456.PnG")

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log),
                b"cat-ABCDEF123456.jxl cat-abcdef123456.jxl")

    def test_hash_requires_hex_and_minimum_length(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.txt")
            content = (
                'bad1="cat-abc12.png" bad2="cat-2024.png" '
                'bad3="cat-abcdefgh.png" bad4="cat-abcdefg.png" '
                'ok="cat-abcdef12.png" ok18="cat-5eb75cfcf2d6578914.png"')
            write_bytes(log, content.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))

            expected = (
                'bad1="cat-abc12.png" bad2="cat-2024.png" '
                'bad3="cat-abcdefgh.png" bad4="cat-abcdefg.png" '
                'ok="cat-abcdef12.jxl" '
                'ok18="cat-5eb75cfcf2d6578914.jxl"')
            self.assertEqual(read_bytes(log).decode("utf-8"), expected)

    def test_wrong_extension_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.txt")
            write_bytes(log, b"cat-abcdef123456.jpeg cat-abcdef123456.png")

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log),
                b"cat-abcdef123456.jpeg cat-abcdef123456.jxl")

    def test_plain_and_hashed_references_both_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.html")
            write_bytes(
                log,
                b'<img src="cat.png"><img src="cat-abcdef123456.png">')

            patched, failed = patch_archive_logs(
                [(directory, False)], "cat.png", "cat.jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log),
                b'<img src="cat.jxl"><img src="cat-abcdef123456.jxl">')

    def test_bare_name_without_extension_is_not_patched(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.txt")
            write_bytes(log, b"README README.md")

            patched, failed = patch_archive_logs(
                [(directory, False)], "README", "README.jxl")
            self.assertEqual((patched, failed), (0, 0))
            self.assertEqual(read_bytes(log), b"README README.md")


class JxlLogIntegrationTests(unittest.TestCase):
    def test_logs_are_patched_before_the_original_is_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "IMG_7320.jpg")
            write_bytes(source, b"source-bytes")
            output = os.path.join(directory, "IMG_7320.jxl")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, b'<img src="IMG_7320-5eb75cfcf2d6578914.jpg">'
                              b'<img src="IMG_7320.jpg">')

            seen = {}
            real_patch = patch_archive_logs

            def checked_patch(roots, old_name, new_name):
                seen["source_exists"] = os.path.exists(source)
                seen["output_exists"] = os.path.exists(output)
                return real_patch(roots, old_name, new_name)

            options = RunOptions(
                inputs=(source,), to_jxl=True, replace=True, patch_logs=True)
            with mock.patch(
                    "jxlerate.core.to_jxl_file",
                    return_value=(b"jxl-bytes", 12, "cjxl")), \
                    mock.patch("jxlerate.core.patch_archive_logs",
                               side_effect=checked_patch):
                summary = run_batch(options, NullReporter())

            self.assertEqual((summary.ok, summary.failed), (1, 0))
            self.assertTrue(seen["source_exists"])
            self.assertTrue(seen["output_exists"])
            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.isfile(output))
            self.assertEqual(
                read_bytes(log),
                b'<img src="IMG_7320-5eb75cfcf2d6578914.jxl">'
                b'<img src="IMG_7320.jxl">')

    def test_failed_log_patch_keeps_the_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "IMG_7320.jpg")
            write_bytes(source, b"source-bytes")
            output = os.path.join(directory, "IMG_7320.jxl")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, b'<img src="IMG_7320-5eb75cfcf2d6578914.jpg">')

            options = RunOptions(
                inputs=(source,), to_jxl=True, replace=True, patch_logs=True)
            with mock.patch(
                    "jxlerate.core.to_jxl_file",
                    return_value=(b"jxl-bytes", 12, "cjxl")), \
                    mock.patch("jxlerate.core.patch_archive_logs",
                               return_value=(0, 1)):
                result = process_one(source, output, options)

            self.assertEqual(result.status, "written")
            self.assertEqual(result.logs_failed, 1)
            self.assertTrue(os.path.exists(source))
            self.assertTrue(os.path.isfile(output))
            self.assertIn(b"IMG_7320-5eb75cfcf2d6578914.jpg",
                          read_bytes(log))

    def test_dry_run_does_not_patch_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "IMG_7320.jpg")
            write_bytes(source, b"source-bytes")
            output = os.path.join(directory, "IMG_7320.jxl")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, b'<img src="IMG_7320-5eb75cfcf2d6578914.jpg">')

            options = RunOptions(
                inputs=(source,), to_jxl=True, dry_run=True, patch_logs=True)
            with mock.patch(
                    "jxlerate.core.to_jxl_file",
                    return_value=(b"jxl-bytes", 12, "cjxl")):
                result = process_one(source, output, options)

            self.assertEqual(result.status, "dry-run")
            self.assertEqual(result.logs_patched, 0)
            self.assertIn(b"IMG_7320-5eb75cfcf2d6578914.jpg",
                          read_bytes(log))


if __name__ == "__main__":
    unittest.main()
