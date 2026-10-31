import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from translation import dub

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


class FakeEngine:
    """0.12 s of tone per character: long text = long voice."""

    def __init__(self):
        self.calls = []

    def key(self, text, voice):
        return ["fake", voice, text]

    def synthesize(self, text, voice, path):
        self.calls.append((text, voice))
        seconds = 0.12 * len(text)
        t = np.arange(int(seconds * dub.RATE)) / dub.RATE
        dub.write_wav(path, 0.3 * np.sin(2 * np.pi * 220 * t).astype(np.float32))


def cue(i, start, end, text, speaker="speaker_1", translated=True):
    return {"id": f"c{i}", "start": start, "end": end, "text": f"src {i}", "translation": text,
            "translated": translated, "speaker": speaker}


def test_language_codes_and_voice_pools():
    assert dub.language_code("日本語") == "ja-JP"
    assert dub.language_code("English") == "en-US"
    assert dub.language_code("whatever", "fr-FR") == "fr-FR"
    with pytest.raises(dub.DubError):
        dub.language_code("Klingon")
    cues = [cue(1, 0, 1, "a", "A"), cue(2, 1, 2, "b", "B"), cue(3, 2, 3, "c", "A")]
    voices = dub.assign_voices(cues, "ja-JP", "google")
    assert voices["A"] != voices["B"]
    assert dub.assign_voices(cues, "ja-JP", "google", {"B": "custom"})["B"] == "custom"


def test_schedule_speeds_up_then_delays_without_overlap():
    cues = [cue(1, 0.0, 1.0, "x"), cue(2, 1.0, 2.0, "y"), cue(3, 5.0, 6.0, "z")]
    plan = dub.schedule(cues, [0.5, 5.0, 1.0], total=8.0)
    assert plan[0]["tempo"] == 1.0  # fits its slot
    assert 1.2 < plan[1]["tempo"] <= dub.MAX_TEMPO  # 5 s into a 3.94 s slot
    for previous, current in zip(plan, plan[1:]):
        assert current["start"] >= previous["start"] + previous["length"]
    crowded = dub.schedule([cue(1, 0, 1, "x"), cue(2, 1, 2, "y")], [3.0, 1.0], total=2.0)
    assert crowded[0]["tempo"] == dub.MAX_TEMPO and crowded[1]["late"] > 0.5


def test_review_sheet_round_trip_and_formula_guard(tmp_path):
    rows = [{"id": "c1", "dub_text": "=SUM(1)", "voice": "v1", "original": "@x"}]
    dub.write_review(tmp_path / "r.tsv", rows)
    raw = (tmp_path / "r.tsv").read_text(encoding="utf-8-sig")
    assert "'=SUM(1)" in raw and "'@x" in raw
    assert dub.read_review(tmp_path / "r.tsv") == {"c1": {"dub_text": "=SUM(1)", "voice": "v1"}}


def test_build_track_places_lines_and_flags_problems(tmp_path):
    engine = FakeEngine()
    cues = [cue(1, 0.0, 1.0, "短い"), cue(2, 1.0, 1.5, "とても長いせりふでここには収まりません"), cue(3, 2.0, 3.0, "未訳", translated=False)]
    texts = {c["id"]: c["translation"] for c in cues}
    voices = {c["id"]: "v" for c in cues}
    track, rows = dub.build_track(cues, texts, voices, engine, tmp_path, total=6.0)
    assert len(track) >= 6 * dub.RATE
    assert rows[1]["check"] == "要確認" and "速め" in rows[1]["note"]
    assert "未翻訳" in rows[2]["note"]
    # cached: a second build does not voice anything again
    dub.build_track(cues, texts, voices, engine, tmp_path, total=6.0)
    assert len(engine.calls) == 3


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required")
def test_draft_then_review_then_delivery(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=6:r=25",
         "-f", "lavfi", "-i", "sine=f=500:d=6", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(source)],
        check=True,
    )
    cues = [cue(1, 0.5, 2.0, "こんにちは", "A"), cue(2, 2.5, 4.0, "元気ですか", "B")]
    (tmp_path / "translation.json").write_text(json.dumps({"cues": cues}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"duration_seconds": 6.0, "start_seconds": 0.0, "target_language": "日本語",
                    "source_video": str(source), "cue_count": 2}),
        encoding="utf-8",
    )
    engine = FakeEngine()
    draft = dub.run(tmp_path, engine=engine, log=lambda *a: None)
    assert draft["status"] == "draft" and draft["lines"] == 2
    assert draft["voices"]["A"] != draft["voices"]["B"]
    review = tmp_path / "dub" / "review.tsv"
    text = review.read_text(encoding="utf-8-sig").replace("元気ですか", "お元気ですか")
    review.write_text(text, encoding="utf-8-sig")
    final = dub.run(tmp_path, engine=engine, apply_review=True, log=lambda *a: None)
    assert [c[0] for c in engine.calls] == ["こんにちは", "元気ですか", "お元気ですか"]  # only the edit is re-voiced
    package = Path(final["delivery"])
    assert {"dubbed.mp4", "dub-script.tsv", "report.json", "README.txt"} <= {p.name for p in package.iterdir()}
    assert (package / "subtitles" / "subtitles.srt").is_file()
    streams = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(package / "dubbed.mp4")],
        capture_output=True, text=True, check=True).stdout)["streams"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    assert len(audio) == 2 and audio[0]["tags"].get("language") == "jpn"
    assert audio[0]["disposition"]["default"] == 1


def test_voicevox_is_japanese_only(tmp_path):
    (tmp_path / "translation.json").write_text(json.dumps({"cues": [cue(1, 0, 1, "hi")]}), encoding="utf-8")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"duration_seconds": 1, "target_language": "English", "source_video": str(video)}),
        encoding="utf-8",
    )
    with pytest.raises(dub.DubError, match="日本語"):
        dub.run(tmp_path, engine_name="voicevox", log=lambda *a: None)


def test_wav_helpers_round_trip(tmp_path):
    samples = np.linspace(-0.5, 0.5, dub.RATE, dtype=np.float32)
    dub.write_wav(tmp_path / "a.wav", samples)
    with wave.open(str(tmp_path / "a.wav")) as handle:
        assert handle.getframerate() == dub.RATE
    back = dub.read_wav(tmp_path / "a.wav")
    assert np.allclose(back, samples, atol=1e-3)
