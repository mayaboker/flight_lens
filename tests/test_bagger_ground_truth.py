"""Schema-free physics truth playback does not require the desktop UI."""
import json

from mcap.writer import Writer, CompressionType
import msgpack

from flight_lens.bagger_reader import load_bagger_flight


def test_ground_truth_populates_position_and_velocity(tmp_path):
    path = tmp_path / 'truth.mcap'
    base = 1_700_000_000_000_000_000
    with path.open('wb') as stream:
        writer = Writer(stream, compression=CompressionType.NONE)
        writer.start()
        channel = writer.register_channel('analysis/physics/ground_truth', '', 0, {'shm': 'false'})
        for index, position, velocity in ((0, [1, 2, 3], [4, 5, 6]),
                                          (1, [2, 3, 4], [7, 8, 9])):
            payload = json.dumps(dict(schema_version=1, drone_id='drone_1', episode_id='episode',
                                      step_index=index, simulation_time_s=index / 30,
                                      position_m=position, linear_velocity_m_s=velocity)).encode()
            writer.add_message(channel, base + index * 100_000_000, payload,
                               base + index * 100_000_000)
        writer.finish()
    data = load_bagger_flight(path, lambda _: None, lambda: False)
    episode = data.episodes[0]
    assert episode.trajectory_origin == 'Actual ground truth'
    assert episode.trajectory.times.tolist() == [0., .1]
    assert episode.trajectory.values.tolist() == [[1., 2., 3.], [2., 3., 4.]]
    assert episode.velocity.values.tolist() == [[4., 5., 6.], [7., 8., 9.]]


def test_visual_bbox_and_navigation_ttc_feed_bagger_plots(tmp_path):
    path = tmp_path / 'visual.mcap'
    base = 1_700_000_000_000_000_000
    with path.open('wb') as stream:
        writer = Writer(stream, compression=CompressionType.NONE)
        writer.start()
        visual = writer.register_channel('visual/bbox', '', 0, {'shm': 'false'})
        navigation = writer.register_channel('features/navigation_result', '', 0, {'shm': 'false'})
        image = writer.register_channel('camera/front/image', '', 0, {'shm': 'true', 'compress': 'jpeg'})

        def envelope(value):
            return msgpack.packb({'schema_version': 1, 'value': value}, use_bin_type=True)

        box = {'schema_version': 3, 'sequence': 0, 'width': 640, 'height': 480,
               'bbox': {'x': 320, 'y': 240, 'width': 20, 'height': 10}}
        writer.add_message(visual, base, envelope(box), base)
        writer.add_message(navigation, base + 10_000_000, envelope({
            'diagnostics': {'optical_ttc_s': None, 'ttc_reason': 'insufficient_history'}}),
            base + 10_000_000)
        writer.add_message(image, base + 50_000_000, b'jpeg', base + 50_000_000, 0)
        writer.add_message(navigation, base + 70_000_000, envelope({
            'diagnostics': {'optical_ttc_s': 2.5, 'ttc_reason': 'qualified'}}), base + 70_000_000)
        writer.add_message(visual, base + 100_000_000,
                           envelope({**box, 'sequence': 1, 'bbox': None}), base + 100_000_000)
        writer.add_message(image, base + 150_000_000, b'jpeg', base + 150_000_000, 1)
        writer.finish()

    episode = load_bagger_flight(path, lambda _: None, lambda: False).episodes[0]
    assert episode.camera_error_x.values[:, 0].tolist() == [10.]
    assert episode.camera_error_y.values[:, 0].tolist() == [5.]
    assert episode.margins.values.tolist() == [[320., 240., 300., 230.]]
    assert episode.ttc_filtered.values[:, 0].tolist() == [2.5]
    assert episode.tracking_time_s == .01
    assert [(event.reason, event.time_s) for event in episode.events] == [('los_start', .01)]
    assert [sample.bbox for sample in episode.ttc_samples] == [(320., 240., 20., 10.), None]
    assert episode.tracking_samples[0].bbox == (320., 240., 20., 10.)
    assert episode.tracking_samples[1].bbox is None


def test_first_contact_marks_hit_and_ends_plotted_samples(tmp_path):
    path = tmp_path / 'contact.mcap'
    base = 1_700_000_000_000_000_000
    with path.open('wb') as stream:
        writer = Writer(stream, compression=CompressionType.NONE)
        writer.start()
        contact = writer.register_channel('simulation/target_contact', '', 0, {'shm': 'false'})
        altitude = writer.register_channel('telemetry/vfr_hud', '', 0, {'shm': 'false'})
        truth = writer.register_channel('analysis/physics/ground_truth', '', 0, {'shm': 'false'})

        def envelope(value):
            return msgpack.packb({'schema_version': 1, 'value': value}, use_bin_type=True)

        for index in range(4):
            stamp = base + index * 100_000_000
            writer.add_message(altitude, stamp, envelope({'alt': index}), stamp)
            record = dict(schema_version=1, drone_id='drone_1', episode_id='episode',
                          step_index=index, simulation_time_s=index / 10,
                          position_m=[index, 0, index], linear_velocity_m_s=[index, 0, 0])
            writer.add_message(truth, stamp, json.dumps(record).encode(), stamp)
            writer.add_message(contact, stamp, envelope(index >= 2), stamp)
        writer.finish()

    episode = load_bagger_flight(path, lambda _: None, lambda: False).episodes[0]
    assert len(episode.events) == 1
    assert episode.events[0].reason == 'target_hit'
    assert episode.events[0].time_s == .2
    assert episode.trajectory.times.tolist() == [0., .1, .2]
    assert episode.velocity.times.tolist() == [0., .1, .2]
    assert episode.barometer.times.tolist() == [0., .1, .2]


def test_msp_rc_uses_send_time_and_stops_at_contact(tmp_path):
    path = tmp_path / 'rc.mcap'
    base = 1_700_000_000_000_000_000
    with path.open('wb') as stream:
        writer = Writer(stream, compression=CompressionType.NONE)
        writer.start()
        altitude = writer.register_channel('telemetry/vfr_hud', '', 0, {'shm': 'false'})
        rc = writer.register_channel('control/msp_rc', '', 0, {'shm': 'false'})
        contact = writer.register_channel('simulation/target_contact', '', 0, {'shm': 'false'})
        writer.add_message(altitude, base, msgpack.packb({'schema_version': 1, 'value': {'alt': 20}}), base)
        for sequence, sent_ms, pitch in ((1, 50, 1500), (2, 100, 1578), (3, 300, 1600)):
            payload = msgpack.packb({'schema_version': 1, 'timestamp_source': 'send', 'value': {
                'sequence': sequence, 'phase': 'los', 'sent_monotonic_ns': sequence,
                'channels': [1500, pitch, 1540, 1500, 2000, 2000, 1000, 1000]}}, use_bin_type=True)
            writer.add_message(rc, base + (sent_ms + 20) * 1_000_000, payload,
                               base + sent_ms * 1_000_000)
        writer.add_message(contact, base + 200_000_000,
                           msgpack.packb({'schema_version': 1, 'value': True}),
                           base + 200_000_000)
        writer.finish()

    episode = load_bagger_flight(path, lambda _: None, lambda: False).episodes[0]
    assert episode.rc_channels.times.tolist() == [.05, .1]
    assert episode.rc_channels.values[:, 1].tolist() == [1500., 1578.]
    assert episode.rc_channels.values[:, 2].tolist() == [1540., 1540.]
