# SPDX-License-Identifier: Apache-2.0
# Vendored unchanged from Fish Audio official Drama-3 Space (Apache-2.0 Space metadata).
# Source: https://huggingface.co/spaces/fishaudio/Drama-3/raw/c368729ab9347dda6635c5272ad31e6521114b79/drama3_space_ext.py
# Upstream SHA256: df7e7c1e497b4488b69eef6b35c62370d3281277a2b8cd1c7423de8341947aa7
# Adds author-described-character prompt layout; no external I/O or secret reads.
"""Drama 3 prompt layouts that SGLang Omni's /v1/audio/speech cannot express.

The stock Drama 3 preprocessor builds one layout: optional voice references in the
system turn, then a single user turn (Fish Audio Drama 3 Technical Report, Figure 11).
Drama 3 was also trained on two layouts this Space uses:

- casting a voice from a text description, with no reference audio (Figure 13), and
- multi-turn directing: a note in a later user turn revises the take the model just
  produced, which sits in the prompt as an earlier assistant turn (Figure 12).

Drama3SpacePreprocessor reads a JSON envelope from the request text and builds those
layouts. Requests without the envelope take the stock path unchanged. The engine
listens on localhost only, so the Space's web server is the only client that can send
an envelope.

SGLang Omni declares each stage's factory in the model's config class and refuses to
override it from the command line. The Dockerfile instead appends one line to
sglang_omni/models/fishaudio_drama3/stages.py that rebinds the name its preprocessing
factory builds, Drama3SpeechPreprocessor, to this class.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Mapping, Sequence

import torch

from sglang_omni.models.fishaudio_drama3.prompt import (
    GENERATION_START,
    MESSAGE_END,
    REFERENCE_INSTRUCTION,
    SEMANTIC_BEGIN_TOKEN,
    SPEAKER_TAG_PATTERN,
    TTS_INSTRUCTION,
    UNICODE_WHITE_SPACE,
    validate_caller_text,
    with_speaker_tag,
)
from sglang_omni.models.fishaudio_drama3.request_builders import REQUEST_ERROR_PREFIX
from sglang_omni.models.fishaudio_drama3.speech_request import (
    Drama3SpeechPreprocessor,
    speech_inputs,
)
from sglang_omni.proto.request import OmniRequest, StagePayload

logger = logging.getLogger(__name__)

ENVELOPE_MARKER = "@@drama3-space@@"
# The scene-dubbing system instruction of Figure 13. The engine samples only audio
# and end-of-speech tokens, so a request carries one line and never needs the
# separator; the instruction keeps the layout the model was trained on.
SCENE_INSTRUCTION = (
    "Voice every line of the script in order, with <|audio_separator|> "
    "between consecutive lines."
)


@dataclasses.dataclass(frozen=True)
class Character:
    name: str
    description: str
    scene: str | None


@dataclasses.dataclass(frozen=True)
class Envelope:
    """One request: the line to speak now and how its prompt is laid out.

    The request's references hold voice_references voice clips first, then one
    clip per history turn, in order.
    """

    text: str
    voice_references: int
    history: tuple[str, ...]
    character: Character | None


def request_error(message: str) -> ValueError:
    return ValueError(f"{REQUEST_ERROR_PREFIX} {message}")


def single_line(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip(UNICODE_WHITE_SPACE):
        raise request_error(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise request_error(f"{field_name} must be a single line")
    validate_caller_text(value, field_name)
    return value.strip(UNICODE_WHITE_SPACE)


def parse_envelope(body: str, reference_count: int) -> Envelope:
    try:
        spec = json.loads(body)
    except json.JSONDecodeError as error:
        raise request_error(f"malformed envelope: {error}") from error
    if not isinstance(spec, Mapping):
        raise request_error("the envelope must be a JSON object")

    text = spec.get("text")
    if not isinstance(text, str):
        raise request_error("envelope text must be a string")
    validate_caller_text(text, "text")

    history = spec.get("history") or []
    if not isinstance(history, list) or not all(isinstance(t, str) for t in history):
        raise request_error("envelope history must be a list of strings")
    for index, turn_text in enumerate(history):
        validate_caller_text(turn_text, f"history.{index}")

    voice_references = spec.get("voice_references", 0)
    if (
        isinstance(voice_references, bool)
        or not isinstance(voice_references, int)
        or voice_references < 0
        or voice_references + len(history) != reference_count
    ):
        raise request_error(
            f"voice_references {voice_references!r} and {len(history)} history "
            f"turns do not account for {reference_count} references"
        )

    character = None
    raw_character = spec.get("character")
    if raw_character is not None:
        if not isinstance(raw_character, Mapping):
            raise request_error("envelope character must be an object")
        if voice_references:
            raise request_error("a described character cannot also have voice references")
        name = single_line(raw_character.get("name"), "character.name")
        if ":" in name:
            raise request_error("character.name must not contain ':'")
        character = Character(
            name=name,
            description=single_line(
                raw_character.get("description"), "character.description"
            ),
            scene=single_line(raw_character.get("scene"), "character.scene", optional=True),
        )

    return Envelope(
        text=text,
        voice_references=voice_references,
        history=tuple(history),
        character=character,
    )


def reference_text_block(transcript: str) -> str:
    """The stock reference transcript: the speaker tag follows "Text: " directly."""
    tagged = with_speaker_tag(transcript, 0)
    body = tagged.lstrip(UNICODE_WHITE_SPACE)
    tag = SPEAKER_TAG_PATTERN.match(body)
    assert tag is not None
    leading = tagged[: len(tagged) - len(body)]
    return tag.group(0) + leading + body[tag.end() :]


class Drama3SpacePreprocessor(Drama3SpeechPreprocessor):
    """The stock preprocessor, plus described characters and directing history."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        # server.py looks for this line to confirm the stages.py hook took effect.
        logger.warning("Drama 3 Space preprocessor active")

    def __call__(self, payload: StagePayload) -> StagePayload:
        text, descriptors = speech_inputs(payload.request.inputs)
        if not text.startswith(ENVELOPE_MARKER):
            return super().__call__(payload)
        envelope = parse_envelope(text[len(ENVELOPE_MARKER) :], len(descriptors))

        # The stock path checks the sampling fields and the line itself; the rows it
        # builds for that reference-free probe are replaced below.
        probe = StagePayload(
            request_id=payload.request_id,
            request=OmniRequest(
                inputs=self.turn_text(envelope.text, envelope.character),
                params=payload.request.params,
                metadata=payload.request.metadata,
            ),
            data=None,
        )
        checked = super().__call__(probe)

        codes = [
            self.reference_audio_codes(descriptor, index)
            for index, descriptor in enumerate(descriptors)
        ]
        voice_codes = codes[: envelope.voice_references]
        history_codes = codes[envelope.voice_references :]
        transcripts = []
        for index, descriptor in enumerate(descriptors[: envelope.voice_references]):
            transcript = descriptor.get("text")
            if not isinstance(transcript, str):
                raise request_error(f"references.{index}.text must be a string")
            validate_caller_text(transcript, f"references.{index}")
            transcripts.append(transcript)

        segments: list[str | torch.Tensor] = ["<|im_start|>system\n"]
        if envelope.character is not None:
            character = envelope.character
            lines = [f"Scene: {character.scene}"] if character.scene else []
            lines += [
                "Characters:",
                f"- {character.name}: {character.description}",
                SCENE_INSTRUCTION,
            ]
            segments.append("\n".join(lines))
        else:
            segments.append(REFERENCE_INSTRUCTION if voice_codes else TTS_INSTRUCTION)
            for index, (transcript, reference) in enumerate(
                zip(transcripts, voice_codes, strict=True), 1
            ):
                segments += [
                    f"\n\n[Reference {index}]\nText: "
                    f"{reference_text_block(transcript)}\nSpeech: ",
                    reference,
                ]
        segments.append(MESSAGE_END)
        for turn_text, take in zip(envelope.history, history_codes, strict=True):
            segments += [
                "<|im_start|>user\n",
                self.turn_text(turn_text, envelope.character),
                MESSAGE_END,
                GENERATION_START,
                take,
                MESSAGE_END,
            ]
        segments += [
            "<|im_start|>user\n",
            self.turn_text(envelope.text, envelope.character),
            MESSAGE_END,
            GENERATION_START,
        ]

        rows = self.segment_rows(segments)
        if rows.shape[0] > self.max_prompt_rows:
            raise request_error(
                f"a prompt of {rows.shape[0]} rows exceeds the maximum allowed "
                f"length of {self.max_prompt_rows} rows"
            )
        return StagePayload(
            request_id=payload.request_id,
            request=payload.request,
            data=dataclasses.replace(checked.data, prompt_rows=rows),
        )

    @staticmethod
    def turn_text(text: str, character: Character | None) -> str:
        if character is not None:
            return f"{character.name}: {text}"
        return with_speaker_tag(text, 0)

    def segment_rows(self, segments: Sequence[str | torch.Tensor]) -> torch.Tensor:
        """Text rows are [token, 0, ...]; audio rows [c0 + semantic_begin, c0..cN-1].

        Every literal is tokenized on its own, as in the stock builder: tokenizing
        the joined string changes ids at the seams.
        """
        semantic_begin_id = self.tokenizer.token_to_id(SEMANTIC_BEGIN_TOKEN)
        rows: list[torch.Tensor] = []
        for segment in segments:
            if isinstance(segment, str):
                token_ids = torch.tensor(
                    self.tokenizer.encode(segment).ids, dtype=torch.long
                )
                text_rows = torch.zeros(
                    (token_ids.numel(), self.num_codebooks + 1), dtype=torch.long
                )
                text_rows[:, 0] = token_ids
                rows.append(text_rows)
            else:
                audio_codes = segment.to(device="cpu", dtype=torch.long)
                if (
                    audio_codes.ndim != 2
                    or audio_codes.shape[0] != self.num_codebooks
                    or audio_codes.shape[1] == 0
                ):
                    raise request_error(
                        f"audio codes must be [{self.num_codebooks}, frames]; got "
                        f"{tuple(audio_codes.shape)}"
                    )
                rows.append(
                    torch.cat([audio_codes[:1] + semantic_begin_id, audio_codes]).T
                )
        return torch.cat(rows)
