import csv
import io
import os
import shutil
import tempfile
import unittest

import jxlerate

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "Pillow is optional")
class ImageRegressionTests(unittest.TestCase):
    def make_image(self, size=(64, 64)):
        image = Image.new("RGB", size)
        pixels = image.load()
        for y in range(size[1]):
            for x in range(size[0]):
                value = ((x // 8) ^ (y // 8)) * 30
                pixels[x, y] = (value, (value * 3) % 256, (value * 7) % 256)
        exif = Image.Exif()
        exif[0x0112] = 6
        return image, exif

    def test_verification_detects_orientation_loss(self):
        image, exif = self.make_image()
        source = io.BytesIO()
        image.save(source, "JPEG", exif=exif)
        original = source.getvalue()
        stripped_without_orientation = jxlerate.strip_jpeg(
            original, keep_orientation=False)
        self.assertFalse(jxlerate.verify_image_bytes(
            original, stripped_without_orientation, keep_orientation=True))
        self.assertTrue(jxlerate.verify_image_bytes(
            original, stripped_without_orientation, keep_orientation=False))

    def test_lossy_reencode_retains_icc_and_orientation(self):
        image, exif = self.make_image()
        profile = b"test-icc-profile"
        source = io.BytesIO()
        image.save(source, "JPEG", quality=95, exif=exif, icc_profile=profile)
        stripped = jxlerate.strip_jpeg(source.getvalue(), keep_color=True)
        result, _ = jxlerate._lossy_bytes(
            stripped, ".jpg", 80, keep_orientation=True, keep_color=True)
        with Image.open(io.BytesIO(result)) as output:
            self.assertEqual(output.getexif().get(0x0112), 6)
            self.assertEqual(output.info.get("icc_profile"), profile)

    def test_lossy_rejects_unsupported_format(self):
        with self.assertRaisesRegex(ValueError, "JPEG and WebP only"):
            jxlerate._lossy_bytes(b"not a PNG", ".png", 80)

    def test_webp_optimizer_retains_orientation(self):
        image, exif = self.make_image((256, 256))
        source = io.BytesIO()
        image.save(source, "WEBP", lossless=True, quality=100,
                   method=0, exif=exif)
        stripped = jxlerate.strip_webp(source.getvalue())
        result, tool = jxlerate._optimize_webp(stripped, keep_color=False)
        if result is None:
            self.skipTest("this Pillow/libwebp build found no smaller candidate")
        with Image.open(io.BytesIO(result)) as output:
            self.assertEqual(output.getexif().get(0x0112), 6)
        self.assertTrue(jxlerate.verify_image_bytes(
            stripped, result, keep_orientation=True))

    @unittest.skipUnless(shutil.which("oxipng"), "oxipng not installed")
    def test_png_optimizer_retains_orientation(self):
        image, exif = self.make_image((256, 256))
        source = io.BytesIO()
        image.save(source, "PNG", compress_level=0, exif=exif)
        stripped = jxlerate.strip_png(source.getvalue())
        result, tool = jxlerate._optimize_png(stripped, max_effort=False)
        if result is None:
            self.skipTest("optimizer found no smaller candidate")
        with Image.open(io.BytesIO(result)) as output:
            self.assertEqual(output.getexif().get(0x0112), 6)
        self.assertTrue(jxlerate.verify_image_bytes(
            stripped, result, keep_orientation=True))


class ArchiveSafetyTests(unittest.TestCase):
    def test_explicit_non_image_is_passthrough_not_image_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "notes.txt")
            with open(source, "w") as f:
                f.write("metadata companion")
            self.assertEqual(jxlerate.collect_file_entries([source], False), [])
            self.assertEqual(
                jxlerate.collect_passthrough_entries([source], False, False),
                [(source, "notes.txt")])

    def test_output_collisions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "same output"):
            jxlerate.validate_output_plan(
                [("/input/a/photo.png", "/output/photo.png"),
                 ("/input/b/photo.png", "/output/photo.png")],
                ["/input/a/photo.png", "/input/b/photo.png"])

    def test_manifest_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "photo.png")
            alias = os.path.join(directory, "manifest.csv")
            with open(source, "wb") as f:
                f.write(b"image bytes")
            with self.assertRaisesRegex(ValueError, "manifest"):
                jxlerate.validate_output_plan(
                    [(source, source)], [source], manifest_path=source)
            try:
                os.link(source, alias)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "hardlink"):
                jxlerate.validate_output_plan(
                    [(source, source)], [source], manifest_path=alias)

    def test_manifest_inside_input_tree_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = os.path.join(directory, "existing.csv")
            with open(manifest, "w") as f:
                f.write("preserve this file")
            with self.assertRaisesRegex(ValueError, "input folder"):
                jxlerate.validate_manifest_location(manifest, [directory])

    def test_malformed_and_empty_manifests_fail_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            malformed = os.path.join(directory, "bad.csv")
            with open(malformed, "w") as f:
                f.write("not,a,manifest\n")
            self.assertGreater(jxlerate.verify_manifest(malformed)[1], 0)

            empty = os.path.join(directory, "empty.csv")
            with open(empty, "w", newline="") as f:
                csv.writer(f).writerow(jxlerate.MANIFEST_FIELDS)
            self.assertGreater(jxlerate.verify_manifest(empty)[1], 0)

    def test_dedupe_does_not_merge_different_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "first.jpg")
            same_metadata = os.path.join(directory, "same.jpg")
            different_metadata = os.path.join(directory, "different.jpg")
            for path in (first, same_metadata, different_metadata):
                with open(path, "wb") as f:
                    f.write(b"identical content")
            os.chmod(first, 0o644)
            os.chmod(same_metadata, 0o644)
            os.chmod(different_metadata, 0o600)
            timestamp = 1_700_000_000_000_000_000
            for path in (first, same_metadata, different_metadata):
                os.utime(path, ns=(timestamp, timestamp))

            try:
                links, _, failures = jxlerate.dedupe_entries(
                    [(first, None), (same_metadata, None),
                     (different_metadata, None)])
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")
            self.assertEqual((links, failures), (1, 0))
            self.assertTrue(os.path.samefile(first, same_metadata))
            self.assertFalse(os.path.samefile(first, different_metadata))


if __name__ == "__main__":
    unittest.main()
