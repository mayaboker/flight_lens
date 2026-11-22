# MCAP Bagger recording playback

## Goal

Open finalized MCAP Bagger recordings and replay their JPEG camera images with synchronized MAVLink VFR_HUD altitude and ATTITUDE.

## Implementation

- Detect schema-free bagger channels and parse only the supported version-1 MsgPack telemetry envelopes.
- Build a recording-time episode from MCAP `log_time`, retaining image references without loading image bytes.
- Reuse the overview altitude/attitude plots and Tracking tab for frame-driven playback, with recent preceding telemetry readouts.
- Decode JPEG bytes on demand and export images to MP4 on the recording timeline.
- Keep the legacy simulation recording path unchanged and mark unavailable mission analyses clearly.

## Interfaces and defaults

Add `msgpack` as a runtime dependency. Bagger images require `compress: jpeg`; raw images without dimensions are unsupported. Display time starts at the first relevant MCAP message. A replay sample uses preceding telemetry only when no more than 0.5 seconds old.

## Verification

Synthetic MCAP fixtures cover parsing, missing or malformed telemetry, JPEG decoding, frame playback, cursor synchronization and MP4 export. A finalized 2,475-image bagger recording was opened and its image/altitude/attitude streams inspected. The full Flight Lens suite passed (118 tests); a wheel build passed. This standalone checkout cannot resolve its pre-existing `bullet-msgs = { workspace = true }` source with `uv lock`; verification used the shared Bullet workspace environment.

## Status

Implemented. Implementation commit: Pending (no commit requested).
