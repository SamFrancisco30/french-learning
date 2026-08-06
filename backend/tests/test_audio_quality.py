"""The clarity gate: does it pass good audio and stop bad audio?

The thresholds were calibrated against the real library, so the most valuable test here is the
last one — the gate must not reject the material the app is already built from. The rest pin each
individual failure mode, because a gate that silently stops rejecting is worse than no gate: it
reads as a guarantee nobody is checking.
"""

from __future__ import annotations

import textwrap

import pytest

from app.asr.base import ASRResult, ASRSegment, Word
from app.skills.listening import audio_quality as aq


def _segment(idx: int, *, logprob: float, no_speech: float = 0.05, words: int = 8) -> ASRSegment:
    start = idx * 5.0
    return ASRSegment(
        idx=idx,
        start=start,
        end=start + 5.0,
        text=" ".join(f"mot{i}" for i in range(words)),
        words=[Word(f"mot{i}", start + i * 0.1, start + i * 0.1 + 0.09) for i in range(words)],
        avg_logprob=logprob,
        no_speech_prob=no_speech,
    )


def _result(segments: list[ASRSegment]) -> ASRResult:
    return ASRResult(
        text=" ".join(s.text for s in segments),
        segments=segments,
        language="fr",
        duration_s=segments[-1].end if segments else 0.0,
        backend="test",
        model="test",
    )


def _clean(n: int = 20) -> ASRResult:
    """A source resembling the library's own: high confidence, mostly speech, ~1.6 words/s."""
    return _result([_segment(i, logprob=-0.18, no_speech=0.05) for i in range(n)])


class TestAcceptsGoodAudio:
    def test_clean_source_accepts_with_no_complaints(self):
        report = aq.assess_transcript(_clean(), 100.0)
        assert report.verdict == aq.ACCEPT
        assert report.reasons == []
        assert report.ok

    def test_metrics_are_reported_even_when_everything_passes(self):
        # The numbers are the point: a bare "accept" gives a human nothing to sanity-check.
        report = aq.assess_transcript(_clean(), 100.0)
        assert report.metrics["logprob_mean"] == pytest.approx(-0.18)
        assert report.metrics["fraction_non_speech"] == 0.0
        assert report.metrics["words_per_second"] == pytest.approx(1.6)

    def test_one_bad_patch_does_not_condemn_a_good_source(self):
        # A cough, a name, a moment of crosstalk. 1 of 20 segments is under the per-segment floor,
        # which is 5% — below the 15% tolerance.
        segments = [_segment(i, logprob=-0.18) for i in range(19)]
        segments.append(_segment(19, logprob=-1.4))
        report = aq.assess_transcript(_result(segments), 100.0)
        assert report.verdict == aq.ACCEPT


class TestRejectsBadAudio:
    def test_mud_throughout_is_rejected(self):
        report = aq.assess_transcript(
            _result([_segment(i, logprob=-0.9) for i in range(20)]), 100.0
        )
        assert report.verdict == aq.REJECT
        assert not report.ok
        assert "unsure throughout" in report.summary()

    def test_scattered_unintelligible_patches_are_rejected(self):
        # Mean confidence stays respectable, but a fifth of the segments are mud. This is the
        # case the mean alone cannot catch, which is why both tests exist.
        segments = [_segment(i, logprob=-0.05) for i in range(16)]
        segments += [_segment(16 + i, logprob=-1.2) for i in range(4)]
        report = aq.assess_transcript(_result(segments), 100.0)
        assert report.metrics["logprob_mean"] > aq.LOGPROB_MEAN_FLOOR
        assert report.verdict == aq.REJECT
        assert "barely intelligible" in report.summary()

    def test_mostly_music_is_rejected(self):
        report = aq.assess_transcript(
            _result([_segment(i, logprob=-0.18, no_speech=0.9) for i in range(20)]), 100.0
        )
        assert report.verdict == aq.REJECT
        assert "not speech" in report.summary()

    def test_near_silence_is_rejected(self):
        # Two words in a hundred seconds: a video that is not really talking.
        report = aq.assess_transcript(
            _result([_segment(0, logprob=-0.18, words=2)]), 100.0
        )
        assert report.verdict == aq.REJECT
        assert "barely any speech" in report.summary()

    def test_transcript_not_tracking_the_audio_is_rejected(self):
        report = aq.assess_transcript(_result([_segment(0, logprob=-0.18, words=60)]), 10.0)
        assert report.verdict == aq.REJECT
        assert "not tracking" in report.summary()


class TestWarnsWithoutBlocking:
    def test_music_under_the_voice_warns_and_still_proceeds(self):
        # This is lessons 1 and 7 in the real library: news reports with a bed under the speech.
        # They are usable, so the gate must flag them for a human rather than discard them.
        segments = [_segment(i, logprob=-0.16, no_speech=0.9) for i in range(9)]
        segments += [_segment(9 + i, logprob=-0.16, no_speech=0.05) for i in range(11)]
        report = aq.assess_transcript(_result(segments), 100.0)
        assert report.verdict == aq.WARN
        assert report.ok, "a warning must not stop the pipeline"
        assert "music under" in report.summary()

    def test_a_warning_does_not_mask_a_rejection(self):
        # Both conditions trip. The verdict has to be the more serious one.
        segments = [_segment(i, logprob=-0.9, no_speech=0.6) for i in range(20)]
        report = aq.assess_transcript(_result(segments), 100.0)
        assert report.verdict == aq.REJECT


class TestMissingSignals:
    def test_absent_confidence_is_reported_not_assumed_good(self):
        segments = [_segment(i, logprob=-0.18) for i in range(20)]
        for s in segments:
            s.avg_logprob = None
            s.no_speech_prob = None
        report = aq.assess_transcript(_result(segments), 100.0)
        assert report.verdict == aq.ACCEPT  # nothing to reject on
        assert any("no per-segment confidence" in r for r in report.reasons)

    def test_a_backend_without_word_timestamps_is_not_read_as_silence(self):
        # word_count falls back to splitting the text. If it did not, words/s would be 0 and a
        # perfectly good source would be rejected for "barely any speech".
        segments = [_segment(i, logprob=-0.18) for i in range(20)]
        for s in segments:
            s.words = []
        report = aq.assess_transcript(_result(segments), 100.0)
        assert report.metrics["words"] > 0
        assert report.verdict == aq.ACCEPT

    def test_zero_duration_does_not_divide_by_zero(self):
        report = aq.assess_transcript(_result([_segment(0, logprob=-0.18)]), 0.0)
        assert report.metrics["words_per_second"] is None
        assert report.verdict == aq.ACCEPT


class TestLoudnessParsing:
    SUMMARY = textwrap.dedent(
        """\
        [Parsed_ebur128_0 @ 0x8aec30c00] t: 244.899937 TARGET:-23 LUFS    M:-147.1 S: -46.4     I: -99.9 LUFS       LRA:   4.6 LU  FTPK:  -inf dBFS  TPK:  -0.6 dBFS
        [Parsed_ebur128_0 @ 0x8aec30c00] Summary:

          Integrated loudness:
            I:         -21.6 LUFS
            Threshold: -32.4 LUFS

          True peak:
            Peak:       -0.6 dBFS
        """
    )

    def test_reads_the_summary_block_not_the_running_meter(self, tmp_path, monkeypatch):
        # The per-frame lines carry an `I:` too. Taking one of those would report the loudness of
        # a single instant — the fixture puts -99.9 there on purpose.
        import subprocess

        class Proc:
            stderr = self.SUMMARY

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        assert aq.measure_loudness(audio) == {"loudness_lufs": -21.6, "true_peak_dbtp": -0.6}

    def test_running_meter_alone_is_never_taken_as_the_integrated_figure(
        self, tmp_path, monkeypatch
    ):
        # The stronger form of the test above: with the summary block absent — ffmpeg killed
        # mid-file, say — there is no integrated loudness, and the parse must say so rather than
        # report the instantaneous value from the last frame it happened to see.
        import subprocess

        frames = "\n".join(
            f"[Parsed_ebur128_0 @ 0x1] t: {t} TARGET:-23 LUFS M:-147.1 S:-46.4 "
            f"I: -99.9 LUFS LRA: 4.6 LU FTPK: -inf dBFS TPK: -0.6 dBFS"
            for t in (0.1, 0.2, 0.3)
        )

        class Proc:
            stderr = frames

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        assert aq.measure_loudness(audio) == {"loudness_lufs": None, "true_peak_dbtp": None}

    def test_unmeasurable_file_yields_none_rather_than_raising(self, tmp_path, monkeypatch):
        import subprocess

        def boom(*a, **k):
            raise OSError("ffmpeg not found")

        monkeypatch.setattr(subprocess, "run", boom)
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        assert aq.measure_loudness(audio) == {"loudness_lufs": None, "true_peak_dbtp": None}

    def test_missing_ffmpeg_does_not_reject_an_otherwise_good_source(self, tmp_path, monkeypatch):
        import subprocess

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        report = aq.assess(audio, _clean(), 100.0)
        assert report.verdict == aq.ACCEPT

    def test_near_silent_recording_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            aq, "measure_loudness", lambda p: {"loudness_lufs": -52.0, "true_peak_dbtp": -30.0}
        )
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        report = aq.assess(audio, _clean(), 100.0)
        assert report.verdict == aq.REJECT
        assert "too quiet" in report.summary()

    def test_clipping_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            aq, "measure_loudness", lambda p: {"loudness_lufs": -14.0, "true_peak_dbtp": 2.5}
        )
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        report = aq.assess(audio, _clean(), 100.0)
        assert report.verdict == aq.WARN
        assert report.ok

    def test_absent_audio_file_falls_back_to_transcript_signals(self):
        report = aq.assess(None, _clean(), 100.0)
        assert report.verdict == aq.ACCEPT
        assert "loudness_lufs" not in report.metrics


class TestCalibrationAgainstTheRealLibrary:
    """The measured figures from the 18 transcripts the thresholds were derived from.

    If a future change to the thresholds would have thrown out material the app is built from,
    this is what says so. Each row is (mean logprob, fraction of segments over 0.5 no-speech,
    words per second) as measured, with the verdict that was reviewed and accepted at the time.
    """

    MEASURED = [
        (-0.236, 0.000, 3.07, aq.ACCEPT),   # technology B2
        (-0.230, 0.000, 2.31, aq.ACCEPT),   # economics B1
        (-0.226, 0.117, 2.47, aq.ACCEPT),   # society A2
        (-0.223, 0.033, 2.38, aq.ACCEPT),   # biology B2
        (-0.200, 0.036, 1.43, aq.ACCEPT),   # society A2 — the slowest in the library
        (-0.184, 0.333, 3.56, aq.ACCEPT),   # economics B2 — just under the warn line
        (-0.179, 0.452, 2.54, aq.WARN),     # world_news B1 — music bed
        (-0.174, 0.071, 3.63, aq.ACCEPT),   # culture C1
        (-0.162, 0.207, 4.24, aq.ACCEPT),   # culture B2 — the fastest in the library
        (-0.160, 0.543, 3.25, aq.WARN),     # world_news B2 — heaviest music bed
        (-0.138, 0.020, 3.16, aq.ACCEPT),   # geography B2
    ]

    @pytest.mark.parametrize("mean_lp,non_speech,wps,expected", MEASURED)
    def test_library_verdicts_are_stable(self, mean_lp, non_speech, wps, expected):
        n = 1000  # large enough that the fractions land where intended
        loud = round(non_speech * n)
        segments = [
            _segment(i, logprob=mean_lp, no_speech=0.9 if i < loud else 0.05)
            for i in range(n)
        ]
        result = _result(segments)
        duration = result.word_count / wps
        assert aq.assess_transcript(result, duration).verdict == expected

    def test_nothing_in_the_library_is_rejected(self):
        # The headline claim about this gate. Stated as its own test so it fails loudly.
        for mean_lp, non_speech, wps, _ in self.MEASURED:
            n = 1000
            loud = round(non_speech * n)
            segments = [
                _segment(i, logprob=mean_lp, no_speech=0.9 if i < loud else 0.05)
                for i in range(n)
            ]
            result = _result(segments)
            report = aq.assess_transcript(result, result.word_count / wps)
            assert report.ok, f"would have rejected a real lesson: {report.summary()}"
