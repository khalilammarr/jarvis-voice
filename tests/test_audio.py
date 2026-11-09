from jarvis.audio import CHIME_WAKE, PlaybackBuffer


class FakeClock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now


def test_read_returns_queued_audio_then_silence():
    buffer = PlaybackBuffer()
    buffer.write(b"\x01\x02\x03\x04")
    out = bytearray(8)
    buffer.read_into(out)
    assert bytes(out) == b"\x01\x02\x03\x04\x00\x00\x00\x00"


def test_reads_are_fifo_across_calls():
    buffer = PlaybackBuffer()
    buffer.write(b"abcdef")
    first, second = bytearray(4), bytearray(4)
    buffer.read_into(first)
    buffer.read_into(second)
    assert bytes(first) == b"abcd" and bytes(second) == b"ef\x00\x00"


def test_clear_drops_pending_audio_for_barge_in():
    buffer = PlaybackBuffer()
    buffer.write(b"\x7f" * 100)
    buffer.clear()
    out = bytearray(4)
    buffer.read_into(out)
    assert bytes(out) == b"\x00" * 4


def test_busy_covers_pending_audio_and_the_echo_tail():
    clock = FakeClock()
    buffer = PlaybackBuffer(tail_s=0.35, clock=clock)
    assert not buffer.busy
    buffer.write(b"\x01\x01")
    assert buffer.busy                      # queued
    buffer.read_into(bytearray(2))
    assert buffer.busy                      # just played: echo tail
    clock.now += 0.36
    assert not buffer.busy                  # tail elapsed


def test_chime_is_valid_int16_pcm():
    assert len(CHIME_WAKE) % 2 == 0 and len(CHIME_WAKE) > 1000
