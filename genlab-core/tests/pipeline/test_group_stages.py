"""Tests for the stage grouping logic used by pipeline_runner.

The grouping algorithm is simple: consecutive stages with the same
``parallel_group`` value are merged into batches. We test it as a
standalone function rather than importing from CriticalRush.
"""

from __future__ import annotations


def _group_stages(stages, declarations):
    """Mirror of PipelineRunner._group_stages — pure function."""
    batches = []
    current_group = None
    current_batch = []

    for decl, stage in zip(declarations, stages, strict=False):
        group = decl.get("parallel_group")

        if group and group == current_group:
            current_batch.append((decl, stage))
        else:
            if current_batch:
                batches.append(current_batch)
            current_batch = [(decl, stage)]
            current_group = group

    if current_batch:
        batches.append(current_batch)

    return batches


class FakeStage:
    pass


class TestGroupStages:
    def _group(self, decls, stages=None):
        if stages is None:
            stages = [FakeStage() for _ in decls]
        return _group_stages(stages, decls)

    def test_all_sequential(self):
        decls = [{"class": "A"}, {"class": "B"}, {"class": "C"}]
        batches = self._group(decls)
        assert len(batches) == 3
        assert all(len(b) == 1 for b in batches)

    def test_single_parallel_group(self):
        decls = [
            {"class": "A"},
            {"class": "B", "parallel_group": "post_render"},
            {"class": "C", "parallel_group": "post_render"},
            {"class": "D"},
        ]
        batches = self._group(decls)
        assert len(batches) == 3
        assert len(batches[0]) == 1  # A
        assert len(batches[1]) == 2  # B + C
        assert len(batches[2]) == 1  # D

    def test_multiple_parallel_groups(self):
        decls = [
            {"class": "A", "parallel_group": "fetch"},
            {"class": "B", "parallel_group": "fetch"},
            {"class": "C"},
            {"class": "D", "parallel_group": "render"},
            {"class": "E", "parallel_group": "render"},
            {"class": "F", "parallel_group": "render"},
        ]
        batches = self._group(decls)
        assert len(batches) == 3
        assert len(batches[0]) == 2  # A + B (fetch)
        assert len(batches[1]) == 1  # C
        assert len(batches[2]) == 3  # D + E + F (render)

    def test_non_consecutive_same_group_separate(self):
        """Same group name but not consecutive -> separate batches."""
        decls = [
            {"class": "A", "parallel_group": "g1"},
            {"class": "B"},
            {"class": "C", "parallel_group": "g1"},
        ]
        batches = self._group(decls)
        assert len(batches) == 3
        assert len(batches[0]) == 1
        assert len(batches[1]) == 1
        assert len(batches[2]) == 1

    def test_empty_input(self):
        batches = self._group([], [])
        assert batches == []

    def test_all_same_group(self):
        decls = [
            {"class": "A", "parallel_group": "all"},
            {"class": "B", "parallel_group": "all"},
            {"class": "C", "parallel_group": "all"},
        ]
        batches = self._group(decls)
        assert len(batches) == 1
        assert len(batches[0]) == 3
