"""
voice_handler.py — Faster-Whisper transcription for Telegram voice messages.

Requirements:
  pip install faster-whisper   (already in requirements.txt)
  No separate ffmpeg needed — faster-whisper uses PyAV which bundles FFmpeg.

Model is downloaded automatically on first use (~150 MB for 'base').
Set WHISPER_MODEL=small in .env for slightly better accuracy at the cost of speed.
"""

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_MODEL      = None
_MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")   # tiny/base/small/medium


def _load_model():
    global _MODEL
    if _MODEL is None:
        try:
            from faster_whisper import WhisperModel
            _MODEL = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            logger.info("Whisper '%s' model ready", _MODEL_SIZE)
        except ImportError:
            logger.error(
                "faster-whisper not installed. Run: pip install faster-whisper"
            )
        except Exception as exc:
            logger.error("Whisper load failed: %s", exc)
    return _MODEL


def transcribe(audio_bytes: bytes, ext: str = ".ogg") -> str | None:
    """
    Transcribe audio bytes to Russian text.

    audio_bytes: raw file bytes (Telegram sends voice as .ogg/Opus)
    ext:         file extension hint so ffmpeg picks the right decoder
    Returns stripped text, or None on any error.
    """
    model = _load_model()
    if model is None:
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        segments, _ = model.transcribe(
            tmp_path,
            language   = "ru",
            beam_size  = 5,
            vad_filter = True,   # skip silent segments
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text or None

    except Exception as exc:
        logger.error("Transcription error: %s", exc)
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def warmup() -> None:
    """Pre-load model at startup so first voice message is instant."""
    _load_model()
