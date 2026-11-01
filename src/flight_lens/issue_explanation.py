"""Explain recorded causes, retaining evidence that recovery itself clears.

This module never runs a controller or substitutes current tuning defaults.
Only preceding samples and the recorded, episode-local profile are used.
"""
from math import degrees, isfinite
from collections.abc import Mapping

MICROSECONDS_PER_SECOND = 1_000_000
LIMITED_REASONS = frozenset(('control_limited', 'descent_infeasible'))
MAX_EVIDENCE_GAP_S = .2


def number(mapping, key):
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(key)
    return float(value) if type(value) in (int, float) and isfinite(value) else None


def tracking(value):
    return (str(value.get('navigator_state', '')).upper() == 'TRACK'
            or value.get('phase') == 'LOS_APPROACH')


class IssueEvidence:
    """Consume records in arrival order within one physics/episode identity."""
    def __init__(self):
        self.settings = {}
        self.previous = None
        self.limited_since_us = None

    def configure(self, settings):
        settings = settings if isinstance(settings, Mapping) else {}
        settings = dict(tuning=dict(settings['tuning']) if isinstance(settings.get('tuning'), Mapping) else {},
                        vehicle=dict(settings['vehicle']) if isinstance(settings.get('vehicle'), Mapping) else {},
                        target_altitude_m=settings.get('target_altitude_m'))
        # A profile change can alter thresholds; do not join intervals across it.
        if settings != self.settings:
            self.limited_since_us = None
            self.previous = None
        self.settings = settings

    def consume(self, time_s, value):
        previous = self.previous
        now_us = number(value, 'measurement_us')
        before_us = None if previous is None else number(previous, 'measurement_us')
        max_gap = number(self.settings.get('tuning', {}), 'max_control_gap_s')
        continuous = (now_us is not None and before_us is not None and max_gap is not None
                      and 0 <= (now_us - before_us) / MICROSECONDS_PER_SECOND <= max_gap)
        recent = (now_us is not None and before_us is not None
                  and 0 <= (now_us - before_us) / MICROSECONDS_PER_SECOND
                  <= (max_gap if max_gap is not None else MAX_EVIDENCE_GAP_S))
        if not continuous:
            self.limited_since_us = None
        duration = (None if self.limited_since_us is None or now_us is None else
                    (now_us - self.limited_since_us) / MICROSECONDS_PER_SECOND)
        context = dict(settings=self.settings, previous=previous if recent else None,
                       limitation_duration_s=duration)
        if tracking(value) and value.get('reason') in LIMITED_REASONS:
            if self.limited_since_us is None:
                self.limited_since_us = now_us
        else:
            self.limited_since_us = None
        self.previous = dict(time_s=time_s, measurement_us=value.get('measurement_us'),
                             phase=value.get('phase'), navigator_state=value.get('navigator_state'),
                             reason=value.get('reason'), diagnostics=value.get('diagnostics') or {})
        return context


def _vertical(value):
    return f'{abs(value):.2f} m/s {"down" if value < 0 else "up"}'


def _persistent(details, context):
    previous = context.get('previous') or {}
    d = (previous.get('diagnostics') or {}) if tracking(previous) else {}
    settings = context.get('settings') or {}
    tuning = settings.get('tuning') or {}
    facts = []
    causes = []
    request = number(d, 'requested_vertical_speed_m_s')
    reference = number(d, 'vertical_reference_m_s')
    descent = number(tuning, 'max_descent_m_s')
    climb = number(tuning, 'max_climb_m_s')
    if request is not None and descent is not None and request < -descent:
        causes.append('the requested descent was faster than allowed')
        facts.append(f'The controller requested {_vertical(request)}; max_descent_m_s allowed {descent:.2f} m/s down.')
    elif request is not None and climb is not None and request > climb:
        causes.append('the requested climb was faster than allowed')
        facts.append(f'The controller requested {_vertical(request)}; max_climb_m_s allowed {climb:.2f} m/s up.')
    elif reference is not None and descent is not None and reference <= -descent:
        causes.append('the descent command reached its limit')
        facts.append(f'The descent reference reached {descent:.2f} m/s (max_descent_m_s).')
    elif reference is not None and climb is not None and reference >= climb:
        causes.append('the climb command reached its limit')
        facts.append(f'The climb reference reached {climb:.2f} m/s (max_climb_m_s).')
    elif d.get('vertical_limited') is True:
        causes.append('the vertical-speed request was limited')
        facts.append('The preceding tracking sample reported a vertical-speed limit; its exact bound was not recorded.')
    if d.get('angle_limited') is True:
        causes.append('a tilt command exceeded its allowed angle')
        pairs = [('requested_roll_rad','roll_limit_rad','Roll'),
                 ('requested_tilt_rad','combined_tilt_limit_rad','Combined tilt')]
        for measured_key, limit_key, label in pairs:
            value, limit = number(d, measured_key), number(tuning, limit_key)
            if value is not None and limit is not None and abs(value) > limit:
                facts.append(f'{label} demand was {degrees(abs(value)):.2f}°; {limit_key} allowed {degrees(limit):.2f}°.')
                break
        else:
            facts.append('The preceding tracking sample reported an angle limit.')
    if d.get('thrust_saturated') is True:
        causes.append('thrust reached its available limit')
        thrust = number(d, 'thrust_n')
        maximum = number(settings.get('vehicle') or {}, 'max_thrust_n')
        if thrust is not None and maximum is not None:
            facts.append(f'Thrust saturation was reported at {thrust:.2f} N; maximum thrust was {maximum:.2f} N.')
        else:
            facts.append('The preceding tracking sample reported thrust saturation.')
    if d.get('descent_feasible') is False:
        causes.append('the estimated remaining time was too short for the descent')
        tau = number(d, 'optical_ttc_s')
        altitude = number(d, 'altitude_m')
        target = number(settings, 'target_altitude_m')
        parts = []
        if tau is not None:
            parts.append(f'estimated time remaining {tau:.2f} s')
        if altitude is not None and target is not None:
            parts.append(f'height above the configured target {altitude-target:.2f} m')
        facts.append('The runtime judged descent infeasible' + (': ' + '; '.join(parts) if parts else '') + '.')
    if causes:
        cause = 'Recovery started because ' + '; '.join(causes) + '.'
    else:
        cause = ('Recovery started because a control limit or the descent-feasibility check stayed active. '
                 'The recording does not identify which condition triggered it.')
    duration = number(context, 'limitation_duration_s')
    timeout = number(tuning, 'limitation_timeout_s')
    if duration is not None and timeout is not None:
        facts.append(f'The limiting condition was observed for about {duration:.2f} s; limitation_timeout_s was {timeout:.2f} s.')
    elif timeout is not None:
        facts.append(f'limitation_timeout_s was {timeout:.2f} s; the continuous duration was not recorded.')
    return cause, facts, 'Forward approach was reduced, then the navigator switched to recovery instead of continuing normal tracking.'


def explain_issue(event):
    """A short cause, only relevant measurements, and the control consequence."""
    details = event.details
    context = details.get('explanation_context') or {}
    settings = context.get('settings') or {}
    tuning = settings.get('tuning') or {}
    reason = str(details.get('reason') or event.reason)
    if reason.startswith('Recovery · '):
        reason = reason.removeprefix('Recovery · ')
    if event.component == 'vision':
        reason = str(details.get('vision_reason') or event.reason)
    if reason.startswith('los_') and reason not in ('los_deviation',):
        reason = reason.removeprefix('los_')
    facts = []
    recovering = event.component == 'navigation'
    consequence = ('The navigator switched to recovery and tried to regain a usable target.' if recovering else
                   'This target measurement was rejected; normal LOS guidance cannot use it.')
    if reason == 'persistent_limitation':
        cause, facts, consequence = _persistent(details, context)
    elif reason == 'clipped_target':
        margins = details.get('edge_margins_px')
        edges = details.get('touched_edges')
        edges = ([edge for edge in edges if edge in ('left','top','right','bottom')]
                 if isinstance(edges, (list, tuple)) else [])
        if not edges and isinstance(margins, (list, tuple)) and len(margins) == 4:
            edges = [name for name, value in zip(('left','top','right','bottom'),margins)
                     if type(value) in (int,float) and isfinite(value) and value <= 0]
        location = ' and '.join(edges or ())
        cause = (f'The target box reached the {location + " " if location else ""}image edge. '
                 'The navigator could no longer trust that the whole target was visible.')
    elif reason in ('stale_image','stale_or_foreign_image','image_deadline'):
        cause = ('The image was about to expire before another command could be delivered.' if reason == 'image_deadline'
                 else 'The image was too old or belonged to another episode.' if reason == 'stale_or_foreign_image'
                 else 'The target image was older than the allowed age.')
        age, limit = number(details,'image_age_s'), number(tuning,'max_image_age_s')
        if age is not None and limit is not None:
            facts.append(f'Image age: {age:.3f} s; max_image_age_s: {limit:.3f} s.')
    elif reason == 'no_target':
        cause = 'The detector found no target in the image, so visual tracking lost its target.'
    elif reason == 'attitude_unaligned':
        cause = 'No valid drone attitude was available at the image capture time, so the target direction could not be calculated.'
    elif reason == 'los_deviation':
        cause = 'The target direction moved too far from the line of sight saved during acquisition.'
        limit = number(tuning,'max_los_deviation_rad')
        if limit is not None:
            facts.append(f'Allowed deviation: {degrees(limit):.2f}° (max_los_deviation_rad).')
        facts.append('The exact rejected deviation was not recorded.')
    elif reason == 'association_jump':
        cause = 'The target direction jumped too far between images, so the navigator could not trust it was still tracking the same target.'
        limit = number(tuning,'max_association_jump_rad')
        if limit is not None:
            facts.append(f'Allowed jump: {degrees(limit):.2f}° (max_association_jump_rad).')
    elif reason == 'bearing_outside_envelope':
        cause = 'The target direction was outside the allowed viewing angles.'
        for axis in ('azimuth','elevation'):
            value = number(details,f'rejected_{axis}_rad')
            limit = number(details,f'max_{axis}_rad')
            if limit is None:
                limit = number(tuning,f'max_{axis}_rad')
            if value is not None and limit is not None and abs(value) > limit:
                facts.append(f'{axis.capitalize()}: {degrees(value):.2f}°; allowed magnitude: {degrees(limit):.2f}°.')
    elif reason == 'measured_over_tilt':
        cause = 'The drone tilted farther than the allowed combined roll/pitch angle.'
        tilt, limit = number(details,'measured_tilt_rad'), number(tuning,'combined_tilt_limit_rad')
        if tilt is not None and limit is not None:
            facts.append(f'Measured tilt: {degrees(tilt):.2f}°; combined_tilt_limit_rad: {degrees(limit):.2f}°.')
    elif reason in ('attitude_stale','barometer_stale','feedback_stale','sensors_stale'):
        sensor = reason.removesuffix('_stale')
        cause = f'The required {sensor} measurements stopped arriving fresh enough for control.'
        for name in ('attitude','barometer'):
            age, limit = number(details,f'{name}_age_s'), number(tuning,f'max_{name}_age_s')
            if age is not None and limit is not None and age > limit:
                facts.append(f'{name.capitalize()} age: {age:.3f} s; allowed: {limit:.3f} s.')
        consequence = 'The controller withdrew usable guidance rather than using stale measurements.'
    elif reason in ('acquisition_timeout','recovery_timeout','search_timeout'):
        action = {'acquisition_timeout':'acquire a stable target','recovery_timeout':'regain a usable target',
                  'search_timeout':'find and center a target'}[reason]
        cause = f'The navigator could not {action} before its deadline.'
        key = 'yaw_search_timeout_s' if reason == 'search_timeout' else reason + '_s'
        limit = number(tuning,key)
        if limit is not None:
            facts.append(f'Time allowed: {limit:.2f} s ({key}).')
        consequence = 'The mission stopped normal guidance after the timeout.'
    elif reason == 'control_gap':
        cause = 'The control loop went too long without an update.'
        limit = number(tuning,'max_control_gap_s')
        if limit is not None:
            facts.append(f'Maximum allowed gap: {limit:.3f} s (max_control_gap_s).')
        consequence = 'The navigator faulted instead of continuing with a delayed command.'
    elif reason == 'search_exhausted':
        cause = 'A full yaw search finished without finding a usable target.'
        consequence = 'The search stopped and the mission reported a fault.'
    elif reason == 'rc_expired':
        cause = 'The previous RC command expired before a new valid command reached the flight backend.'
        consequence = 'The backend stopped the mission.'
    elif reason == 'command_expired':
        cause = 'The generated guidance command had no remaining validity time.'
        consequence = 'The application withdrew the active flight command.'
    elif reason == 'platform_stale':
        cause = 'The application stopped receiving the required platform configuration updates.'
        consequence = 'The application withdrew active control.'
    elif reason.endswith('_stream_changed'):
        cause = 'A required sensor stream restarted or changed identity during the mission.'
        consequence = 'The navigator requires reset before it can trust the new stream.'
    elif reason.endswith('_numeric_failure'):
        cause = 'An estimator or controller calculation produced an invalid numeric result.'
        consequence = 'The navigator stopped issuing guidance.'
    elif event.component == 'physics':
        cause = f'The simulator reported an error: {event.reason}.'
        consequence = 'The simulation could not continue normal flight.'
    elif event.component == 'renderer':
        cause = f'The camera renderer reported an error: {event.reason}.'
        consequence = 'Camera images may be missing; this message alone does not prove a control failure.'
    else:
        cause = f'The runtime reported {reason.replace("_", " ")}. The recording does not expose a more specific cause.'
        consequence = ('Normal guidance stopped.' if event.phase == 'FAULT' else
                       'No additional control consequence was recorded.')
    when = 'Time unavailable' if event.time_s is None else f'{event.time_s:.3f} s'
    return '\n\n'.join((f'{when} · {event.reason}', cause, *facts, consequence))
