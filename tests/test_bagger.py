"""The schema-free MCAP Bagger format uses log time and opaque JPEG/MsgPack bytes."""
from io import BytesIO
from pathlib import Path

import msgpack
from mcap.writer import Writer, CompressionType
from PIL import Image
from PySide6 import QtWidgets
import pytest

from flight_lens.app import TrackingReplay
from flight_lens.reader import load_flight, read_frame
from flight_lens.video import export_mp4, frame_schedule

BASE_NS = 1_700_000_000_000_000_000


def _jpeg():
    with Image.new('RGB', (8, 6), (240, 0, 0)) as image, BytesIO() as stream:
        image.save(stream, format='JPEG')
        return stream.getvalue()


def _envelope(value):
    return msgpack.packb({'schema_version': 1, 'value': value,
                          'timestamp_source': 'receive'}, use_bin_type=True)


def write_bagger(path, *, compress='jpeg', malformed=False, image_only=False):
    with Path(path).open('wb') as stream:
        writer = Writer(stream, compression=CompressionType.NONE)
        writer.start()
        image = writer.register_channel('camera/front/image', '', 0,
                                         {'shm': 'true', 'compress': compress})
        altitude = writer.register_channel('telemetry/vfr_hud', '', 0, {'shm': 'false'})
        attitude = writer.register_channel('telemetry/attitude', '', 0, {'shm': 'false'})
        if not image_only:
            writer.add_message(altitude, BASE_NS, _envelope({'alt': 2.5}), BASE_NS + 10**12)
            writer.add_message(attitude, BASE_NS,
                               _envelope({'roll': .1, 'pitch': -.2, 'yaw': .3}), BASE_NS + 10**12)
            if malformed:
                writer.add_message(attitude, BASE_NS + 50_000_000, b'bad', BASE_NS + 10**12)
        for index, offset in enumerate((100_000_000, 800_000_000, 1_000_000_000)):
            writer.add_message(image, BASE_NS + offset, _jpeg(), BASE_NS + offset, index)
        writer.finish()
    return path


def test_bagger_images_and_telemetry_share_log_time(tmp_path):
    data = load_flight(write_bagger(tmp_path / 'recording.mcap', malformed=True))
    episode = data.episodes[0]
    assert data.recording_format == 'bagger' and data.index_path is None
    assert len(episode.frames) == 3 and data.camera_image_count == 3
    assert episode.time_basis == 'recording'
    assert episode.barometer.times.tolist() == [0.]
    assert episode.barometer.values[:, 0].tolist() == [2.5]
    assert episode.attitude.values[0, 0] == pytest.approx(5.72957795)
    assert episode.tracking_samples[0].altitude_m == 2.5
    assert episode.tracking_samples[0].measured_attitude_deg[1] == pytest.approx(-11.4591559)
    assert episode.tracking_samples[1].altitude_m is None
    assert episode.tracking_samples[1].measured_attitude_deg is None
    assert episode.frames[0].playback_time_s == pytest.approx(.1)
    assert frame_schedule(episode.frames) == (21, 6, 1)
    assert any('invalid telemetry/attitude' in notice for notice in episode.notices)
    frame = read_frame(data, episode.frames[0])
    assert (frame.width, frame.height) == (8, 6)
    assert frame.rgb[0] > 230


def test_image_only_and_raw_image_feedback(tmp_path):
    data = load_flight(write_bagger(tmp_path / 'images.mcap', image_only=True))
    assert not data.episodes[0].barometer.times.size
    assert any('VFR_HUD altitude was not recorded' in notice for notice in data.episodes[0].notices)
    with pytest.raises(ValueError, match='raw images need recorded dimensions'):
        load_flight(write_bagger(tmp_path / 'raw.mcap', compress='raw'))


def test_tracking_replay_uses_recorded_images(application, tmp_path):
    data = load_flight(write_bagger(tmp_path / 'replay.mcap'))
    replay = TrackingReplay()
    times = []
    replay.time_changed.connect(times.append)
    replay.set_episode(data.episodes[0])
    assert replay.rate_label.text() == 'Recording rate'
    assert replay.category_values['altitude'].text() == '+2.500 m'
    assert replay.camera_frame_counter.text() == 'Frame 1/3'
    replay.step(1)
    assert times == pytest.approx([.1, .8])
    assert replay.category_values['altitude'].text() == '—'
    replay.close()


def test_overview_cursor_follows_bagger_replay(application, tmp_path):
    from flight_lens.app import Overview

    data = load_flight(write_bagger(tmp_path / 'overview.mcap'))
    window = Overview()
    try:
        window.apply_data(data)
        assert window.tabs.currentWidget() is window.tracking_tab
        assert window.cursor_time_s == pytest.approx(.1)
        assert 'VFR_HUD altitude +2.50 m' in window.readout.text()
        window.tracking_tab.step(1)
        assert window.cursor_time_s == pytest.approx(.8)
        assert 'VFR_HUD altitude unavailable' in window.readout.text()
    finally:
        window.close()


@pytest.fixture
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_bagger_mp4_export(tmp_path):
    data = load_flight(write_bagger(tmp_path / 'video.mcap'))
    output = tmp_path / 'video.mp4'
    try:
        export_mp4(data, data.episodes[0], output)
    except ValueError as error:
        if 'requires FFmpeg' in str(error):
            pytest.skip(str(error))
        raise
    assert output.is_file() and output.stat().st_size > 0
