"""Adapt finalized, schema-free MCAP Bagger recordings to Flight Lens playback."""
from bisect import bisect_right
from math import degrees, isfinite
from pathlib import Path

import msgpack
from mcap.reader import make_reader

from .model import Episode, FlightData, FrameRef, Summary, TrackingSample, series

IMAGE = 'camera/front/image'
ALTITUDE = 'telemetry/vfr_hud'
ATTITUDE = 'telemetry/attitude'
TOPICS = (IMAGE, ALTITUDE, ATTITUDE)
MAX_ENVELOPE_BYTES = 1024 * 1024
MAX_SAMPLE_AGE_S = .5


def is_bagger_summary(summary):
    """Recognize the bagger's schema-free channels without guessing from a filename."""
    if summary is None:
        return False
    return any(channel.topic == IMAGE and channel.metadata.get('shm') == 'true'
               or channel.topic in (ALTITUDE, ATTITUDE) and channel.schema_id == 0
               and channel.message_encoding == ''
               for channel in summary.channels.values())


def _envelope(payload):
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError('MsgPack telemetry exceeds 1 MiB')
    try:
        packet = msgpack.unpackb(payload, raw=False)
    except (ValueError, TypeError, msgpack.ExtraData, msgpack.FormatError,
            msgpack.StackError) as error:
        raise ValueError('Invalid MsgPack telemetry') from error
    if (not isinstance(packet, dict) or type(packet.get('schema_version')) is not int
            or packet['schema_version'] != 1 or not isinstance(packet.get('value'), dict)):
        raise ValueError('Unsupported blackboard telemetry envelope')
    return packet['value']


def _finite_number(value):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError('Expected a finite telemetry number')
    return float(value)


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


def load_bagger_flight(path, progress, cancel):
    """Read small telemetry and image references; leave JPEG bytes in the MCAP."""
    path = Path(path).resolve()
    if path.name.endswith('.partial'):
        raise ValueError('Finish the MCAP Bagger recording before opening it.')
    stat = path.stat()
    images, altitude_rows, attitude_rows = [], [], []
    invalid = {ALTITUDE: 0, ATTITUDE: 0}
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
                value = _envelope(message.data)
                if channel.topic == ALTITUDE:
                    altitude_rows.append((message.log_time, (_finite_number(value['alt']),)))
                elif channel.topic == ATTITUDE:
                    attitude_rows.append((message.log_time, tuple(degrees(_finite_number(value[key]))
                                                                  for key in ('roll', 'pitch', 'yaw'))))
            except (KeyError, ValueError, OverflowError):
                invalid[channel.topic] += 1
    stamps = [stamp for stamp, _ in images]
    stamps.extend(stamp for stamp, _ in altitude_rows)
    stamps.extend(stamp for stamp, _ in attitude_rows)
    if not stamps:
        raise ValueError('No recorded JPEG images, VFR_HUD altitude, or ATTITUDE samples found.')
    origin = min(stamps)
    elapsed = lambda stamp: (stamp - origin) / 1e9
    altitude = series((elapsed(stamp), value) for stamp, value in altitude_rows)
    attitude = series(((elapsed(stamp), value) for stamp, value in attitude_rows), 3)
    frames = tuple(FrameRef(IMAGE, stamp, sequence, (stamp, sequence), 0, 0,
                            recording_time_s=elapsed(stamp)) for stamp, sequence in images)
    frames = tuple(sorted(frames, key=lambda ref: (ref.log_time_ns, ref.record_sequence)))
    samples = []
    for ref in frames:
        attitude_value, attitude_age = _recent(attitude, ref.recording_time_s)
        altitude_value, _ = _recent(altitude, ref.recording_time_s)
        samples.append(TrackingSample(
            time_s=ref.recording_time_s, phase='Recorded image', reason='recording',
            navigator_state=None, bbox=None, bbox_age_s=None, dx_px=None, dy_px=None,
            ttc_raw_s=None, ttc_filtered_s=None, ttc_valid=None, ttc_reason=None,
            measured_attitude_deg=attitude_value, measured_attitude_age_s=attitude_age,
            requested_roll_deg=None, requested_pitch_deg=None, requested_yaw_rate_deg_s=None,
            requested_thrust_n=None, frame=ref,
            altitude_m=None if altitude_value is None else altitude_value[0]))
    notices = ['MCAP receive time aligns images and MAVLink telemetry; simulation and mission analyses are unavailable.']
    for topic, count in invalid.items():
        if count:
            notices.append(f'{count:,} invalid {topic} messages were skipped.')
    if not frames:
        notices.append('No camera images were recorded.')
    if not altitude.times.size:
        notices.append('VFR_HUD altitude was not recorded.')
    if not attitude.times.size:
        notices.append('ATTITUDE was not recorded.')
    empty = series([])
    episode = Episode(
        key=('mcap_bagger', path.stem), trajectory=series([], 3),
        trajectory_origin='No physics trajectory recorded', velocity=series([], 3),
        barometer_velocity=empty, attitude=attitude, feedback_attitude=series([], 3),
        msp_attitude=series([], 3), msp_attitude_estimate=series([], 3),
        ttc_raw=empty, ttc_filtered=empty, camera_error_x=empty, camera_error_y=empty,
        thrust=empty, tracking_time_s=None, tracking_samples=tuple(samples),
        barometer=altitude, goal=empty, margins=series([], 4),
        margins_origin='BBox', events=(),
        summary=Summary('Not recorded', elapsed(max(stamps)),
                        float(max(altitude.values[:, 0])) if altitude.times.size else None,
                        None, None, 0, 0, 'Finalized MCAP'),
        notices=tuple(notices), context_times=(), contexts=(), frames=frames,
        boxes={}, time_basis='recording')
    progress(f'Loaded {len(altitude_rows) + len(attitude_rows):,} telemetry samples; {len(frames):,} images remain on disk.')
    return FlightData(path, stat.st_size, stat.st_mtime_ns, (episode,), 0, (), len(frames),
                      recording_format='bagger')
