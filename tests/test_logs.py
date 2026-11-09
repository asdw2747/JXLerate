import codecs
import os
import tempfile
import threading
import unittest
from unittest import mock

from jxlerate.core import NullReporter, RunOptions, process_one, run_batch
from jxlerate.fs import _replace_with_retry
from jxlerate.logs import (
    _filename_pattern, log_search_roots, patch_archive_logs,
    patch_existing_jxl_logs)


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class AtomicWriteRetryTests(unittest.TestCase):
    def test_replace_retries_a_windows_sharing_violation(self):
        calls = []

        def flaky_replace(source, destination):
            calls.append((source, destination))
            if len(calls) == 1:
                raise PermissionError("sharing violation")
            return None

        with mock.patch("jxlerate.fs.os.name", "nt"), \
                mock.patch("jxlerate.fs.os.replace",
                           side_effect=flaky_replace), \
                mock.patch("jxlerate.fs.time.sleep"):
            _replace_with_retry("source", "destination")

        self.assertEqual(len(calls), 2)


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

    def test_files_folder_inputs_include_the_export_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            image = os.path.join(files, "photo.jpg")
            write_bytes(image, b"data")

            roots = log_search_roots([files], recursive=True)
            self.assertIn((os.path.abspath(files), True), roots)
            self.assertIn((os.path.abspath(directory), False), roots)

            roots = log_search_roots([image], recursive=False)
            self.assertIn((os.path.abspath(files), False), roots)
            self.assertIn((os.path.abspath(directory), False), roots)

    def test_nested_files_input_walks_up_to_the_export_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            nested = os.path.join(files, "nested")
            os.makedirs(nested)

            roots = log_search_roots([nested], recursive=True)
            self.assertIn((os.path.abspath(nested), True), roots)
            self.assertIn((os.path.abspath(directory), False), roots)

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

            results = []

            def run(name):
                results.append(patch_archive_logs(
                    [(directory, False)], name,
                    os.path.splitext(name)[0] + ".jxl"))

            threads = [
                threading.Thread(target=run, args=(name,))
                for name in names
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(results.count((1, 0)), len(names), results)

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

    def test_full_path_is_reduced_to_the_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = os.path.join(directory, "Encoded%20Folder")
            os.makedirs(nested)
            source = os.path.join(nested, "IMG_7320.jpg")
            output = os.path.join(nested, "IMG_7320.jxl")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, b'<img src="images/IMG_7320.jpg">')

            pattern = _filename_pattern(source)
            self.assertIsNotNone(pattern)
            self.assertNotIn("Encoded", pattern.pattern)

            patched, failed = patch_archive_logs(
                [(directory, False)], source, output)
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log), b'<img src="images/IMG_7320.jxl">')

    def test_matches_after_url_and_html_delimiters(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "archive.html")
            content = (
                'url="https://cdn.example/attachments/1/2/'
                'IMG_7320-5eb75cfcf2d6578914.jpg?ex=1&is=2&hm=3"\n'
                'attr=src=images/IMG_7320.jpg\n'
                'quoted="IMG_7320.jpg"\n'
                'tag=dir%20name/IMG_7320.jpg\n'
                'tag=dir%20name%20IMG_7320.jpg\n'
                'tag=dir%u2019name%20IMG_7320.jpg\n'
                'decoy=copyIMG_7320.jpg\n')
            write_bytes(log, content.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], "IMG_7320.jpg", "IMG_7320.jxl")
            self.assertEqual((patched, failed), (1, 0))

            expected = (
                'url="https://cdn.example/attachments/1/2/'
                'IMG_7320-5eb75cfcf2d6578914.jxl?ex=1&is=2&hm=3"\n'
                'attr=src=images/IMG_7320.jxl\n'
                'quoted="IMG_7320.jxl"\n'
                'tag=dir%20name/IMG_7320.jxl\n'
                'tag=dir%20name%20IMG_7320.jxl\n'
                'tag=dir%u2019name%20IMG_7320.jxl\n'
                'decoy=copyIMG_7320.jpg\n')
            self.assertEqual(read_bytes(log).decode("utf-8"), expected)

    def test_percent_encoded_basename_is_patched(self):
        with tempfile.TemporaryDirectory() as directory:
            name = ("i_p_c_191919,bnr_1,wmk,s_630,"
                    "q_90-bb67314a40a0660c.jpg")
            encoded = name.replace(",", "%2C")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, f'<img src="Export_Files/{encoded}">'.encode())

            patched, failed = patch_archive_logs(
                [(directory, False)], name, name[:-4] + ".jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                f'<img src="Export_Files/{encoded[:-4]}.jxl">')

    def test_double_encoded_percent_basename_is_patched(self):
        with tempfile.TemporaryDirectory() as directory:
            name = (
                "My%20Little%20Pony%20Forever%20Friendship-"
                "3b2fd2a7e58e4ca6.jpg")
            encoded = name.replace("%", "%25")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, f'<img src="Export_Files/{encoded}">'.encode())

            patched, failed = patch_archive_logs(
                [(directory, False)], name, name[:-4] + ".jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                f'<img src="Export_Files/{encoded[:-4]}.jxl">')

    def test_encoded_unicode_basename_is_patched(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "Bob\u2019s photo.jpg"
            log = os.path.join(directory, "archive.html")
            content = (
                '<img src="Files/Bob%E2%80%99s%20photo.jpg">\n'
                '<img src="Files/Bob%u2019s%20photo.jpg">\n'
                '<img src="Files/Bob\u2019s photo.jpg">\n')
            write_bytes(log, content.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], name, name[:-4] + ".jxl")
            self.assertEqual((patched, failed), (1, 0))

            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                '<img src="Files/Bob%E2%80%99s%20photo.jxl">\n'
                '<img src="Files/Bob%u2019s%20photo.jxl">\n'
                '<img src="Files/Bob\u2019s photo.jxl">\n')

    def test_encoded_basename_with_hash_is_patched(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "Bob\u2019s photo.jpg"
            reference = "Bob%E2%80%99s%20photo-5eb75cfcf2d6578914.jpg"
            log = os.path.join(directory, "archive.html")
            write_bytes(log, f'<img src="Files/{reference}">'.encode())

            patched, failed = patch_archive_logs(
                [(directory, False)], name, name[:-4] + ".jxl")
            self.assertEqual((patched, failed), (1, 0))
            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                '<img src="Files/'
                'Bob%E2%80%99s%20photo-5eb75cfcf2d6578914.jxl">')

    def test_encoded_basename_respects_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "Bob\u2019s photo.jpg"
            base = "Bob%E2%80%99s%20photo"
            content = (
                f'bad1="copy{base}.jpg" bad2="my-{base}.jpg" '
                f'bad3="{base}.jpg.bak" bad4="{base}.jpgx" '
                f'bad5="{base}.jpeg" '
                f'ok="{base}.jpg"')
            log = os.path.join(directory, "archive.html")
            write_bytes(log, content.encode("utf-8"))

            patched, failed = patch_archive_logs(
                [(directory, False)], name, name[:-4] + ".jxl")
            self.assertEqual((patched, failed), (1, 0))

            expected = (
                f'bad1="copy{base}.jpg" bad2="my-{base}.jpg" '
                f'bad3="{base}.jpg.bak" bad4="{base}.jpgx" '
                f'bad5="{base}.jpeg" '
                f'ok="{base}.jxl"')
            self.assertEqual(read_bytes(log).decode("utf-8"), expected)

    def test_encoded_basename_patching_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "i_p_c_191919,bnr_1,wmk,s_630,q_90-bb67314a40a0660c.jpg"
            encoded = name.replace(",", "%2C")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, f'<img src="{encoded}">'.encode())

            self.assertEqual(
                patch_archive_logs([(directory, False)],
                                   name, name[:-4] + ".jxl"),
                (1, 0))
            self.assertEqual(
                patch_archive_logs([(directory, False)],
                                   name, name[:-4] + ".jxl"),
                (0, 0))
            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                f'<img src="{encoded[:-4]}.jxl">')


class ExistingJxlReconciliationTests(unittest.TestCase):
    def test_patches_local_references_and_skips_remote_or_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            write_bytes(os.path.join(files, "photo.jxl"), b"jxl")
            log = os.path.join(directory, "Export.html")
            content = (
                '<img src="Export.html_Files/photo.jpg">\n'
                '<a href="https://cdn.example/'
                'Export.html_Files/photo.jpg">remote</a>\n'
                '<img src="Export.html_Files/ghost.jpg">\n'
                '<img src="Export.html_Files/photo.jpg.bak">\n')
            write_bytes(log, content.encode("utf-8"))

            self.assertEqual(
                patch_existing_jxl_logs([(directory, True)]), (1, 0))

            expected = (
                '<img src="Export.html_Files/photo.jxl">\n'
                '<a href="https://cdn.example/'
                'Export.html_Files/photo.jpg">remote</a>\n'
                '<img src="Export.html_Files/ghost.jpg">\n'
                '<img src="Export.html_Files/photo.jpg.bak">\n')
            self.assertEqual(read_bytes(log).decode("utf-8"), expected)

    def test_patches_encoded_basenames(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            names = [
                ("i_p_c_191919,bnr_1,wmk,s_630,"
                 "q_90-bb67314a40a0660c.jpg"),
                ("My%20Little%20Pony%20Forever%20Friendship-"
                 "3b2fd2a7e58e4ca6.jpg"),
                "Bob\u2019s photo.jpg",
            ]
            for name in names:
                write_bytes(
                    os.path.join(files, name[:-4] + ".jxl"), b"jxl")

            comma = names[0].replace(",", "%2C")
            percent = names[1].replace("%", "%25")
            bob_utf8 = "Bob%E2%80%99s%20photo.jpg"
            bob_js = "Bob%u2019s%20photo.jpg"
            log = os.path.join(directory, "Export.html")
            content = (
                f'<img src="Export.html_Files/{comma}">\n'
                f'<img src="Export.html_Files/{percent}">\n'
                f'<img src="Export.html_Files/{bob_utf8}">\n'
                f'<img src="Export.html_Files/{bob_js}">\n')
            write_bytes(log, content.encode("utf-8"))

            self.assertEqual(
                patch_existing_jxl_logs([(directory, True)]), (1, 0))

            expected = (
                f'<img src="Export.html_Files/{comma[:-4]}.jxl">\n'
                f'<img src="Export.html_Files/{percent[:-4]}.jxl">\n'
                f'<img src="Export.html_Files/{bob_utf8[:-4]}.jxl">\n'
                f'<img src="Export.html_Files/{bob_js[:-4]}.jxl">\n')
            self.assertEqual(read_bytes(log).decode("utf-8"), expected)

    def test_preserves_a_discord_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            write_bytes(os.path.join(files, "IMG_7320.jxl"), b"jxl")
            log = os.path.join(directory, "Export.html")
            reference = "IMG_7320-5eb75cfcf2d6578914.jpg"
            write_bytes(
                log, f'<img src="Export.html_Files/{reference}">'.encode())

            self.assertEqual(
                patch_existing_jxl_logs([(directory, True)]), (1, 0))
            self.assertEqual(
                read_bytes(log).decode("utf-8"),
                '<img src="Export.html_Files/'
                'IMG_7320-5eb75cfcf2d6578914.jxl">')

    def test_handles_a_log_moved_inside_its_own_files_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            write_bytes(os.path.join(files, "photo.jxl"), b"jxl")
            log = os.path.join(files, "Export.html")
            write_bytes(
                log, b'<img src="Export.html_Files/photo.jpg">')

            self.assertEqual(
                patch_existing_jxl_logs([(files, False)]), (1, 0))
            self.assertEqual(
                read_bytes(log),
                b'<img src="Export.html_Files/photo.jxl">')

    def test_dry_run_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            write_bytes(os.path.join(files, "photo.jxl"), b"jxl")
            log = os.path.join(directory, "Export.html")
            original = b'<img src="Export.html_Files/photo.jpg">'
            write_bytes(log, original)

            self.assertEqual(
                patch_existing_jxl_logs([(directory, True)],
                                        dry_run=True),
                (1, 0))
            self.assertEqual(read_bytes(log), original)

    def test_no_jxl_files_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "Export.html")
            original = b'<img src="photo.jpg">'
            write_bytes(log, original)

            self.assertEqual(
                patch_existing_jxl_logs([(directory, True)]), (0, 0))
            self.assertEqual(read_bytes(log), original)


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

    def test_encoded_discord_export_reference_is_patched(self):
        with tempfile.TemporaryDirectory() as directory:
            name = ("i_p_c_191919,bnr_1,wmk,s_630,"
                    "q_90-bb67314a40a0660c.jpg")
            encoded = name.replace(",", "%2C")
            source = os.path.join(directory, name)
            write_bytes(source, b"source-bytes")
            output = os.path.join(directory, name[:-4] + ".jxl")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, (
                '<div class=chatlog__attachment><a href='
                f'Export_Files/{encoded}><img src=Export_Files/{encoded}>'
                '</a></div>').encode("utf-8"))

            options = RunOptions(
                inputs=(source,), to_jxl=True, replace=True, patch_logs=True)
            with mock.patch(
                    "jxlerate.core.to_jxl_file",
                    return_value=(b"jxl-bytes", 12, "cjxl")):
                summary = run_batch(options, NullReporter())

            self.assertEqual((summary.ok, summary.failed), (1, 0))
            self.assertFalse(os.path.exists(source))
            self.assertTrue(os.path.isfile(output))
            patched = read_bytes(log).decode("utf-8")
            self.assertEqual(patched.count(f"Export_Files/{encoded[:-4]}.jxl"),
                             2)
            self.assertNotIn(encoded, patched)

    def test_reconciliation_runs_when_no_source_images_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            files = os.path.join(directory, "Export.html_Files")
            os.makedirs(files)
            write_bytes(os.path.join(files, "photo.jxl"), b"jxl")
            log = os.path.join(directory, "Export.html")
            write_bytes(log, b'<img src="Export.html_Files/photo.jpg">')

            options = RunOptions(
                inputs=(files,), to_jxl=True, replace=True,
                patch_logs=True, recursive=True)
            summary = run_batch(options, NullReporter())

            self.assertTrue(summary.no_images)
            self.assertEqual((summary.ok, summary.failed), (0, 0))
            self.assertEqual(
                read_bytes(log),
                b'<img src="Export.html_Files/photo.jxl">')

    def test_reconciliation_runs_when_skip_existing_skips_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "photo.jpg")
            write_bytes(source, b"source-bytes")
            output = os.path.join(directory, "photo.jxl")
            write_bytes(output, b"jxl")
            log = os.path.join(directory, "archive.html")
            write_bytes(log, b'<img src="photo.jpg">')

            options = RunOptions(
                inputs=(source,), to_jxl=True, replace=True,
                patch_logs=True, skip_existing=True)
            summary = run_batch(options, NullReporter())

            self.assertEqual((summary.ok, summary.failed), (0, 0))
            self.assertEqual(summary.skipped, 1)
            self.assertTrue(os.path.exists(source))
            self.assertEqual(read_bytes(log), b'<img src="photo.jxl">')

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
