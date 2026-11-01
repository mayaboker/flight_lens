# Recording configuration tab

## Goal
Let FlightLens users inspect the effective application and physics configuration saved in the selected MCAP episode, including runtime parameter-change events.

## Implementation
- Read physics configuration and runtime parameter-event topics in addition to existing app snapshots.
- Retain distinct immutable snapshots per source/episode without creating metadata-only flights or extending flight duration.
- Add a Recording tab with application/physics source and snapshot-time selectors, searchable categorized parameter values, backend/revision information, and a runtime-change table.
- State that the profile filename is not recorded; never infer a YAML filename or read today's local settings.

## Interfaces
Episode gains optional recorded configuration snapshots and parameter changes. New supported MCAP topics: analysis/physics/config and analysis/app/parameters. Existing recordings without these topics remain usable; no remote or wire-schema changes.

## Verification
Reader tests for distinct snapshots, missing metadata, episode isolation, runtime applied/rejected changes, and metadata-only episode/duration guards. Qt tests for source/time selectors, search/category filtering and missing data. Verification results: the full desktop/xcb suite passed (89 tests); the isolated
recording-tab reader/UI checks passed offscreen (3 tests). A combined offscreen
run completed all 55 assertions but hit the previously observed Qt shutdown heap
failure; no production cleanup changes were introduced to hide that limitation.
The selected 20261101T101405 MCAP was inspected on the desktop with application
limits and physics wind filters. Snapshot values/metadata were visible from the
recording despite the pre-existing SQLite cache. The source distribution and
wheel built successfully. Previews: /tmp/flight-lens-recording-app.png and
/tmp/flight-lens-recording-physics.png.

## Scope/defaults
Read-only configuration inspection. Existing tab indices retained by appending Recording. No YAML profile-path inference, parameter edits or simulation changes.

## Status
Implemented; desktop and focused offscreen verification passed (not committed)

## Implementation commit
Pending
