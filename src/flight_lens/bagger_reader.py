"""Adapt finalized, schema-free MCAP Bagger recordings to Flight Lens playback."""
from bisect import bisect_right
import json
from math import degrees, isfinite
from pathlib import Path

import msgpack
from mcap.reader import make_reader

from .model import Episode, Event, FlightData, FrameRef, Summary, TrackingSample, series

IMAGE = 'camera/front/image'
ALTITUDE = 'telemetry/vfr_hud'
ATTITUDE = 'telemetry/attitude'
GROUND_TRUTH = 'analysis/physics/ground_truth'
BBOX = 'visual/bbox'
NAVIGATION_RESULT = 'features/navigation_result'
CONTACT = 'simulation/target_contact'
MSP_RC = 'control/msp_rc'
TOPICS = (IMAGE, ALTITUDE, ATTITUDE, GROUND_TRUTH, BBOX, NAVIGATION_RESULT, CONTACT, MSP_RC)
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_SAMPLE_AGE_S = .5


def is_bagger_summary(summary):
    """Recognize the bagger's schema-free channels without guessing from a filename."""
    if summary is None:
        return False
    return any(channel.topic == IMAGE and channel.metadata.get('shm') == 'true'
               or channel.topic in (ALTITUDE, ATTITUDE, GROUND_TRUTH, BBOX, NAVIGATION_RESULT, CONTACT, MSP_RC)
               and channel.schema_id == 0
               and channel.message_encoding == ''
               for channel in summary.channels.values())


def _envelope(payload, value_type=dict):
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError('MsgPack telemetry exceeds 1 MiB')
    try:
        packet = msgpack.unpackb(payload, raw=False)
    except (ValueError, TypeError, msgpack.ExtraData, msgpack.FormatError,
            msgpack.StackError) as error:
        raise ValueError('Invalid MsgPack telemetry') from error
    if (not isinstance(packet, dict) or type(packet.get('schema_version')) is not int
            or packet['schema_version'] != 1 or type(packet.get('value')) is not value_type):
        raise ValueError('Unsupported blackboard telemetry envelope')
    return packet['value']


def _finite_number(value):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError('Expected a finite telemetry number')
    return float(value)


def _msp_rc(value):
    """Decode the exact eight PWM channels sent in one MSP_SET_RAW_RC frame."""
    channels = value.get('channels')
    if (type(value.get('sequence')) is not int or value['sequence'] < 1
            or type(value.get('sent_monotonic_ns')) is not int or value['sent_monotonic_ns'] < 0
            or not isinstance(value.get('phase'), str) or not value['phase']
            or not isinstance(channels, list) or len(channels) != 8
            or any(type(channel) is not int or not 1000 <= channel <= 2000 for channel in channels)):
        raise ValueError('Invalid MSP RC command')
    return tuple(channels)


def _ground_truth(payload):
    """Decode the compact physics record; MCAP Bagger stores it unchanged."""
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError('Ground truth exceeds 1 MiB')
    try:
        value = json.loads(payload)
        if (not isinstance(value, dict) or set(value) != {
                'schema_version', 'drone_id', 'episode_id', 'step_index',
                'simulation_time_s', 'position_m', 'linear_velocity_m_s'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or type(value['step_index']) is not int or value['step_index'] < 0
                or not isinstance(value['drone_id'], str) or not value['drone_id']
                or not isinstance(value['episode_id'], str) or not value['episode_id']):
            raise ValueError('Invalid ground-truth identity')
        _finite_number(value['simulation_time_s'])
        vectors = []
        for key in ('position_m', 'linear_velocity_m_s'):
            vector = value[key]
            if not isinstance(vector, list) or len(vector) != 3:
                raise ValueError('Invalid ground-truth vector')
            vectors.append(tuple(_finite_number(component) for component in vector))
        return vectors
    except (UnicodeDecodeError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError('Invalid ground-truth JSON') from error


def _bbox(value):
    """Return image dimensions and an optional image-space x/y/width/height box."""
    if type(value.get('schema_version')) is not int or value['schema_version'] != 3:
        raise ValueError('Unsupported visual bbox version')
    if type(value.get('sequence')) is not int or value['sequence'] < 0:
        raise ValueError('Invalid visual bbox frame sequence')
    width = _finite_number(value['width'])
    height = _finite_number(value['height'])
    if width <= 0 or height <= 0:
        raise ValueError('Invalid image dimensions')
    box = value.get('bbox')
    if box is None:
        return None
    if not isinstance(box, dict):
        raise ValueError('Invalid visual bbox')
    x, y, box_width, box_height = (_finite_number(box[key]) for key in ('x', 'y', 'width', 'height'))
    if (box_width <= 0 or box_height <= 0 or x < 0 or y < 0
            or x + box_width > width or y + box_height > height):
        raise ValueError('Visual bbox lies outside image')
    return x, y, box_width, box_height, width, height


def _optical_ttc(value):
    """Read qualified production optical TTC from a navigation result."""
    diagnostics = value.get('diagnostics')
    if not isinstance(diagnostics, dict):
        raise ValueError('Missing navigation diagnostics')
    estimate = diagnostics.get('optical_ttc_s')
    if estimate is None or diagnostics.get('ttc_reason') != 'qualified':
        return None
    estimate = _finite_number(estimate)
    if estimate <= 0:
        raise ValueError('Invalid optical TTC')
    return estimate


def _ttc_sample(time_s, bbox):
    """Keep detector samples separate from image playback samples for TTC replay."""
    return TrackingSample(
        time_s=time_s, phase='Recorded detection', reason='recording',
        navigator_state=None, bbox=bbox, bbox_age_s=0. if bbox is not None else None,
        dx_px=None, dy_px=None, ttc_raw_s=None, ttc_filtered_s=None,
        ttc_valid=None, ttc_reason=None if bbox is not None else 'not_observed',
        measured_attitude_deg=None, measured_attitude_age_s=None,
        requested_roll_deg=None, requested_pitch_deg=None,
        requested_yaw_rate_deg_s=None, requested_thrust_n=None)


def _recent(samples, time_s):
    if not samples.times.size:
        return None, None
    index = bisect_right(samples.times, time_s) - 1
    if index < 0:
        return None, None
    age = time_s - float(samples.times[index])
    if age > MAX_SAMPLE_AGE_S:
        return None, None
    return tuple(float(value) for value in samples.values[index]), age


def _frame_bbox(rows, sequence, stamp):
    candidates = rows.get(sequence, ())
    if not candidates:
        return None, None
    closest_stamp, box = min(candidates, key=lambda row: abs(row[0] - stamp))
    age = abs(stamp - closest_stamp) / 1e9
    return (box, age) if age <= MAX_SAMPLE_AGE_S else (None, None)


def load_bagger_flight(path, progress, cancel):
    """Read small telemetry and image references; leave JPEG bytes in the MCAP."""
    path = Path(path).resolve()
    if path.name.endswith('.partial'):
        raise ValueError('Finish the MCAP Bagger recording before opening it.')
    stat = path.stat()
    images, altitude_rows, attitude_rows, truth_rows = [], [], [], []
    bbox_rows, bbox_by_sequence, ttc_rows, navigation_stamps, contact_stamps, rc_rows = [], {}, [], [], [], []
    invalid = {ALTITUDE: 0, ATTITUDE: 0, GROUND_TRUTH: 0, BBOX: 0, NAVIGATION_RESULT: 0, CONTACT: 0, MSP_RC: 0}
    with path.open('rb') as stream:
        reader = make_reader(stream, validate_crcs=True)
        summary = reader.get_summary()
        if summary is None or not summary.chunk_indexes:
            raise ValueError('Bagger recording has no usable MCAP index.')
        image_channels = [channel for channel in summary.channels.values() if channel.topic == IMAGE]
        if image_channels and any(channel.metadata.get('compress') != 'jpeg' for channel in image_channels):
            raise ValueError('Bagger raw images need recorded dimensions; this playback supports JPEG images.')
        for count, (_, channel, message) in enumerate(reader.iter_messages(topics=TOPICS), 1):
            if cancel():
                from .reader import Cancelled
                raise Cancelled()
            if count % 500 == 0:
                progress(f'Indexed {count:,} bagger messages…')
            if channel.topic == IMAGE:
                images.append((message.log_time, message.sequence))
                continue
            try:
                if channel.topic == CONTACT:
                    if _envelope(message.data, bool):
                        contact_stamps.append(message.log_time)
                    continue
                if channel.topic == GROUND_TRUTH:
                    truth_rows.append((message.log_time, _ground_truth(message.data)))
                    continue
                value = _envelope(message.data)
                if channel.topic == MSP_RC:
                    rc_rows.append((message.publish_time, _msp_rc(value)))
                elif channel.topic == ALTITUDE:
                    altitude_rows.append((message.log_time, (_finite_number(value['alt']),)))
                elif channel.topic == ATTITUDE:
                    attitude_rows.append((message.log_time, tuple(degrees(_finite_number(value[key]))
                                                                  for key in ('roll', 'pitch', 'yaw'))))
                elif channel.topic == BBOX:
                    box = _bbox(value)
                    bbox_rows.append((message.log_time, box))
                    bbox_by_sequence.setdefault(value['sequence'], []).append((message.log_time, box))
                elif channel.topic == NAVIGATION_RESULT:
                    estimate = _optical_ttc(value)
                    navigation_stamps.append(message.log_time)
                    if estimate is not None:
                        ttc_rows.append((message.log_time, (estimate,)))
            except (KeyError, ValueError, OverflowError):
                invalid[channel.topic] += 1
    stamps = [stamp for stamp, _ in images]
    stamps.extend(stamp for stamp, _ in altitude_rows)
    stamps.extend(stamp for stamp, _ in attitude_rows)
    stamps.extend(stamp for stamp, _ in truth_rows)
    stamps.extend(stamp for stamp, _ in bbox_rows)
    stamps.extend(stamp for stamp, _ in ttc_rows)
    stamps.extend(navigation_stamps)
    stamps.extend(contact_stamps)
    stamps.extend(stamp for stamp, _ in rc_rows)
    if not stamps:
        raise ValueError('No recorded images, telemetry, physics truth, visual boxes, or optical TTC samples found.')
    origin = min(stamps)
    elapsed = lambda stamp: (stamp - origin) / 1e9
    contact_stamp = min(contact_stamps) if contact_stamps else None
    los_stamp = min(navigation_stamps) if navigation_stamps else None
    # Keep the original MCAP and image timeline; stop only plotted samples at contact.
    through_contact = lambda rows: (row for row in rows if contact_stamp is None or row[0] <= contact_stamp)
    altitude_rows = list(through_contact(altitude_rows))
    attitude_rows = list(through_contact(attitude_rows))
    truth_rows = list(through_contact(truth_rows))
    bbox_rows = list(through_contact(bbox_rows))
    ttc_rows = list(through_contact(ttc_rows))
    rc_rows = list(through_contact(rc_rows))
    altitude = series((elapsed(stamp), value) for stamp, value in altitude_rows)
    attitude = series(((elapsed(stamp), value) for stamp, value in attitude_rows), 3)
    trajectory = series(((elapsed(stamp), vectors[0]) for stamp, vectors in truth_rows), 3)
    velocity = series(((elapsed(stamp), vectors[1]) for stamp, vectors in truth_rows), 3)
    bbox_rows.sort(key=lambda row: row[0])
    bbox_samples = series(((elapsed(stamp), box) for stamp, box in bbox_rows if box is not None), 6)
    camera_error_x = series(((elapsed(stamp), (box[0] + box[2] / 2 - box[4] / 2,))
                             for stamp, box in bbox_rows if box is not None))
    camera_error_y = series(((elapsed(stamp), (box[1] + box[3] / 2 - box[5] / 2,))
                             for stamp, box in bbox_rows if box is not None))
    margins = series(((elapsed(stamp), (box[0], box[1], box[4] - box[0] - box[2],
                                        box[5] - box[1] - box[3]))
                      for stamp, box in bbox_rows if box is not None), 4)
    ttc_filtered = series((elapsed(stamp), value) for stamp, value in ttc_rows)
    rc_channels = series(((elapsed(stamp), channels) for stamp, channels in rc_rows), 8)
    ttc_samples = tuple(_ttc_sample(elapsed(stamp), None if box is None else box[:4])
                        for stamp, box in bbox_rows)
    frames = tuple(FrameRef(IMAGE, stamp, sequence, (stamp, sequence), 0, 0,
                            recording_time_s=elapsed(stamp)) for stamp, sequence in images)
    frames = tuple(sorted(frames, key=lambda ref: (ref.log_time_ns, ref.record_sequence)))
    samples = []
    for ref in frames:
        attitude_value, attitude_age = _recent(attitude, ref.recording_time_s)
        altitude_value, _ = _recent(altitude, ref.recording_time_s)
        bbox_value, bbox_age = _frame_bbox(bbox_by_sequence, ref.record_sequence, ref.log_time_ns)
        optical_value, _ = _recent(ttc_filtered, ref.recording_time_s)
        samples.append(TrackingSample(
            time_s=ref.recording_time_s, phase='Recorded image', reason='recording',
            navigator_state=None, bbox=None if bbox_value is None else bbox_value[:4], bbox_age_s=bbox_age,
            dx_px=None if bbox_value is None else bbox_value[0] + bbox_value[2] / 2 - bbox_value[4] / 2,
            dy_px=None if bbox_value is None else bbox_value[1] + bbox_value[3] / 2 - bbox_value[5] / 2,
            ttc_raw_s=None, ttc_filtered_s=None if optical_value is None else optical_value[0],
            ttc_valid=None if optical_value is None else True, ttc_reason=None,
            measured_attitude_deg=attitude_value, measured_attitude_age_s=attitude_age,
            requested_roll_deg=None, requested_pitch_deg=None, requested_yaw_rate_deg_s=None,
            requested_thrust_n=None, frame=ref,
            altitude_m=None if altitude_value is None else altitude_value[0]))
    notices = ['MCAP receive time aligns images, physics truth, and MAVLink telemetry; mission analysis is unavailable.']
    for topic, count in invalid.items():
        if count:
            notices.append(f'{count:,} invalid {topic} messages were skipped.')
    if not frames:
        notices.append('No camera images were recorded.')
    if not altitude.times.size:
        notices.append('VFR_HUD altitude was not recorded.')
    if not attitude.times.size:
        notices.append('ATTITUDE was not recorded.')
    if not trajectory.times.size:
        notices.append('Physics ground truth was not recorded.')
    if not bbox_samples.times.size:
        notices.append('Visual bounding boxes were not recorded.')
    if not ttc_filtered.times.size:
        notices.append('Qualified optical TTC was not recorded.')
    if not rc_channels.times.size:
        notices.append('MSP RC commands were not recorded.')
    events = []
    if los_stamp is not None:
        events.append(Event(elapsed(los_stamp), elapsed(los_stamp), los_stamp,
                            'info', 'navigation', 'los_start', 'LOS',
                            {'source': NAVIGATION_RESULT}))
    if contact_stamp is not None:
        events.append(Event(elapsed(contact_stamp), elapsed(contact_stamp), contact_stamp,
                            'info', 'simulation', 'target_hit', 'CONTACT',
                            {'source': CONTACT}))
    events.sort(key=lambda event: event.arrival_ns)
    empty = series([])
    episode = Episode(
        key=('mcap_bagger', path.stem), trajectory=trajectory,
        trajectory_origin='Actual ground truth' if trajectory.times.size else 'No physics trajectory recorded', velocity=velocity,
        barometer_velocity=empty, attitude=attitude, feedback_attitude=series([], 3),
        msp_attitude=series([], 3), msp_attitude_estimate=series([], 3),
        ttc_raw=empty, ttc_filtered=ttc_filtered, camera_error_x=camera_error_x,
        camera_error_y=camera_error_y,
        thrust=empty, tracking_time_s=elapsed(los_stamp) if los_stamp is not None else None,
        tracking_samples=tuple(samples),
        barometer=altitude, goal=empty, margins=margins,
        margins_origin='Recorded bbox',
        events=tuple(events),
        summary=Summary('Not recorded', elapsed(max(stamps)),
                        float(max(trajectory.values[:, 2])) if trajectory.times.size else
                        float(max(altitude.values[:, 0])) if altitude.times.size else None,
                        (float(min(trajectory.values[:, 1])), float(max(trajectory.values[:, 1])))
                        if trajectory.times.size else None, None, 0, 0, 'Finalized MCAP'),
        notices=tuple(notices), context_times=(), contexts=(), frames=frames,
        boxes={}, time_basis='recording', ttc_samples=ttc_samples, rc_channels=rc_channels)
    progress(f'Loaded {len(altitude_rows) + len(attitude_rows) + len(truth_rows) + len(bbox_rows) + len(ttc_rows) + len(rc_rows):,} telemetry samples; {len(frames):,} images remain on disk.')
    return FlightData(path, stat.st_size, stat.st_mtime_ns, (episode,), 0, (), len(frames),
                      recording_format='bagger')
