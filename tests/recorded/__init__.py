"""Recorded adb transcripts: what a real phone said, replayed without one.

    from recorded import replay, APhoneOnRecord

    phone = APhoneOnRecord(replay("what-status-reads"))
    state = read_device_state(phone)

The code is in `a_transcript.py` and the data is beside it, one JSON Lines file per
recording. `scripts/record_transcripts.py` makes them and needs a phone; everything
that reads them does not.
"""

from recorded.a_transcript import (
    APhoneOnRecord,
    ATranscript,
    NotRecorded,
    recording_what_the_phone_says,
    replay,
    write_a_transcript,
)

__all__ = [
    "APhoneOnRecord",
    "ATranscript",
    "NotRecorded",
    "recording_what_the_phone_says",
    "replay",
    "write_a_transcript",
]
