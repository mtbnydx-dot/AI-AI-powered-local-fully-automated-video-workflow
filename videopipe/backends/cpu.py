from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from ..media import run_ffmpeg
from ..models import Shot


class CPUGenerator:
    """Reference-image camera-motion preview; this backend performs no model inference."""

    def generate(self, shot: Shot, output_dir: Path, *, attempt: int, feedback: str = "") -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{shot.id}-attempt-{attempt}.mp4"
        reference = output_dir / f"{shot.id}-preview.png"
        canvas = self._reference_canvas(shot)
        canvas.save(reference)
        frames = max(1, round(shot.duration * shot.fps))
        zoom = f"1+0.08*on/{frames}"
        run_ffmpeg([
            "-loglevel", "error", "-i", str(reference), "-vf",
            f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:"
            f"s={shot.width}x{shot.height}:fps={shot.fps}",
            "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-threads", "2", "-movflags", "+faststart", str(target),
        ])
        return target

    def _reference_canvas(self, shot: Shot) -> Image.Image:
        size = (shot.width * 2, shot.height * 2)
        first_frames = ([asset.path for asset in shot.asset_bindings if asset.role == "first_frame"]
                        if shot.asset_bindings else shot.reference_images[:1])
        if first_frames:
            with Image.open(first_frames[0]) as image:
                canvas = ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
        else:
            canvas = Image.new("RGB", size, "#303944")
            ImageDraw.Draw(canvas).text((24, 60), shot.prompt[:160], fill="white")
        ImageDraw.Draw(canvas).text((24, 24), f"CPU PREVIEW / {shot.id}", fill="white", stroke_width=1, stroke_fill="black")
        return canvas
