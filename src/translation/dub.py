"""Dubbing for client delivery: translated cues → voiced track → review → package.

Semi-automatic flow:
  1. python -m translation.dub OUTPUT
       Voices every translated cue, fits it into the speaker's time slot, mixes it
       over the original sound, and writes dub/review.tsv plus a draft video.
  2. A person checks dub/review.tsv (edit the dub_text column; rows marked 要確認
       are the ones that needed speeding up or ran late).
  3. python -m translation.dub OUTPUT --apply-review
       Re-voices only edited lines and writes the delivery package (delivery/).

Timing: a line plays at its original start. If its voice is longer than the gap
before the next line, it is sped up (at most MAX_TEMPO); if it still does not fit,
it starts after the previous line ends (never overlapping) and is flagged.
Background: the original audio stays underneath at a low level and ducks while
the dub speaks (voice-over style). With --separate and Demucs installed, the
original voice is removed and the background (music/effects) is kept at full level.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

from . import ffmpeg_recipes as fr

RATE = 48000
MAX_TEMPO = 1.3
GAP = 0.06  # silence kept between two dubbed lines
LANGUAGES = {
    "日本語": "ja-JP", "japanese": "ja-JP", "英語": "en-US", "english": "en-US",
    "中国語": "cmn-CN", "chinese": "cmn-CN", "韓国語": "ko-KR", "korean": "ko-KR",
    "スペイン語": "es-ES", "spanish": "es-ES", "フランス語": "fr-FR", "french": "fr-FR",
    "ドイツ語": "de-DE", "german": "de-DE", "ポルトガル語": "pt-BR", "portuguese": "pt-BR",
}
# Different speakers get different voices automatically (override with --voices).
VOICE_POOLS = {
    "ja-JP": ["ja-JP-Neural2-B", "ja-JP-Neural2-C", "ja-JP-Neural2-D"],
    "en-US": ["en-US-Neural2-D", "en-US-Neural2-F", "en-US-Neural2-J", "en-US-Neural2-C"],
    "cmn-CN": ["cmn-CN-Wavenet-A", "cmn-CN-Wavenet-B", "cmn-CN-Wavenet-C"],
    "ko-KR": ["ko-KR-Neural2-A", "ko-KR-Neural2-C", "ko-KR-Neural2-B"],
    "es-ES": ["es-ES-Neural2-A", "es-ES-Neural2-B"],
    "fr-FR": ["fr-FR-Neural2-A", "fr-FR-Neural2-B"],
    "de-DE": ["de-DE-Neural2-A", "de-DE-Neural2-B"],
    "pt-BR": ["pt-BR-Neural2-A", "pt-BR-Neural2-B"],
}
VOICEVOX_POOL = ["3", "2", "8", "13"]  # ずんだもん, 四国めたん, 春日部つむぎ, 青山龍星
VOICEVOX_NAMES = {"3": "ずんだもん", "2": "四国めたん", "8": "春日部つむぎ", "13": "青山龍星"}
REVIEW_COLUMNS = [
    "id", "start", "end", "speaker", "original", "subtitle", "dub_text", "voice",
    "voice_seconds", "slot_seconds", "tempo", "late_seconds", "check", "note",
]


class DubError(RuntimeError):
    pass


def language_code(name, override=None):
    if override:
        return override
    code = LANGUAGES.get(str(name).strip().lower()) or LANGUAGES.get(str(name).strip())
    if not code:
        raise DubError(f"吹き替えの言語コードが分かりません: {name}（--language ja-JP などで指定）")
    return code


def assign_voices(cues, language, engine, overrides=None):
    """speaker label -> voice, in order of first appearance."""
    pool = VOICEVOX_POOL if engine == "voicevox" else VOICE_POOLS.get(language)
    if not pool:
        raise DubError(f"{language} の声の候補がありません。--voices で指定してください")
    voices, overrides = {}, dict(overrides or {})
    for cue in cues:
        speaker = str(cue.get("speaker") or "default")
        if speaker not in voices:
            voices[speaker] = overrides.get(speaker) or overrides.get("default") or pool[len(voices) % len(pool)]
    return voices


# ---------------------------------------------------------------- engines
class GoogleTTS:
    """Cloud Text-to-Speech (REST) with the same Google credentials as translation."""

    def __init__(self, language, project, token, rate=1.0, opener=urllib.request.urlopen):
        self.language, self.project, self.token, self.rate, self.opener = language, project, token, rate, opener

    def key(self, text, voice):
        return ["google", self.language, voice, self.rate, text]

    def synthesize(self, text, voice, path):
        import base64

        body = {
            "input": {"text": text},
            "voice": {"languageCode": self.language, "name": voice},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": RATE, "speakingRate": self.rate},
        }
        request = urllib.request.Request(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={
                "Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json; charset=utf-8",
                "x-goog-user-project": self.project,
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=120) as reply:
                audio = json.loads(reply.read())["audioContent"]
        except urllib.error.HTTPError as error:
            raise DubError(f"音声合成に失敗しました（HTTP {error.code}）") from None
        Path(path).write_bytes(base64.b64decode(audio))


class Voicevox:
    """Local VOICEVOX engine (Japanese only). Credit: VOICEVOX:<character>."""

    def __init__(self, url="http://127.0.0.1:50021", speed=1.1, opener=urllib.request.urlopen):
        self.url, self.speed, self.opener = url.rstrip("/"), speed, opener

    def key(self, text, voice):
        return ["voicevox", voice, self.speed, text]

    def synthesize(self, text, voice, path):
        query_url = f"{self.url}/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": voice})
        with self.opener(urllib.request.Request(query_url, data=b"", method="POST"), timeout=60) as reply:
            query = json.loads(reply.read())
        query.update(speedScale=self.speed, outputSamplingRate=RATE, outputStereo=False)
        request = urllib.request.Request(
            f"{self.url}/synthesis?speaker={voice}",
            data=json.dumps(query).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener(request, timeout=120) as reply:
            Path(path).write_bytes(reply.read())


# ---------------------------------------------------------------- audio helpers
def read_wav(path):
    """Mono float32 samples at RATE (resampled with FFmpeg when needed)."""
    with wave.open(str(path), "rb") as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if rate != RATE or width != 2:
        converted = Path(path).with_suffix(".48k.wav")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(RATE),
             "-sample_fmt", "s16", str(converted)],
            check=True,
        )
        return read_wav(converted)
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples


def write_wav(path, samples):
    clipped = np.clip(samples, -1, 1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((clipped * 32767).astype("<i2").tobytes())


def stretch(samples, tempo, folder):
    """Speed up speech without changing pitch (FFmpeg atempo)."""
    if tempo <= 1.001:
        return samples
    source, target = folder / "stretch-in.wav", folder / "stretch-out.wav"
    write_wav(source, samples)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-af", f"atempo={tempo:.4f}", str(target)],
        check=True,
    )
    return read_wav(target)


def schedule(cues, durations, total):
    """Start time, tempo and lateness for every line; lines never overlap."""
    plan, previous_end = [], 0.0
    for index, (cue, seconds) in enumerate(zip(cues, durations)):
        following = cues[index + 1]["start"] if index + 1 < len(cues) else total
        slot = max(0.2, following - cue["start"] - GAP)
        tempo = min(MAX_TEMPO, seconds / slot) if seconds > slot else 1.0
        length = seconds / tempo
        start = max(cue["start"], previous_end + GAP if previous_end else 0.0)
        previous_end = start + length
        plan.append(
            {
                "start": round(start, 3),
                "tempo": round(tempo, 3),
                "length": round(length, 3),
                "slot": round(slot, 3),
                "late": round(start - cue["start"], 3),
            }
        )
    return plan


def needs_check(cue, row):
    reasons = []
    if not cue.get("translated", True):
        reasons.append("未翻訳")
    if row["tempo"] > 1.2:
        reasons.append(f"速め×{row['tempo']}")
    if row["late"] > 0.5:
        reasons.append(f"{row['late']}秒遅れ")
    return "・".join(reasons)


# ---------------------------------------------------------------- review sheet
def write_review(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _safe(row.get(k, "")) for k in REVIEW_COLUMNS})


def _safe(value):
    """Spreadsheet apps must not run a cell as a formula."""
    return "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value


def read_review(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    edits = {}
    for row in rows:
        text = (row.get("dub_text") or "").strip()
        if text.startswith("'"):
            text = text[1:]
        if row.get("id"):
            edits[row["id"]] = {"dub_text": text, "voice": (row.get("voice") or "").strip()}
    return edits


# ---------------------------------------------------------------- main build
def build_track(cues, texts, voices, engine, folder, total, log=print):
    """Voice every line (cached by text+voice), fit timing, return (track, rows)."""
    cache = folder / "voice-cache"
    cache.mkdir(parents=True, exist_ok=True)
    clips, durations = [], []
    for cue in cues:
        text = texts[cue["id"]]
        voice = voices[cue["id"]]
        if not text:
            clips.append(np.zeros(0, np.float32))
            durations.append(0.0)
            continue
        name = hashlib.sha256(json.dumps(engine.key(text, voice), ensure_ascii=False).encode()).hexdigest()[:24]
        path = cache / f"{name}.wav"
        if not path.is_file():
            engine.synthesize(text, voice, path)
        samples = read_wav(path)
        clips.append(samples)
        durations.append(len(samples) / RATE)
    plan = schedule(cues, durations, total)
    end = max([total] + [p["start"] + p["length"] for p in plan])
    track = np.zeros(int(end * RATE) + RATE, np.float32)
    rows = []
    for cue, samples, timing in zip(cues, clips, plan):
        if len(samples):
            fitted = stretch(samples, timing["tempo"], folder)
            at = int(timing["start"] * RATE)
            track[at : at + len(fitted)] += fitted
        row = {
            "id": cue["id"],
            "start": cue["start"],
            "end": cue["end"],
            "speaker": cue.get("speaker") or "",
            "original": cue["text"],
            "subtitle": cue.get("translation", ""),
            "dub_text": texts[cue["id"]],
            "voice": voices[cue["id"]],
            "voice_seconds": round(len(samples) / RATE, 2),
            "slot_seconds": timing["slot"],
            "tempo": timing["tempo"],
            "late_seconds": timing["late"],
        }
        row["check"] = "要確認" if needs_check(cue, timing) else ""
        row["note"] = needs_check(cue, timing)
        rows.append(row)
    return track[: int(end * RATE)], rows


def separate_background(audio, folder, log=print):
    """Optional: remove the original voice with Demucs (two stems). None if unavailable."""
    if shutil.which("demucs") is None:
        try:
            import demucs  # noqa: F401
        except ImportError:
            log("[dub] Demucs が無いので、元音声を小さく残す方式で合成します")
            return None
    subprocess.run(
        [sys.executable, "-m", "demucs", "--two-stems=vocals", "-o", str(folder / "separated"), str(audio)],
        check=True,
    )
    found = sorted((folder / "separated").glob("*/*/no_vocals.wav"))
    return found[0] if found else None


def mix_and_mux(video, start, duration, dub_wav, output, *, background=None, bed=0.22, subtitles=None,
                quality="standard", encoder="libx264", language="ja-JP"):
    """Dubbed mix as the first audio track, the untouched original as the second."""
    lang3 = {"ja": "jpn", "en": "eng", "cmn": "chi", "ko": "kor", "es": "spa", "fr": "fre", "de": "ger", "pt": "por"}
    tag = lang3.get(language.split("-")[0], "und")
    inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video), "-i", str(dub_wav)]
    _, has_audio = fr.probe_media(video)
    if not background and not has_audio:
        # Silent source: the dub is the whole soundtrack.
        bed, background = 0, None
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        bg = "[2:a]anull[bg]"
    elif background:
        inputs += ["-i", str(background)]
        bg = "[2:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.0[bg]"
    else:
        bg = f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={bed}[bg]"
    graph = (
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[dub][key];{bg};"
        "[bg][key]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=350[ducked];"
        "[dub][ducked]amix=inputs=2:duration=first:normalize=0,"
        f"{fr.voice_chain(compress=False).replace('highpass=f=70,', '')}[mix]"
    )
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", graph]
    if subtitles:
        command += ["-map", "0:v:0", "-vf", f"ass={Path(subtitles).name}", *fr.video_args(quality, encoder)]
    else:
        command += ["-map", "0:v:0", *fr.video_args(quality, encoder)]
    command += [
        "-map", "[mix]", *(["-map", "0:a:0"] if has_audio else []),
        *fr.audio_args(),
        "-metadata:s:a:0", f"language={tag}", "-metadata:s:a:0", "title=Dub",
        "-metadata:s:a:1", "title=Original",
        "-disposition:a:0", "default", "-disposition:a:1", "0",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True, cwd=str(Path(subtitles).parent) if subtitles else None)
    return output


def shorten(rows, client, model, language, log=print):
    """Ask Gemini to condense lines that ran long; meaning, numbers and negation stay."""
    targets = [r for r in rows if r["tempo"] > 1.2 or r["late_seconds"] > 0.5]
    if not targets:
        return {}
    request = [
        {"id": r["id"], "text": r["dub_text"], "ratio": round(min(1.0, r["slot_seconds"] / max(r["voice_seconds"], 0.1)), 2)}
        for r in targets
    ]
    prompt = (
        f"次の吹き替え用の{language}のせりふを、話す時間が ratio 倍程度になるよう短くしてください。"
        "意味・数字・単位・否定・条件・固有名詞・話者の口調は変えない。情報を足さない。"
        "短くできないものは元の文のまま返す。JSON {\"segments\":[{\"id\":...,\"text\":...}]} だけを返す。\n"
        + json.dumps(request, ensure_ascii=False)
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "LOW"}},
    }
    response = client.generate(model, body, {"operation": "dub-shorten", "request": request})
    try:
        text = "".join(p.get("text", "") for p in response["candidates"][0]["content"]["parts"] if not p.get("thought"))
        segments = json.loads(text).get("segments", [])
    except (KeyError, IndexError, TypeError, ValueError):
        log("[dub] 短縮案を読み取れませんでした。元のせりふで続けます")
        return {}
    wanted = {r["id"] for r in targets}
    return {s["id"]: s["text"].strip() for s in segments if isinstance(s, dict) and s.get("id") in wanted and str(s.get("text", "")).strip()}


def run(output, *, engine_name="google", language=None, voices=None, apply_review=False, separate=False,
        burn_subtitles=False, shorten_long=False, bed=0.22, video=None, engine=None, project=None, log=print):
    from .backend import Vertex
    from .export import export_saved

    output = Path(output).expanduser().resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    data = json.loads((output / "translation.json").read_text(encoding="utf-8"))
    cues = sorted([c for c in data["cues"] if c["end"] > c["start"]], key=lambda c: c["start"])
    if not cues:
        raise DubError("吹き替える字幕がありません")
    video = Path(video or manifest.get("source_video") or output / "source.mp4")
    if not video.is_file():
        raise DubError(f"元動画が見つかりません: {video}（--video で指定）")
    language = language_code(manifest.get("target_language", ""), language)
    folder = output / "dub"
    folder.mkdir(exist_ok=True)
    settings_path = folder / "settings.json"
    saved = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    by_speaker = assign_voices(cues, language, engine_name, voices or saved.get("voices"))
    texts = {c["id"]: (c.get("translation") or c["text"]).strip() for c in cues}
    line_voices = {c["id"]: by_speaker[str(c.get("speaker") or "default")] for c in cues}
    review = folder / "review.tsv"
    if apply_review:
        if not review.is_file():
            raise DubError("dub/review.tsv がありません。先に下書きを作ってください")
        for cue_id, edit in read_review(review).items():
            if cue_id in texts:
                texts[cue_id] = edit["dub_text"]
                if edit["voice"]:
                    line_voices[cue_id] = edit["voice"]
    if engine is None:
        if engine_name == "voicevox":
            if not language.startswith("ja"):
                raise DubError("VOICEVOX は日本語だけです")
            engine = Voicevox(os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021"))
        else:
            vertex = Vertex(project or manifest.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT", ""), output / "cache")
            engine = GoogleTTS(language, vertex.project, vertex.token)
    total = float(manifest["duration_seconds"])
    track, rows = build_track(cues, texts, line_voices, engine, folder, total, log)
    if shorten_long and not apply_review:
        vertex = Vertex(project or manifest.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT", ""), output / "cache")
        proposals = shorten(rows, vertex, manifest.get("translation_model", "gemini-3.5-flash"), language, log)
        if proposals:
            texts.update(proposals)
            track, rows = build_track(cues, texts, line_voices, engine, folder, total, log)
            for row in rows:
                if row["id"] in proposals:
                    row["note"] = ("短縮案・" + row["note"]).rstrip("・")
    dub_wav = folder / "dub-voice.wav"
    write_wav(dub_wav, track)
    write_review(review, rows)
    settings_path.write_text(json.dumps({"engine": engine_name, "language": language, "voices": by_speaker},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    background = separate_background(video, folder, log) if separate else None
    subtitles = output / "subtitles.ass" if burn_subtitles else None
    target = folder / ("dubbed.mp4" if apply_review else "dubbed-draft.mp4")
    mix_and_mux(video, manifest.get("start_seconds", 0.0), total, dub_wav, target, background=background,
                bed=bed, subtitles=subtitles, language=language)
    flagged = sum(1 for r in rows if r["check"])
    report = {
        "status": "delivered" if apply_review else "draft",
        "language": language,
        "engine": engine_name,
        "voices": by_speaker,
        "lines": len(rows),
        "lines_to_check": flagged,
        "max_late_seconds": max(r["late_seconds"] for r in rows),
        "background": "separated" if background else f"original at {bed:g} (ducked)",
        "video": str(target),
    }
    if engine_name == "voicevox":
        report["credit"] = " / ".join(f"VOICEVOX:{VOICEVOX_NAMES.get(v, v)}" for v in sorted(set(by_speaker.values())))
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if apply_review:
        package = output / "delivery"
        if package.exists():
            shutil.rmtree(package)
        package.mkdir()
        shutil.copy2(target, package / "dubbed.mp4")
        export_saved(output, package / "subtitles")
        shutil.copy2(review, package / "dub-script.tsv")
        shutil.copy2(folder / "report.json", package / "report.json")
        notes = [
            "納品物",
            "- dubbed.mp4: 吹き替え音声（第1音声）と元の音声（第2音声）",
            "- subtitles/: 字幕（SRT・ASS・VTT・対訳）と書き起こし",
            "- dub-script.tsv: 吹き替え台本（時刻・話者・原文・訳文）",
        ]
        if report.get("credit"):
            notes.append(f"- 音声クレジット（概要欄などに記載してください）: {report['credit']}")
        (package / "README.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
        report["delivery"] = str(package)
    log(f"[dub] {'納品物' if apply_review else '下書き'}: {target}（要確認 {flagged} 行）")
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="翻訳済みの字幕から吹き替え動画を作る（下書き→確認→納品）")
    p.add_argument("output", type=Path, help="translation の出力フォルダ")
    p.add_argument("--engine", choices=["google", "voicevox"], default="google")
    p.add_argument("--language", help="BCP-47（例 ja-JP, en-US）。省略時は翻訳先の言語から決める")
    p.add_argument("--voices", help='話者ごとの声 JSON 例: {"speaker_1":"ja-JP-Neural2-C"}')
    p.add_argument("--apply-review", action="store_true", help="dub/review.tsv の修正を反映して納品物を作る")
    p.add_argument("--shorten", action="store_true", help="長すぎるせりふの短縮案を Gemini に作らせる")
    p.add_argument("--separate", action="store_true", help="Demucs で元の声を除き、背景音だけ残す")
    p.add_argument("--burn-subtitles", action="store_true", help="字幕も焼き込む")
    p.add_argument("--bed", type=float, default=0.22, help="元音声を残す大きさ（0〜1、分離しない場合）")
    p.add_argument("--video", type=Path, help="元動画（manifest に無い場合）")
    p.add_argument("--project", help="Google Cloud project ID（Google の声・短縮案に使う）")
    args = p.parse_args(argv)
    try:
        report = run(
            args.output, engine_name=args.engine, language=args.language,
            voices=json.loads(args.voices) if args.voices else None, apply_review=args.apply_review,
            separate=args.separate, burn_subtitles=args.burn_subtitles, shorten_long=args.shorten,
            bed=args.bed, video=args.video, project=args.project,
        )
    except (DubError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"吹き替えに失敗しました: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
