"""Explain entry causes from preceding evidence, not cleared recovery outputs."""
from math import radians

import pytest
from flight_lens.issue_explanation import IssueEvidence, explain_issue
from flight_lens.model import Event
from flight_lens.reader import APP_CONFIG, MISSION, load_flight
from conftest import mission, source, write_mcap


SETTINGS = dict(target_altitude_m=0., vehicle=dict(max_thrust_n=122.625), tuning=dict(
    max_descent_m_s=3.5, max_climb_m_s=5., max_control_gap_s=.1, limitation_timeout_s=.25,
    max_los_deviation_rad=radians(12), max_image_age_s=.1, combined_tilt_limit_rad=radians(30)))


def event(reason, details=None, component='navigation', time_s=1.26):
    return Event(time_s,time_s,1,'warning',component,'Recovery · '+reason if component=='navigation' else reason,
                 'LOS_ACQUIRE',dict(details or {},reason=reason))


def limitation_context(diagnostics, settings=SETTINGS):
    evidence=IssueEvidence()
    if settings is not None:evidence.configure(settings)
    for t in [1.,1.05,1.1,1.15,1.2,1.25]:
        evidence.consume(t,mission(t,reason='control_limited',diagnostics=diagnostics))
    recovery=mission(1.26,phase='LOS_ACQUIRE',reason='persistent_limitation',diagnostics={
        'requested_vertical_speed_m_s':0.,'vertical_limited':False,'descent_feasible':None})
    recovery['navigator_state']='recover'
    return evidence.consume(1.26,recovery)


def test_persistent_limitation_reports_pre_recovery_descent_limit_ttc_and_duration():
    d=dict(requested_vertical_speed_m_s=-5.60,vertical_limited=True,descent_feasible=False,
           optical_ttc_s=.70,altitude_m=15.47)
    text=explain_issue(event('persistent_limitation',dict(explanation_context=limitation_context(d))))
    for expected in ['5.60 m/s down','3.50 m/s down','max_descent_m_s','0.70 s','15.47 m','0.26 s','0.25 s']:
        assert expected in text
    assert 'navigator_state ==' not in text
    assert 'Raw recorded' not in text and 'available' not in text


def test_slew_alone_is_not_reported_as_the_persistent_limitation_trigger():
    context=limitation_context(dict(slew_limited=True,requested_vertical_speed_m_s=-2.))
    text=explain_issue(event('persistent_limitation',dict(explanation_context=context)))
    assert 'does not identify which' in text
    assert 'requested descent was faster' not in text and 'slew' not in text


def test_missing_profile_still_reports_recorded_saturation_without_inventing_defaults():
    context=limitation_context(dict(thrust_saturated=True,thrust_n=99.),settings=None)
    text=explain_issue(event('persistent_limitation',dict(explanation_context=context)))
    assert 'thrust saturation' in text.lower()
    assert '0.25 s' not in text and '122.62' not in text


def test_gap_discards_old_evidence_and_does_not_claim_a_continuous_duration():
    evidence=IssueEvidence();evidence.configure(SETTINGS)
    evidence.consume(1.,mission(1.,reason='control_limited',diagnostics=dict(requested_vertical_speed_m_s=-9.)))
    context=evidence.consume(2.,mission(2.,phase='LOS_ACQUIRE',reason='persistent_limitation'))
    text=explain_issue(event('persistent_limitation',dict(explanation_context=context)))
    assert '9.00' not in text and 'continuous duration was not recorded' in text


@pytest.mark.parametrize('reason,details,expected',[
    ('clipped_target',dict(edge_margins_px=[20,30,0,0]),'right and bottom'),
    ('stale_image',dict(image_age_s=.16),'0.160 s; max_image_age_s: 0.100 s'),
    ('los_deviation',{},'12.00°'),
    ('measured_over_tilt',dict(measured_tilt_rad=radians(35)),'Measured tilt: 35.00°'),
    ('attitude_unaligned',{},'image capture time'),
    ('no_target',{},'detector found no target'),
    ('association_jump',{},'jumped too far between images'),
    ('control_gap',{},'0.100 s'),
    ('los_recovery_timeout',{},'before its deadline'),
])
def test_specific_causes_show_relevant_evidence_only(reason,details,expected):
    details['explanation_context']=dict(settings=SETTINGS)
    text=explain_issue(event(reason,details))
    assert expected in text
    assert 'Raw recorded' not in text and 'Trigger condition:' not in text
    assert 'dx:' not in text and 'dy:' not in text


def test_angle_and_thrust_saturation_are_reported_with_recorded_limits():
    context=limitation_context(dict(angle_limited=True,requested_tilt_rad=radians(40),
                                    thrust_saturated=True,thrust_n=122.625))
    text=explain_issue(event('persistent_limitation',dict(explanation_context=context)))
    assert '40.00°' in text and '30.00°' in text and '122.62 N' in text


def config(t, settings=SETTINGS, episode='episode'):
    return dict(schema_version=1,role='app',source=source(t,episode=episode),settings=settings,revisions={})


def test_reader_keeps_pre_transition_evidence_and_uses_recorded_profile(tmp_path):
    rows=[(APP_CONFIG,.1,config(.1))]
    for t in [1.,1.05,1.1,1.15,1.2,1.25]:
        rows.append((MISSION,t,mission(t,reason='control_limited',diagnostics=dict(
            requested_vertical_speed_m_s=-5.6,vertical_limited=True))))
    recovery=mission(1.26,phase='LOS_ACQUIRE',reason='persistent_limitation',diagnostics=dict(
        requested_vertical_speed_m_s=0.,vertical_limited=False))
    recovery['navigator_state']='recover'
    rows.append((MISSION,1.26,recovery))
    # A later profile must not replace the limits used for this earlier event.
    rows.append((APP_CONFIG,2.,config(2.,dict(tuning=dict(max_descent_m_s=9.)))))
    path=write_mcap(tmp_path/'cause.mcap',rows)
    ep=next(e for e in load_flight(path).episodes if e.key[1]=='episode')
    issue=next(e for e in ep.events if e.component=='navigation')
    text=explain_issue(issue)
    assert '5.60 m/s down' in text and '3.50 m/s down' in text and '0.26 s' in text
    assert '9.00' not in text


def test_config_and_evidence_do_not_leak_between_episodes(tmp_path):
    first=mission(1.,phase='LOS_ACQUIRE',reason='persistent_limitation')
    first['navigator_state']='recover'
    second=mission(2.,phase='LOS_ACQUIRE',reason='persistent_limitation',episode='other')
    second['navigator_state']='recover'
    path=write_mcap(tmp_path/'episodes.mcap',[(APP_CONFIG,.1,config(.1)),(MISSION,1.,first),(MISSION,2.,second)])
    episodes=load_flight(path).episodes
    ep=next(e for e in episodes if e.key[1]=='other')
    text=explain_issue(next(e for e in ep.events if e.component=='navigation'))
    assert '0.25 s' not in text and 'does not identify which' in text


def test_malformed_optional_configuration_does_not_crash_explanation():
    evidence=IssueEvidence();evidence.configure(dict(tuning='bad',vehicle=None))
    context=evidence.consume(1.,mission(1.))
    text=explain_issue(event('persistent_limitation',dict(explanation_context=context)))
    assert 'does not identify which' in text


def test_configuration_only_episode_does_not_create_a_phantom_flight(tmp_path):
    path=write_mcap(tmp_path/'config-only-episode.mcap',[
        (APP_CONFIG,.1,config(.1,episode='old')), (MISSION,0.,mission(0.)), (MISSION,1.,mission(1.)),
        (APP_CONFIG,9.,config(9.))])
    episodes=load_flight(path).episodes
    assert len(episodes)==1 and episodes[0].key[1]=='episode'
    assert episodes[0].summary.duration_s==1.
