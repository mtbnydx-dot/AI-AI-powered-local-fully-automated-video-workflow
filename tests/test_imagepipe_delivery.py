import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from imagepipe.delivery import deliver_image


class ImageDeliveryTests(unittest.TestCase):
    def test_reference_canvas_is_center_cropped_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            target = Path(directory) / "assets" / "first-frame.png"
            source = Image.new("RGBA", (100, 100), "red")
            ImageDraw.Draw(source).rectangle((30, 0, 69, 99), fill="blue")
            source.save(original)
            before = original.read_bytes()

            record = deliver_image(original, target, 20, 50)

            with Image.open(target) as result:
                self.assertEqual((result.size, result.mode, result.format), ((20, 50), "RGB", "PNG"))
                self.assertEqual(result.getpixel((10, 25)), (0, 0, 255))
                self.assertGreater(result.getpixel((1, 25))[2], 240)
            self.assertEqual(record["source_size"], [100, 100])
            self.assertEqual(record["crop_box"], [30, 0, 70, 100])
            self.assertEqual(original.read_bytes(), before)

    def test_exact_size_keeps_pixels_without_resampling(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            target = Path(directory) / "result.png"
            source = Image.new("RGB", (9, 16), "white")
            source.putpixel((3, 8), (17, 28, 39))
            source.save(original)

            record = deliver_image(original, target, 9, 16)

            with Image.open(target) as result:
                self.assertEqual(result.tobytes(), source.tobytes())
            self.assertFalse(record["resized"])

    def test_pad_keeps_edge_content_and_adds_letterboxing(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            target = Path(directory) / "padded.png"
            source = Image.new("RGB", (100, 50), "blue")
            ImageDraw.Draw(source).rectangle((0, 0, 19, 49), fill="red")
            source.save(original)
            record = deliver_image(original, target, 100, 100, "pad")
            with Image.open(target) as result:
                self.assertEqual(result.getpixel((5, 50)), (255, 0, 0))
                self.assertEqual(result.getpixel((50, 50)), (0, 0, 255))
                self.assertEqual(result.getpixel((50, 0)), (0, 0, 0))
            self.assertEqual(record["crop_box"], [0, 0, 100, 50])


if __name__ == "__main__":
    unittest.main()
