import array
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image, ImageDraw

from videopipe.backends.cpu import CPUGenerator
from videopipe.media import assemble, finalize, inspect_video, normalize_clip, run_ffmpeg, sample_frames
from videopipe.models import MediaConfig, Shot


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        image = Image.new("RGB", (240, 160), "#dbb28d")
        ImageDraw.Draw(image).ellipse((50, 20, 170, 140), fill="#dc531c")
        self.reference = self.directory / "cup.png"
        image.save(self.reference)
        self.shot = Shot(id="cup", prompt="An orange cup", reference_images=[self.reference],
                         duration=1, width=160, height=96, fps=12)
        self.clip = CPUGenerator().generate(self.shot, self.directory, attempt=1)
        self.config = MediaConfig(width=192, height=128, fps=12)

    def test_cpu_preview_and_sampled_frames_are_real_images(self):
        info = inspect_video(self.clip)
        self.assertEqual((info.width, info.height, info.fps, info.has_audio), (160, 96, 12, False))
        self.assertAlmostEqual(info.duration, 1, delta=0.05)
        frames = sample_frames(self.clip, self.directory / "frames", count=8, max_size=80)
        self.assertEqual(len(frames), 8)
        for frame in frames:
            with Image.open(frame) as image:
                image.load()
                self.assertEqual(image.size, (80, 48))
        self.assertNotEqual(frames[0].read_bytes(), frames[-1].read_bytes())

    def test_normalize_pads_duration_and_adds_silent_audio(self):
        normalized = normalize_clip(self.clip, self.directory / "normalized.mp4", duration=1.5, config=self.config)
        info = inspect_video(normalized)
        self.assertEqual((info.width, info.height, info.fps, info.has_audio), (192, 128, 12, True))
        self.assertAlmostEqual(info.duration, 1.5, delta=0.05)
        run_ffmpeg(["-loglevel", "error", "-i", str(normalized), "-f", "null", "-"])

    def test_mixed_audio_assembly_and_lanczos_delivery(self):
        audio_clip = self.directory / "with-audio.mp4"
        run_ffmpeg([
            "-loglevel", "error", "-i", str(self.clip), "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "copy", "-c:a", "aac", str(audio_clip),
        ])
        self.assertTrue(inspect_video(audio_clip).has_audio)
        quiet = normalize_clip(self.clip, self.directory / "quiet.mp4", duration=1, config=self.config)
        audible = normalize_clip(audio_clip, self.directory / "audible.mp4", duration=1, config=self.config)
        movie = assemble([quiet, audible], self.directory / "assembled.mp4", self.config)
        info = inspect_video(movie)
        self.assertAlmostEqual(info.duration, 2, delta=0.08)
        self.assertTrue(info.has_audio)

        config = self.config.model_copy(update={"upscale": "lanczos", "upscale_factor": 2})
        delivered = finalize(movie, self.directory / "delivery.mp4", config)
        final_info = inspect_video(delivered)
        self.assertEqual((final_info.width, final_info.height, final_info.fps), (384, 256, 12))
        self.assertAlmostEqual(final_info.duration, 2, delta=0.1)
        self.assertTrue(final_info.has_audio)
        audio = self.directory / "audio.wav"
        run_ffmpeg(["-loglevel", "error", "-i", str(delivered), "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(audio)])
        with wave.open(str(audio)) as stream:
            samples = array.array("h", stream.readframes(stream.getnframes()))
            rate = stream.getframerate()
        self.assertLess(max(abs(value) for value in samples[:rate // 2]), 4)
        self.assertGreater(max(abs(value) for value in samples[rate + rate // 2:]), 500)

    def test_comfyui_finalization_does_not_scale_twice(self):
        config = self.config.model_copy(update={"upscale": "comfyui", "upscale_factor": 2})
        result = finalize(self.clip, self.directory / "encoded.mp4", config)
        info = inspect_video(result)
        self.assertEqual((info.width, info.height), (160, 96))


if __name__ == "__main__":
    unittest.main()
