"""Тесты wiring Oracle: органы собираются, контракты корректны."""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import wiring  # noqa: E402
from wiring_builder import build_oracle_organs  # noqa: E402


def test_build_oracle_organs_count():
    organs = build_oracle_organs()
    names = [o.name for o in organs]
    assert names == ["oracle_scan", "oracle_plan", "deliver_oracle"]


def test_oracle_scan_wiring():
    organs = {o.name: o for o in build_oracle_organs()}
    o = organs["oracle_scan"]
    assert o.role == "source"
    assert o.produces == ["project_map"]
    assert o.consumes == []


def test_oracle_plan_wiring():
    organs = {o.name: o for o in build_oracle_organs()}
    o = organs["oracle_plan"]
    assert o.role == "transform"
    assert o.produces == ["plan"]
    assert o.consumes == ["project_map"]


def test_deliver_oracle_wiring():
    organs = {o.name: o for o in build_oracle_organs()}
    o = organs["deliver_oracle"]
    assert o.role == "sink"
    assert o.produces == ["delivered"]
    assert o.consumes == ["plan"]


def test_oracle_organs_not_in_default_build():
    default = wiring.build_organs()
    names = {o.name for o in default}
    assert "oracle_scan" not in names
    assert "oracle_plan" not in names
    assert "deliver_oracle" not in names
