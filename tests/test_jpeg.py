from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from foxglove_schemas_protobuf.CompressedImage_pb2 import CompressedImage
import pytest

from flight_lens import reader
from conftest import health, msp, write_mcap


def payload(format='JPEG', size=(16, 12)):
    with Image.new('RGB', size, (240, 0, 0)) as image:
        buffer = BytesIO()
        image.save(buffer, format=format)
        return buffer.getvalue()


def decode(data, format='jpeg'):
    message = CompressedImage(data=data, format=format)
    return reader.decode_frame(SimpleNamespace(message_encoding='protobuf'),
        SimpleNamespace(data=message.SerializeToString()), SimpleNamespace(name='foxglove.CompressedImage'))


def test_jpeg_color_and_dimensions():
    frame = decode(payload())
    assert (frame.width, frame.height) == (16, 12)
    assert frame.rgb[0] > 230 and frame.rgb[1] < 5 and frame.rgb[2] < 5


@pytest.mark.parametrize('data,format', [(b'bad', 'jpeg'), (payload(), 'h264'), (payload('PNG'), 'jpeg')])
def test_invalid_or_mislabeled_jpeg_is_rejected(data, format):
    with pytest.raises(ValueError):
        decode(data, format)


def test_dimension_limit_checked_before_pixel_decode(monkeypatch):
    monkeypatch.setattr(reader, 'MAX_IMAGE_BYTES', 2048)
    with pytest.raises(ValueError, match='oversized'):
        decode(payload(size=(32, 32)))


def test_dropped_images_notice_does_not_invalidate_finished_recording(tmp_path):
    data = health()
    data['dropped_images'] = 17
    recording = reader.load_flight(write_mcap(tmp_path/'dropped.mcap', [(reader.MSP, 0.1, msp(0.1))], data))
    episode = recording.episodes[0]
    assert episode.summary.integrity == 'Complete (recorder)'
    assert any('17 images were dropped' in notice for notice in episode.notices)
