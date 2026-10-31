import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from translation import ffmpeg_recipes as fr


class Recipes(unittest.TestCase):
    def test_encoder_profiles(self):
        x264 = fr.video_args("high")
        self.assertEqual(x264[x264.index("-crf") + 1], "18")
        self.assertIn("bt709", x264)
        nvenc = fr.video_args("standard", "h264_nvenc")
        self.assertEqual(nvenc[nvenc.index("-preset") + 1], "p5")
        self.assertIn("-spatial-aq", nvenc)
        self.assertEqual(fr.quality_profile("unknown"), fr.QUALITY["standard"])
        self.assertIn("+faststart", fr.delivery_args())

    def test_fit_frame_modes(self):
        self.assertIn("boxblur", fr.fit_frame(720, 1280))
        self.assertIn("pad=720:1280", fr.fit_frame(720, 1280, "black"))
        self.assertIn("scale=180:320", fr.fit_frame(721, 1281))

    def test_voice_chain_order(self):
        chain = fr.voice_chain(denoise=True)
        order = [chain.index(k) for k in ("highpass", "afftdn", "acompressor", "loudnorm", "aresample", "alimiter")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("afftdn", fr.voice_chain())

    def test_silence_parsing_and_keep_ranges(self):
        log = "silence_start: 1.0\nsilence_end: 2.5 | silence_duration: 1.5\nsilence_start: 9.0\n"
        silences = fr.parse_silences(log)
        self.assertEqual(silences, [(1.0, 2.5), (9.0, float("inf"))])
        keep = fr.keep_ranges(10.0, silences, margin=0.2, min_keep=0.25)
        self.assertEqual(keep, [(0.0, 1.2), (2.3, 9.2)])
        self.assertEqual(fr.keep_ranges(5.0, []), [(0.0, 5.0)])

    def test_remap_time_follows_cuts(self):
        keep = [(0.0, 1.0), (2.0, 3.0)]
        self.assertEqual(fr.remap_time(0.5, keep), 0.5)
        self.assertEqual(fr.remap_time(1.5, keep), 1.0)
        self.assertEqual(fr.remap_time(2.5, keep), 1.5)
        self.assertEqual(fr.remap_time(9.0, keep), 2.0)
        self.assertEqual(fr.remap_time(4.0, []), 4.0)

    def test_ken_burns_and_fades(self):
        graph = fr.ken_burns(720, 1280, 30, 90, "pan_up")
        self.assertIn("scale=2880:5120", graph)
        self.assertIn("d=90", graph)
        self.assertEqual(fr.fades(0.3), ("", ""))
        self.assertIn("fade=t=out:st=9.750", fr.fades(10)[0])

    def test_music_bed_ducks_under_voice(self):
        graph = fr.music_bed(30.0)
        self.assertIn("sidechaincompress", graph)
        self.assertIn("afade=t=out:st=28.500", graph)
        self.assertTrue(graph.endswith("[aout]"))
        self.assertNotIn("sidechaincompress", fr.music_bed(30.0, voice=False))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is not installed")
class RealRender(unittest.TestCase):
    def test_vertical_delivery_render(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "in.mp4", Path(folder) / "out.mp4"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc2=s=640x360:d=2:r=30",
                 "-f", "lavfi", "-i", "sine=f=300:d=2", "-shortest", str(source)],
                check=True,
            )
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                 "-vf", fr.fit_frame(360, 640) + "," + fr.fades(2)[0],
                 "-af", fr.voice_chain() + "," + fr.fades(2)[1],
                 *fr.delivery_args("draft"), str(target)],
                check=True,
            )
            probe = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target)],
                capture_output=True, text=True, check=True,
            ).stdout)["streams"]
            video = next(s for s in probe if s["codec_type"] == "video")
            audio = next(s for s in probe if s["codec_type"] == "audio")
            self.assertEqual((video["width"], video["height"]), (360, 640))
            self.assertEqual(video.get("color_primaries"), "bt709")
            self.assertEqual(audio["sample_rate"], "48000")

            music, mixed = Path(folder) / "bgm.m4a", Path(folder) / "mixed.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "sine=f=200:d=5", str(music)], check=True)
            fr.add_music(target, music, mixed)
            duration, has_audio = fr.probe_media(mixed)
            self.assertTrue(has_audio)
            self.assertAlmostEqual(duration, 2.0, delta=0.2)
            thumb = fr.thumbnail(mixed, Path(folder) / "thumb.jpg", width=180)
            self.assertGreater(thumb.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
