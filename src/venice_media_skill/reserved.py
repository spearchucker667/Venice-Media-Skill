"""Reserved and transport-control key constants.

Lives in a dedicated module to break the circular import between
:mod:`venice_media_skill.request` and :mod:`venice_media_skill.payloads`.
"""

from __future__ import annotations

from typing import Final

RESERVED_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "operation",
        "model",
        "prompt",
        "inputs",
        "output",
        "execution",
        "attestations",
        "parameters",
    }
)

RESERVED_PROVIDER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "consents",
        "queueId",
        "download_url",
        "downloadUrl",
        "image_url",
        "imageUrl",
        "video_url",
        "videoUrl",
        "audio_url",
        "audioUrl",
        "end_image_url",
        "endImageUrl",
        "reference_image_urls",
        "referenceImageUrls",
        "reference_video_urls",
        "referenceVideoUrls",
        "reference_audio_urls",
        "referenceAudioUrls",
        "scene_image_urls",
        "sceneImageUrls",
        "elements",
        "Authorization",
        "authorization",
        "api_key",
        "token",
        "venice_api_key",
        "stream",
        "streaming",
        "return_binary",
        "returnBinary",
    }
)

RESERVED_PARAMETERS: Final[frozenset[str]] = RESERVED_TOP_LEVEL_KEYS | RESERVED_PROVIDER_KEYS
