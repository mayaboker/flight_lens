"""Read-only inspection of the settings actually stored in an MCAP episode."""
from collections.abc import Mapping
import json
import re

from PySide6 import QtCore, QtGui, QtWidgets
from bullet_msgs.parameters import BY_PATH

PARAMETER_COLUMN_WIDTHS = {0: 390, 1: 230, 2: 175, 3: 110}
CHANGE_COLUMN_WIDTHS = {0: 100, 1: 310, 2: 115, 3: 115, 4: 115, 5: 105}
RESULT_COLORS = {'applied': '#70d6ad', 'rejected': '#ff8282'}


def flatten_settings(value, prefix=''):
    """Yield every leaf, including strings, booleans, nulls and list elements."""
    if isinstance(value, Mapping) and value:
        for key, child in value.items():
            yield from flatten_settings(child, f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value, (list, tuple)) and value:
        for index, child in enumerate(value):
            yield from flatten_settings(child, f'{prefix}.{index}')
    elif prefix:
        yield prefix, value


def display_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        value = dict(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def parameter_rows(snapshot):
    rows = []
    if snapshot.role != 'app':
        return []
    for path, value in flatten_settings(snapshot.settings):
        definition = BY_PATH.get(path)
        parts = path.split('.')
        category = (definition.category if definition is not None else
                    '.'.join(parts[:-1][:1]) or 'General')
        rows.append((path, display_value(value), category,
                     definition.units if definition is not None else '',
                     re.split(r'\*{3,}', definition.description)[-1].strip() if definition is not None else ''))
    return sorted(rows)


def time_label(time_s):
    return 'Unavailable' if time_s is None else f'{time_s:.3f}'


class RecordingTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.episode = None
        self.snapshots = ()
        self.rows = []
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel('Open an MCAP to inspect its recorded configuration.')
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.profile = QtWidgets.QLabel('Mission profile: Not recorded\nTransport config: Not recorded\nSimulation config: Not recorded')
        self.profile.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.profile.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.profile.setWordWrap(True)
        layout.addWidget(self.profile)
        controls = QtWidgets.QHBoxLayout()
        self.snapshot = QtWidgets.QComboBox()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search recorded application parameter name…')
        self.category = QtWidgets.QComboBox()
        self.category.addItem('All categories')
        for text, widget in [('Application snapshot time (s)',self.snapshot),
                             ('',self.search), ('Category',self.category)]:
            if text: controls.addWidget(QtWidgets.QLabel(text))
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.provenance = QtWidgets.QLabel('')
        self.provenance.setWordWrap(True)
        self.provenance.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.provenance)
        self.table = QtWidgets.QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['Recorded parameter', 'Value', 'Category', 'Units', 'Description'])
        self._configure_table(self.table, PARAMETER_COLUMN_WIDTHS)
        layout.addWidget(self.table, 3)
        self.count = QtWidgets.QLabel('')
        layout.addWidget(self.count)
        layout.addWidget(QtWidgets.QLabel('Runtime parameter changes · selected episode'))
        self.changes_status = QtWidgets.QLabel('No parameter-change events recorded.')
        layout.addWidget(self.changes_status)
        self.changes = QtWidgets.QTableWidget(0,7)
        self.changes.setHorizontalHeaderLabels(['Time (s)', 'Parameter', 'Old value', 'Requested', 'Effective', 'Result', 'Reason'])
        self._configure_table(self.changes, CHANGE_COLUMN_WIDTHS)
        layout.addWidget(self.changes, 1)
        note = QtWidgets.QLabel('Only application/LOS parameters are shown and searched; simulator physics settings are excluded. '
            'Values come from the recording, not current local YAML files. '
            'Repeated identical snapshots are collapsed. Snapshot time is simulation time. '
            'Units are the stored runtime units; known LOS descriptions come from the shared Bullet catalog. '
            'A missing change-event stream does not prove that no changes occurred.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.snapshot.currentIndexChanged.connect(self._snapshot_changed)
        self.search.textChanged.connect(self._filter)
        self.category.currentTextChanged.connect(self._filter)
        self._set_snapshots()

    @staticmethod
    def _configure_table(table, widths):
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().hide()
        for col, width in widths.items():
            table.setColumnWidth(col,width)
        table.horizontalHeader().setStretchLastSection(True)

    def set_episode(self, episode, recording_path=None):
        self.episode = episode
        self.search.clear()
        name = 'MCAP' if recording_path is None else str(recording_path)
        self.summary.setText(f'{name}\nPhysics: {episode.key[0]} · Episode: {episode.key[1]}')
        self._set_snapshots()
        self.changes.setRowCount(len(episode.parameter_changes))
        for row, change in enumerate(episode.parameter_changes):
            values = (time_label(change.time_s),change.parameter,display_value(change.old_value),
                      display_value(change.requested_value),display_value(change.effective_value),
                      change.result,change.reason)
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 5 and change.result in RESULT_COLORS:
                    item.setForeground(QtGui.QColor(RESULT_COLORS[change.result]))
                self.changes.setItem(row,col,item)
        self.changes_status.setText(f'{len(episode.parameter_changes)} recorded change events (applied and rejected).' if episode.parameter_changes
                                    else 'No runtime parameter-change events recorded for this episode.')

    def _set_snapshots(self):
        self.snapshots = (() if self.episode is None else
                          tuple(s for s in self.episode.configurations if s.role == 'app'))
        self.snapshot.blockSignals(True)
        self.snapshot.clear()
        for index, snapshot in enumerate(self.snapshots):
            self.snapshot.addItem(f'{time_label(snapshot.time_s)} · snapshot {index+1}')
        self.snapshot.setEnabled(bool(self.snapshots))
        self.snapshot.blockSignals(False)
        self._snapshot_changed()

    def _snapshot_changed(self, *_):
        index = self.snapshot.currentIndex()
        if not 0 <= index < len(self.snapshots):
            self.rows = []
            self.provenance.setText('No application configuration snapshots recorded for this episode.')
        else:
            snapshot = self.snapshots[index]
            self.rows = parameter_rows(snapshot)
            control = snapshot.settings.get('control')
            backend = control.get('backend','Not recorded') if isinstance(control,Mapping) else 'Not recorded'
            revisions = ', '.join(f'{key}={value}' for key,value in snapshot.revisions.items() if value) or 'Not recorded'
            topic = 'analysis/app/config'
            self.provenance.setText(f'Backend: {backend} · Topic: {topic}\nSoftware revisions: {revisions}')
        self._show_files(self.snapshots[index] if 0 <= index < len(self.snapshots) else None)
        self.table.setRowCount(len(self.rows))
        for row, values in enumerate(self.rows):
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row,col,item)
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(['All categories', *sorted({row[2] for row in self.rows})])
        self.category.blockSignals(False)
        self._filter()

    def _show_files(self, snapshot):
        files = dict(snapshot.files or {}) if snapshot is not None else {}
        if self.episode is not None:
            for recorded in self.episode.configurations:
                if recorded.role == 'physics' and recorded.files:
                    if 'physics_config' in recorded.files:
                        files['physics_config'] = recorded.files['physics_config']
                        break
        labels = []
        paths = []
        for key, label in [('mission_profile','Mission profile'), ('transport_config','Transport config'),
                           ('physics_config','Simulation config')]:
            path = files.get(key)
            # These are producer paths, possibly from another OS. Never open them.
            name = path.replace('\\','/').rsplit('/',1)[-1] if path else 'Not recorded'
            labels.append(f'{label}: {name}')
            if path:
                paths.append(f'{label}: {path}')
        self.profile.setText('\n'.join(labels))
        self.profile.setToolTip('\n'.join(paths) or 'File origins were not saved in this recording.')

    def _filter(self, *_):
        query = self.search.text().casefold()
        category = self.category.currentText()
        visible = 0
        for row, values in enumerate(self.rows):
            show = query in values[0].casefold() and category in ('All categories',values[2])
            self.table.setRowHidden(row,not show)
            visible += show
        self.count.setText(f'{visible} / {len(self.rows)} recorded application parameters')
