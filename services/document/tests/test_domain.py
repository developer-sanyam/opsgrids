"""The bytes decide, and the bytes are re-encoded (ADR-0009 §5).

No database, no S3, no settings — `app/domain.py` is pure, and this is the suite that proves
each of its five rules stops the specific thing it exists to stop.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from app import domain
from tests.helpers import (
    SVG_BYTES,
    decompression_bomb,
    make_image,
    make_image_with_gps,
)


class TestWhatIsAccepted:
    @pytest.mark.parametrize(
        ("fmt", "content_type"),
        [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
    )
    def test_the_three_accepted_formats_round_trip(self, fmt: str, content_type: str) -> None:
        result = domain.process_upload(make_image(fmt))
        assert result.content_type == content_type
        assert result.width == 64
        assert result.height == 48
        assert result.size_bytes > 0
        assert len(result.checksum_sha256) == 64

    def test_an_svg_is_refused(self) -> None:
        """Permanently, and not by a list somebody could edit: Pillow has no SVG decoder.

        An `<img src>` of an SVG does not execute script, but a direct navigation to the
        object URL does — on a host we control and will later put a cookie-bearing CDN domain
        in front of. There is no version of "sanitise the SVG" that is boring.
        """
        with pytest.raises(domain.ImageRejected) as exc:
            domain.process_upload(SVG_BYTES)
        assert exc.value.code == "unsupported_image"
        assert "JPEG, PNG or WebP" in exc.value.message

    def test_a_gif_is_refused(self) -> None:
        """Refusing is louder than ruining: the re-encode would flatten an animation silently."""
        with pytest.raises(domain.ImageRejected) as exc:
            domain.process_upload(make_image("GIF"))
        assert exc.value.code == "unsupported_image_format"

    def test_an_animated_webp_is_refused_rather_than_flattened(self) -> None:
        buffer = io.BytesIO()
        frames = [Image.new("RGB", (32, 32), c) for c in ("red", "blue", "green")]
        frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:])
        with pytest.raises(domain.ImageRejected) as exc:
            domain.process_upload(buffer.getvalue())
        assert exc.value.code == "animated_image"

    def test_a_file_that_is_not_an_image_at_all_is_refused(self) -> None:
        with pytest.raises(domain.ImageRejected):
            domain.process_upload(b"#!/bin/sh\nrm -rf /\n")

    def test_a_truncated_image_is_refused_rather_than_raising(self) -> None:
        raw = make_image("PNG", size=(400, 400))
        with pytest.raises(domain.ImageRejected):
            domain.process_upload(raw[: len(raw) // 3])


class TestTheFileCannotLieAboutItsType:
    def test_the_stored_content_type_comes_from_the_decoder(self) -> None:
        """A PNG named `photo.jpg` and declared `image/jpeg` is still stored as `image/png`.

        The declared content type and the extension are not arguments to this function at all,
        which is the strongest form of "they are ignored for the decision".
        """
        result = domain.process_upload(make_image("PNG"))
        assert result.content_type == "image/png"

    def test_a_polyglot_does_not_survive_the_round_trip(self) -> None:
        """A file that is both a valid image and a valid HTML document comes out as ours.

        The payload is appended after the image data, where a decoder ignores it and a browser
        sniffing the object would not. Re-encoding is what makes the stored bytes ours rather
        than the uploader's.
        """
        payload = b"<html><script>alert(1)</script></html>"
        polyglot = make_image("JPEG") + payload
        result = domain.process_upload(polyglot)
        assert payload not in result.data


class TestTheDecompressionBomb:
    def test_max_image_pixels_is_set_explicitly(self) -> None:
        """Pillow's default behaviour at this threshold is a *warning*, which is worse than
        either alternative. The module sets a real value at import."""
        assert Image.MAX_IMAGE_PIXELS == domain.MAX_IMAGE_PIXELS == 24_000_000

    def test_a_bomb_is_refused_before_it_is_decoded(self) -> None:
        """81 megapixels of flat colour is a few kilobytes on the wire — the attack a byte
        limit does not cover."""
        raw = decompression_bomb(9000, 9000)
        assert len(raw) < 1024 * 1024  # small on the wire, enormous in memory
        with pytest.raises(domain.ImageRejected) as exc:
            domain.process_upload(raw)
        assert exc.value.code == "image_too_many_pixels"

    def test_a_legal_sized_image_that_used_to_nearly_oom_is_now_refused(self) -> None:
        """A 7000x7000 PNG (49 MP) was accepted under the old 50 MP ceiling and measured
        ~489 MB peak RSS decoding, resizing and re-encoding it — against this container's 320m
        `mem_limit`. Refusing it fast, off the header, before a single pixel decodes, is the
        fix; see the module-level comment on MAX_IMAGE_PIXELS."""
        raw = decompression_bomb(7000, 7000)
        with pytest.raises(domain.ImageRejected) as exc:
            domain.process_upload(raw)
        assert exc.value.code == "image_too_many_pixels"

    def test_an_image_just_under_the_ceiling_is_accepted(self) -> None:
        raw = decompression_bomb(4800, 4800)  # 23.04 MP, under the 24 MP ceiling
        assert domain.process_upload(raw).width == domain.MAX_LONG_EDGE


class TestTheReEncode:
    def test_exif_including_gps_is_stripped(self) -> None:
        raw = make_image_with_gps()
        assert Image.open(io.BytesIO(raw)).getexif().get_ifd(0x8825), "fixture has no GPS"

        result = domain.process_upload(raw)
        stored = Image.open(io.BytesIO(result.data))
        assert not stored.getexif().get_ifd(0x8825)
        assert not stored.getexif()

    def test_a_large_image_is_downscaled_to_the_long_edge(self) -> None:
        result = domain.process_upload(make_image("JPEG", size=(4000, 2000)))
        assert result.width == domain.MAX_LONG_EDGE
        assert result.height == domain.MAX_LONG_EDGE // 2

    def test_a_small_image_is_not_upscaled(self) -> None:
        result = domain.process_upload(make_image("PNG", size=(120, 90)))
        assert (result.width, result.height) == (120, 90)

    def test_the_checksum_is_of_the_stored_bytes_not_the_uploaded_ones(self) -> None:
        import hashlib

        raw = make_image("JPEG", size=(3000, 3000))
        result = domain.process_upload(raw)
        assert result.checksum_sha256 == hashlib.sha256(result.data).hexdigest()
        assert result.checksum_sha256 != hashlib.sha256(raw).hexdigest()

    def test_processing_is_deterministic(self) -> None:
        """Same bytes in, same bytes out — which is what makes the checksum worth storing."""
        raw = make_image("PNG", size=(300, 200), colour="teal")
        assert (
            domain.process_upload(raw).checksum_sha256 == domain.process_upload(raw).checksum_sha256
        )


class TestKeysAndPaths:
    def test_the_key_is_two_uuids_and_no_extension(self) -> None:
        """The format travels in the object's Content-Type, which is what S3 serves. Leaving
        the extension out is what makes the URL derivable from two ids alone."""
        org = "8f1c0f7e-0000-4000-8000-000000000001"
        asset = "3b9d0f7e-0000-4000-8000-000000000002"
        assert domain.storage_key(org, asset) == f"{org}/{asset}"
        assert "." not in domain.storage_key(org, asset)

    def test_the_public_path_is_root_relative_and_resolves_onto_the_key(self) -> None:
        """`/a` is a sentinel, not a path segment of the key: dropping it and joining the rest
        onto ASSET_BASE has to land exactly on the object (ADR-0009 §6)."""
        org = "8f1c0f7e-0000-4000-8000-000000000001"
        asset = "3b9d0f7e-0000-4000-8000-000000000002"
        path = domain.public_path(org, asset)
        assert path == f"/a/{org}/{asset}"
        assert path.removeprefix("/a") == f"/{domain.storage_key(org, asset)}"

    def test_parse_storage_key_is_the_inverse_of_storage_key(self) -> None:
        org = uuid.uuid4()
        asset = uuid.uuid4()
        parsed = domain.parse_storage_key(domain.storage_key(org, asset))
        assert parsed == (org, asset)

    def test_parse_storage_key_refuses_a_key_this_service_never_wrote(self) -> None:
        """The orphan sweep lists a shared bucket; an object with a key shape this service
        never produced must be left alone rather than guessed at (`app/cron.py`)."""
        assert domain.parse_storage_key("not-a-uuid/also-not-one") is None
        assert domain.parse_storage_key(str(uuid.uuid4())) is None
        assert domain.parse_storage_key(f"{uuid.uuid4()}/{uuid.uuid4()}/extra") is None
        assert domain.parse_storage_key("") is None
