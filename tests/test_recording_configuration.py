"""Recorded configuration, historical snapshots and read-only table behavior."""
from dataclasses import replace

import pytest
from PySide6 import QtWidgets
from flight_lens.reader import (APP_CONFIG, PHYSICS_CONFIG, PARAMETERS, MISSION, load_flight)
from flight_lens.recording_view import RecordingTab
from conftest import source, mission, write_mcap


def configuration(t, settings, role='app', episode='episode'):
    return dict(schema_version=1,source=source(t,episode=episode),role=role,settings=settings,
                revisions=dict(bullet_app='abc123' if role=='app' else None))


def change(t, result='applied', episode='episode'):
    return dict(schema_version=1,event_id=f'{episode}-{t}',result=result,
        parameter_id='B_TEST',parameter_path='tuning.max_descent_m_s',old_value=3.5,
        requested_value=5.5 if result=='applied' else -1.,effective_value=5.5 if result=='applied' else 3.5,
        reason='' if result=='applied' else 'invalid value',requester_system=255,requester_component=190,
        config_revision=1,phase='READY',applied_at_s=1000.+t,source=source(t,episode=episode))


@pytest.fixture
def recorded_file(tmp_path):
    initial=dict(tuning=dict(max_descent_m_s=3.5),control=dict(backend='internal',connection=dict(path=None)),
                 camera=dict(camera_to_body_xyzw=[0.,0.,0.,1.]),enabled=True,label='recorded profile')
    updated=dict(initial,tuning=dict(max_descent_m_s=5.5))
    physics=dict(simulation=dict(control=dict(backend='internal'),wind=dict(enabled=True,velocity_world_m_s=[-1.,-.5,0.])),
                 vehicle=dict(mass_kg=5.))
    orphan=change(.05);orphan['source']=None
    return write_mcap(tmp_path/'configuration.mcap',[
        (MISSION,0.,mission(0.)),(PARAMETERS,.05,orphan),
        (APP_CONFIG,.1,configuration(.1,initial)),(PHYSICS_CONFIG,.11,configuration(.11,physics,'physics')),
        (APP_CONFIG,.2,configuration(.2,initial)),(PARAMETERS,1.,change(1.)),
        (APP_CONFIG,1.1,configuration(1.1,updated)),(PARAMETERS,1.2,change(1.2,'rejected')),
        (PHYSICS_CONFIG,1.3,configuration(1.3,physics,'physics')),(MISSION,2.,mission(2.)),
        (APP_CONFIG,99.,configuration(99.,updated)),
        (APP_CONFIG,100.,configuration(100.,dict(tuning=dict(max_descent_m_s=99.)),episode='foreign')),
    ])


def test_reader_retains_distinct_typed_configuration_and_events(recorded_file):
    data=load_flight(recorded_file)
    assert len(data.episodes)==1
    ep=data.episodes[0]
    assert ep.summary.duration_s==2.
    app=[s for s in ep.configurations if s.role=='app']
    physics=[s for s in ep.configurations if s.role=='physics']
    assert [s.time_s for s in app]==[.1,1.1]
    assert [s.settings['tuning']['max_descent_m_s'] for s in app]==[3.5,5.5]
    assert len(physics)==1 and physics[0].settings['simulation']['wind']['enabled'] is True
    assert app[0].settings['control']['connection']['path'] is None
    assert app[0].settings['label']=='recorded profile'
    assert tuple(app[0].settings['camera']['camera_to_body_xyzw'])==(0.,0.,0.,1.)
    with pytest.raises(TypeError):app[0].settings['tuning']['max_descent_m_s']=99.
    assert [c.result for c in ep.parameter_changes]==['applied','rejected']
    assert ep.parameter_changes[0].time_s==1. and ep.parameter_changes[1].requested_value==-1.


@pytest.fixture(scope='session')
def qt_application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def value_in(table,path):
    return next(table.item(row,1).text() for row in range(table.rowCount()) if table.item(row,0).text()==path)


def test_recording_tab_selects_application_time_search_and_changes(qt_application,recorded_file):
    episode=load_flight(recorded_file).episodes[0]
    tab=RecordingTab()
    try:
        tab.set_episode(episode,recorded_file)
        assert 'Not recorded' in tab.profile.text()
        assert 'internal' in tab.provenance.text() and 'abc123' in tab.provenance.text()
        assert value_in(tab.table,'tuning.max_descent_m_s')=='3.5'
        descent_row=next(row for row in range(tab.table.rowCount())
                         if tab.table.item(row,0).text()=='tuning.max_descent_m_s')
        assert '***' not in tab.table.item(descent_row,4).text()
        assert 'Maximum downward speed' in tab.table.item(descent_row,4).text()
        assert value_in(tab.table,'control.connection.path')=='null'
        tab.snapshot.setCurrentIndex(1)
        assert value_in(tab.table,'tuning.max_descent_m_s')=='5.5'
        tab.search.setText('MAX_DESCENT')
        assert sum(not tab.table.isRowHidden(i) for i in range(tab.table.rowCount()))==1
        tab.category.setCurrentText('Camera')
        assert all(tab.table.isRowHidden(i) for i in range(tab.table.rowCount()))
        tab.category.setCurrentText('All categories')
        tab.search.setText('wind')
        assert all(tab.table.isRowHidden(i) for i in range(tab.table.rowCount()))
        assert not hasattr(tab,'source')
        assert all(not row[0].startswith('simulation.') for row in tab.rows)
        assert tab.changes.rowCount()==2
        assert tab.changes.item(0,0).text()=='1.000'
        assert tab.changes.item(0,5).text()=='applied'
        assert tab.changes.item(1,5).text()=='rejected'
        assert tab.table.editTriggers()==QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        assert tab.changes.editTriggers()==QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        tab.set_episode(replace(episode,key=('physics','other'),configurations=(),parameter_changes=()))
        assert tab.table.rowCount()==0 and tab.changes.rowCount()==0
        assert not tab.snapshot.isEnabled()
        assert 'No application configuration snapshots' in tab.provenance.text()
        assert 'other' in tab.summary.text()
    finally:tab.close()


def test_legacy_recording_without_configurations_is_still_usable(qt_application,flight_file):
    episode=load_flight(flight_file).episodes[0]
    assert not episode.configurations and not episode.parameter_changes
    tab=RecordingTab()
    try:
        tab.set_episode(episode,flight_file)
        assert tab.table.rowCount()==0
        assert 'No application configuration snapshots' in tab.provenance.text()
        assert 'No runtime parameter-change events' in tab.changes_status.text()
    finally:tab.close()


def test_recorded_file_names_are_read_without_opening_current_files(qt_application,tmp_path):
    app=configuration(.1,dict(tuning=dict(max_descent_m_s=5.5),control=dict(backend='betaflight')))
    app['files']={'mission_profile':'/remote/old/profile-heavy.yaml','transport_config':'/remote/transport.json'}
    physics=configuration(.2,dict(simulation=dict(wind=dict(enabled=True))),'physics')
    physics['files']={'physics_config':'/remote/heavy-quad.yaml'}
    renamed=dict(app,source=source(.3),files=dict(app['files'],mission_profile='/remote/new-profile.yaml'))
    path=write_mcap(tmp_path/'file-origins.mcap',[(MISSION,0.,mission(0.)),(APP_CONFIG,.1,app),
        (PHYSICS_CONFIG,.2,physics),(APP_CONFIG,.3,renamed),(MISSION,1.,mission(1.))])
    ep=load_flight(path).episodes[0]
    assert len([s for s in ep.configurations if s.role=='app'])==2
    tab=RecordingTab()
    try:
        tab.set_episode(ep,path)
        assert 'profile-heavy.yaml' in tab.profile.text()
        assert 'transport.json' in tab.profile.text()
        assert 'heavy-quad.yaml' in tab.profile.text()
        assert '/remote/old/profile-heavy.yaml' in tab.profile.toolTip()
        assert all('wind' not in row[0] for row in tab.rows)
        tab.snapshot.setCurrentIndex(1)
        assert 'new-profile.yaml' in tab.profile.text()
        assert value_in(tab.table,'tuning.max_descent_m_s')=='5.5'
    finally:tab.close()


def test_physics_only_recording_does_not_expose_simulator_parameters(qt_application,tmp_path):
    physics=configuration(.1,dict(simulation=dict(wind=dict(enabled=True))),'physics')
    physics['files']={'physics_config':'/remote/simulation.yaml'}
    path=write_mcap(tmp_path/'physics-only.mcap',[(MISSION,0.,mission(0.)),(PHYSICS_CONFIG,.1,physics)])
    tab=RecordingTab()
    try:
        tab.set_episode(load_flight(path).episodes[0],path)
        assert tab.table.rowCount()==0 and not tab.snapshot.isEnabled()
        assert 'No application configuration' in tab.provenance.text()
        assert 'simulation.yaml' in tab.profile.text()
    finally:tab.close()
