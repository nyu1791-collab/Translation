"""High-quality FFmpeg recipes shared by the video repositories.

Pure functions that return FFmpeg arguments or filter strings, so every render
uses the same encoder tuning, vertical framing, voice chain and silence cuts.
Ideas follow well-known open-source tools (auto-editor's silence margins,
ffmpeg-normalize's loudness targets, common NVENC/x264 delivery settings); the
code is written here. Targets are guidance, not gates: nothing re-measures or
re-renders when a result lands slightly off.
"""

import re
import subprocess

# draft: fastest preview. standard: delivery default. high: archive/final.
QUALITY = {
    "draft": {"preset": "veryfast", "crf": 23, "nvenc": "p3", "cq": 25},
    "standard": {"preset": "medium", "crf": 20, "nvenc": "p5", "cq": 21},
    "high": {"preset": "slow", "crf": 18, "nvenc": "p7", "cq": 19},
}
# Tag SDR output as BT.709 so players do not guess (avoids washed-out colours).
BT709 = [
    "-colorspace", "bt709", "-color_primaries", "bt709",
    "-color_trc", "bt709", "-color_range", "tv",
]
# Short-form platforms normalise to about -14 LUFS; one pass, no re-measure.
LOUDNESS = "I=-14:TP=-1.5:LRA=11"


def quality_profile(name):
    return QUALITY.get(name or "standard", QUALITY["standard"])


def video_args(quality="standard", encoder="libx264"):
    """Encoder arguments: x264 CRF or NVENC constant-quality VBR with AQ."""
    q = quality_profile(quality)
    if encoder == "h264_nvenc":
        codec = [
            "-c:v", "h264_nvenc", "-preset", q["nvenc"], "-tune", "hq",
            "-rc", "vbr", "-cq", str(q["cq"]), "-b:v", "0",
            "-spatial-aq", "1", "-rc-lookahead", "20",
        ]
    else:
        codec = ["-c:v", "libx264", "-preset", q["preset"], "-crf", str(q["crf"])]
    return [*codec, "-profile:v", "high", "-pix_fmt", "yuv420p", *BT709]


def audio_args(bitrate="192k", rate=48000):
    return ["-c:a", "aac", "-b:a", bitrate, "-ar", str(rate)]


def delivery_args(quality="standard", encoder="libx264", audio_bitrate="192k"):
    """Video + audio + web-ready container (moov atom first for instant playback)."""
    return [*video_args(quality, encoder), *audio_args(audio_bitrate), "-movflags", "+faststart"]


def _even(value):
    return max(2, int(value) // 2 * 2)


def fit_frame(width, height, background="blur", sharpen=True):
    """Fit any source into WxH. 'blur' fills bars with a blurred copy (vertical shorts look);
    'black' pads. The graph has one input and one output, so it works inside -vf."""
    width, height = _even(width), _even(height)
    fg = f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos"
    if sharpen:
        fg += ",unsharp=5:5:0.4:5:5:0.0"
    if background != "blur":
        return f"{fg},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    # Blur at quarter size, then scale up: same look as a full-size blur, much faster.
    sw, sh = _even(width / 4), _even(height / 4)
    return (
        f"split=2[cfbg][cffg];"
        f"[cfbg]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"boxblur=10:2,scale={width}:{height},eq=brightness=-0.06:saturation=1.1[cfb];"
        f"[cffg]{fg}[cff];"
        f"[cfb][cff]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def voice_chain(denoise=False, compress=True, loudness=LOUDNESS, rate=48000):
    """Speech polish: rumble cut, optional FFT denoise, gentle compression,
    one-pass loudness, resample back from loudnorm's 192 kHz, and a peak limiter."""
    parts = ["highpass=f=70"]
    if denoise:
        parts.append("afftdn=nf=-25")
    if compress:
        parts.append("acompressor=threshold=-20dB:ratio=2.5:attack=5:release=120:makeup=1.5")
    parts += [f"loudnorm={loudness}", f"aresample={rate}", "alimiter=limit=0.89:level=disabled"]
    return ",".join(parts)


def ken_burns(width, height, fps, frames, motion="zoom_in", amount=0.12, oversample=4):
    """Smooth still-image motion. Oversampling (4x default; 2x for drafts) before
    zoompan removes the stair-step jitter zoompan shows at native size."""
    width, height = _even(width), _even(height)
    frames = max(1, int(frames))
    zoom, y = "1", "ih/2-ih/zoom/2"
    if motion == "zoom_in":
        zoom = f"1+{amount}*on/{frames}"
    elif motion == "zoom_out":
        zoom = f"max({1 + amount}-{amount}*on/{frames},1)"
    elif motion == "pan_down":
        zoom, y = f"{1 + amount}", f"(ih-ih/zoom)*on/{frames}"
    elif motion == "pan_up":
        zoom, y = f"{1 + amount}", f"(ih-ih/zoom)*(1-on/{frames})"
    big_w, big_h = width * oversample, height * oversample
    return (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='{y}':d={frames}:s={width}x{height}:fps={fps}"
    )


def fades(duration, video=0.25, audio=0.15):
    """Short in/out fades so cuts never click or flash."""
    v, a = [], []
    if duration > 2 * video > 0:
        v = [f"fade=t=in:st=0:d={video}", f"fade=t=out:st={duration - video:.3f}:d={video}"]
    if duration > 2 * audio > 0:
        a = [f"afade=t=in:st=0:d={audio}", f"afade=t=out:st={duration - audio:.3f}:d={audio}"]
    return ",".join(v), ",".join(a)


_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def parse_silences(log):
    """(start, end) silences from silencedetect output; an open silence runs to the end."""
    found, start = [], None
    for kind, value in _SILENCE.findall(log):
        if kind == "start":
            start = float(value)
        elif start is not None:
            found.append((max(0.0, start), float(value)))
            start = None
    if start is not None:
        found.append((max(0.0, start), float("inf")))
    return found


def keep_ranges(duration, silences, margin=0.15, min_keep=0.25):
    """Speech ranges to keep (auto-editor style): silences shrink by a margin on
    each side so words are not clipped, and tiny islands are dropped."""
    keep, cursor = [], 0.0
    for start, end in sorted(silences):
        cut_start, cut_end = start + margin, min(end, duration) - margin
        if cut_end <= cut_start:
            continue
        if cut_start - cursor >= min_keep:
            keep.append((round(cursor, 3), round(cut_start, 3)))
        cursor = max(cursor, cut_end)
    if duration - cursor >= min_keep:
        keep.append((round(cursor, 3), round(duration, 3)))
    return keep


def detect_silences(path, noise="-35dB", minimum=0.6, start=None, duration=None):
    """Run silencedetect once over the audio only (fast; no video decode)."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-vn"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-af", f"silencedetect=noise={noise}:d={minimum}", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return parse_silences(result.stderr)


def jump_cut_filter(ranges):
    """select/aselect expressions that keep only the given ranges and close the gaps."""
    expr = "+".join(f"between(t,{a},{b})" for a, b in ranges) or "1"
    return (
        f"select='{expr}',setpts=N/FRAME_RATE/TB",
        f"aselect='{expr}',asetpts=N/SR/TB",
    )


def remap_time(t, ranges):
    """Original time -> time after jump cuts (for moving captions with the cut)."""
    if not ranges:
        return t
    removed, previous_end = 0.0, 0.0
    for a, b in ranges:
        removed += a - previous_end
        if t < a:
            return max(0.0, a - removed)
        if t <= b:
            return t - removed
        previous_end = b
    return previous_end - removed


def scene_changes(path, threshold=0.3):
    """Shot boundaries (seconds) via the scene score; useful for cut points/thumbnails."""
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-an",
        "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [float(v) for v in re.findall(r"pts_time:([\d.]+)", result.stderr)]
