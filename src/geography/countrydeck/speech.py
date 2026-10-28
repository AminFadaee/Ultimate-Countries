import hashlib
import json
import logging
import pathlib
import urllib.request
from dataclasses import dataclass
from functools import cached_property

import numpy as np

from geography.detail import CACHE_DIR

logger = logging.getLogger(__name__)
VOICES_DIR = CACHE_DIR / "voices"
PIPER_REPOSITORY = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{}"
MANIFEST = "audio.json"
CLIPS = "audio"


@dataclass(frozen=True)
class Speech:
    engine: str
    voice: str
    language: str


SPEECH = {
    "nl": Speech("supertonic", "M1", "nl"),
    "fa": Speech("piper", "fa_IR-ganji-medium", "fa"),
}


def digest(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def clip_name(speech: Speech, text: str) -> str:
    return f"{speech.language}-{digest(f'{speech.engine}:{speech.voice}:{text}').hex()[:16]}.mp3"


def seed_for(text: str) -> int:
    return int.from_bytes(digest(text)[:4])


class SupertonicVoice:
    MODEL = "supertonic-3"

    def __init__(self, speech: Speech):
        self.speech = speech

    @cached_property
    def engine(self):
        from supertonic import TTS

        return TTS(model=self.MODEL)

    @cached_property
    def style(self):
        return self.engine.get_voice_style(voice_name=self.speech.voice)

    def say(self, text: str) -> tuple[np.ndarray, int]:
        supported = self.engine.model.text_processor.supported_character_set
        speakable = "".join(character for character in text if character in supported or character.isspace())
        np.random.seed(seed_for(text))
        samples, _ = self.engine.synthesize(speakable, voice_style=self.style, lang=self.speech.language)
        return np.asarray(samples).reshape(-1), self.engine.sample_rate


class PiperVoice:
    def __init__(self, speech: Speech):
        self.speech = speech

    @cached_property
    def voice(self):
        from piper import PiperVoice as Loaded

        return Loaded.load(str(self._model()))

    def _model(self) -> pathlib.Path:
        language, name, quality = self.speech.voice.split("-")
        folder = f"{language[:2]}/{language}/{name}/{quality}/{self.speech.voice}"
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx", ".onnx.json"):
            target = VOICES_DIR / f"{self.speech.voice}{suffix}"
            if not target.exists():
                urllib.request.urlretrieve(PIPER_REPOSITORY.format(folder + suffix), target)
        return VOICES_DIR / f"{self.speech.voice}.onnx"

    def say(self, text: str) -> tuple[np.ndarray, int]:
        np.random.seed(seed_for(text))
        chunks = list(self.voice.synthesize(text))
        return np.concatenate([chunk.audio_float_array for chunk in chunks]), chunks[0].sample_rate


ENGINES = {"supertonic": SupertonicVoice, "piper": PiperVoice}


def spoken_names(directory: pathlib.Path) -> list[str]:
    names: set[str] = set()
    for file in ("provinces.json", "cities.json"):
        names |= {item["native"] for item in json.loads((directory / file).read_text()) if item.get("native")}
    for path in (directory / "places" / "features").glob("*.json"):
        if native := json.loads(path.read_text()).get("native"):
            names.add(native)
    return sorted(names)


def build_audio(directory: pathlib.Path, language: str) -> int:
    import soundfile

    speech = SPEECH[language]
    voice = ENGINES[speech.engine](speech)
    clips = directory / CLIPS
    clips.mkdir(exist_ok=True)
    manifest = {}
    for text in spoken_names(directory):
        manifest[text] = clip_name(speech, text)
        path = clips / manifest[text]
        if not path.exists():
            samples, rate = voice.say(text)
            soundfile.write(path, samples, rate, format="MP3")
    for path in clips.glob("*.mp3"):
        if path.name not in manifest.values():
            path.unlink()
    (directory / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    logger.info("Spoke %d names with %s %s", len(manifest), speech.engine, speech.voice)
    return len(manifest)


def load_manifest(directory: pathlib.Path) -> dict[str, str]:
    path = directory / MANIFEST
    return json.loads(path.read_text()) if path.exists() else {}


def speak_country(code: str) -> int:
    directory = pathlib.Path("data/country_decks") / code
    language = json.loads((directory / "meta.json").read_text())["language"]
    if language not in SPEECH:
        raise SystemExit(f"No voice is configured for language {language!r}; add one to SPEECH in countrydeck/speech.py")
    return build_audio(directory, language)
