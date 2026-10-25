from evals.calibration import _weighted_stats, human_labels


def _it(stratum, h, j):
    return {"stratum": stratum, "h": {"bad": h}, "j": {"bad": j}}


def test_human_labels_unsure_is_none():
    h = human_labels({"answer_correct": "unsure", "unique": "y", "faithful": "y", "halluc": "n", "on_topic": "y"})
    assert h["answer_wrong"] is None and h["bad"] is None


def test_human_labels_bad_if_any_problem():
    h = human_labels({"answer_correct": "yes", "unique": "n", "faithful": "y", "halluc": "n", "on_topic": "y"})
    assert h["not_unique"] is True and h["bad"] is True


def test_weighted_stats_reweights_strata():
    # flagged 层 2 题（1 真坏），random 层 2 题（1 漏检）；权重 1 vs 9
    items = [_it("flagged", True, True), _it("flagged", False, True),
             _it("random", True, False), _it("random", False, False)]
    st = _weighted_stats(items, {"flagged": 1.0, "random": 9.0}, "bad")
    assert st["precision"] == 0.5
    assert abs(st["recall"] - 1 / 10) < 1e-9
    assert abs(st["human_rate"] - 0.5) < 1e-9
    assert abs(st["agreement"] - 0.5) < 1e-9
