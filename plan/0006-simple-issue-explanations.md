# Plain-language tracking issue causes

## Goal
Explain why tracking entered an issue, using relevant recorded values and limits rather than restating the resulting navigator state.

## Implementation
- Read recorded application configuration and retain preceding control evidence when constructing issue events.
- Explain persistent limitations from pre-recovery demands, saturation flags, feasibility and recorded timeout; do not mistake slew limiting alone for an entry condition.
- Provide concise cause-specific explanations for visual rejections, tilt/sensor/control faults and timeouts. State missing evidence briefly instead of inventing values.
- Replace the generic trigger/checklist/raw-JSON panel with a short cause, numeric evidence and control consequence.

## Interfaces
Internal explanation context in Event.details; analysis/app/config becomes a recognized input topic. No flight-control, telemetry schema or recording changes.

## Verification
Synthetic records covering cleared recovery diagnostics, combined causes, configured limits, gaps, episodes and missing evidence; issue-format/UI tests; actual latest-recording review and desktop preview. Results: full FlightLens suite passed on X11/xcb (86 tests); focused explanation,
reader and GUI tests passed offscreen (52 tests). The complete offscreen suite's
assertions passed in earlier runs but the process crashed during Qt shutdown;
that combined-backend test limitation remains. No production cleanup changes
were made to mask it. Source distribution and wheel built successfully.

The reviewed 20261101T101405 recording was loaded in the real desktop application.
Frame 1024 explains a 5.60 m/s descent request versus a recorded 3.50 m/s limit,
approximately 0.25 s of limitation, and failed TTC descent feasibility. Frame 1080
reports 4.23 m/s versus the same bound. Frame 1117 explains the recorded 12° LOS
deviation limit without fabricating the unrecorded exact rejected angle.
Screenshot: /tmp/flight-lens-simple-issue-desktop.png.

## Scope/defaults
Read-only retrospective explanation. Use recorded configuration, never today's controller defaults. Keep existing issue detection/navigation and exact frame association.

## Status
Implemented; desktop and focused offscreen verification passed

## Implementation commit
`5e51cc00842e616742764aa7a884b18f9d31b9f3`

## Repository snapshot record
Recorded snapshot: `5e51cc00842e616742764aa7a884b18f9d31b9f3` (flight_lens repository). This is the new local
baseline containing the current implementation, not a recovered historical commit.
