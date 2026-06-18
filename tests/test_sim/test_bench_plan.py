"""Tests for the Phase 1 testbench plan data model."""

from __future__ import annotations

import json

import pytest

from veriforge.sim.bench import (
    ClockDomain,
    ClockSpec,
    InterfaceBinding,
    PlanValidationError,
    ResetSpec,
    TestbenchPlan,
)


# ---------------------------------------------------------------------------
# ClockSpec
# ---------------------------------------------------------------------------


class TestClockSpec:
    def test_defaults(self) -> None:
        c = ClockSpec(name="clk")
        assert c.name == "clk"
        assert c.edge == "posedge"
        assert c.period_hint is None

    def test_custom_edge_and_period(self) -> None:
        c = ClockSpec(name="aclk", edge="negedge", period_hint=10)
        assert c.edge == "negedge"
        assert c.period_hint == 10

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            ClockSpec(name="")

    def test_invalid_edge_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            ClockSpec(name="clk", edge="bothedge")

    def test_non_positive_period_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            ClockSpec(name="clk", period_hint=0)
        with pytest.raises(PlanValidationError):
            ClockSpec(name="clk", period_hint=-5)

    def test_frozen(self) -> None:
        c = ClockSpec(name="clk")
        with pytest.raises((AttributeError, TypeError)):
            c.name = "other"  # type: ignore[misc]

    def test_equality_and_hash(self) -> None:
        a = ClockSpec(name="clk", period_hint=10)
        b = ClockSpec(name="clk", period_hint=10)
        assert a == b
        assert hash(a) == hash(b)
        assert a != ClockSpec(name="clk", period_hint=20)

    def test_to_dict_roundtrips_through_json(self) -> None:
        c = ClockSpec(name="clk", edge="posedge", period_hint=8)
        assert json.loads(json.dumps(c.to_dict())) == {
            "name": "clk",
            "edge": "posedge",
            "period_hint": 8,
        }


# ---------------------------------------------------------------------------
# ResetSpec
# ---------------------------------------------------------------------------


class TestResetSpec:
    def test_sync_active_high(self) -> None:
        r = ResetSpec(name="rst", active_low=False, style="sync")
        assert r.assert_level == 1
        assert r.release_level == 0
        assert r.edge is None

    def test_sync_active_low(self) -> None:
        r = ResetSpec(name="rst_n", active_low=True, style="sync")
        assert r.assert_level == 0
        assert r.release_level == 1

    def test_async_requires_edge(self) -> None:
        with pytest.raises(PlanValidationError):
            ResetSpec(name="arst", active_low=False, style="async")

    def test_async_with_edge_ok(self) -> None:
        r = ResetSpec(name="arst_n", active_low=True, style="async", edge="negedge")
        assert r.style == "async"
        assert r.edge == "negedge"

    def test_sync_rejects_edge(self) -> None:
        with pytest.raises(PlanValidationError):
            ResetSpec(name="rst", active_low=False, style="sync", edge="posedge")

    def test_invalid_style_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            ResetSpec(name="rst", active_low=False, style="weird")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            ResetSpec(name="", active_low=False)

    def test_to_dict(self) -> None:
        r = ResetSpec(name="aresetn", active_low=True, style="async", edge="negedge")
        assert r.to_dict() == {
            "name": "aresetn",
            "active_low": True,
            "style": "async",
            "edge": "negedge",
        }


# ---------------------------------------------------------------------------
# ClockDomain
# ---------------------------------------------------------------------------


class TestClockDomain:
    def test_minimal(self) -> None:
        d = ClockDomain(name="clk", clock=ClockSpec(name="clk"))
        assert d.reset is None

    def test_with_reset(self) -> None:
        d = ClockDomain(
            name="axi",
            clock=ClockSpec(name="aclk", period_hint=10),
            reset=ResetSpec(name="aresetn", active_low=True, style="sync"),
        )
        assert d.reset is not None
        assert d.reset.assert_level == 0

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            ClockDomain(name="", clock=ClockSpec(name="clk"))

    def test_to_dict_includes_reset_none(self) -> None:
        d = ClockDomain(name="clk", clock=ClockSpec(name="clk"))
        assert d.to_dict()["reset"] is None


# ---------------------------------------------------------------------------
# InterfaceBinding
# ---------------------------------------------------------------------------


def _signals() -> dict[str, str]:
    return {
        "tvalid": "m_axis_tvalid",
        "tready": "m_axis_tready",
        "tdata": "m_axis_tdata",
        "tlast": "m_axis_tlast",
    }


class TestInterfaceBinding:
    def test_basic_construction(self) -> None:
        b = InterfaceBinding(
            prefix="m_axis",
            protocol="axi_stream",
            role="master",
            domain_name="aclk",
            signals=_signals(),
        )
        assert b.signals["tvalid"] == "m_axis_tvalid"
        assert b.confidence == "naming"

    def test_signals_are_immutable_mapping(self) -> None:
        b = InterfaceBinding(
            prefix="m_axis",
            protocol="axi_stream",
            role="master",
            domain_name="aclk",
            signals=_signals(),
        )
        with pytest.raises(TypeError):
            b.signals["tvalid"] = "other"  # type: ignore[index]
        # Must remain mapping-iterable.
        assert dict(b.signals)["tdata"] == "m_axis_tdata"
        assert sorted(b.signals.keys()) == sorted(_signals().keys())

    def test_role_validation(self) -> None:
        with pytest.raises(PlanValidationError):
            InterfaceBinding(prefix="m", protocol="axi_stream", role="other", domain_name="clk")

    def test_confidence_validation(self) -> None:
        with pytest.raises(PlanValidationError):
            InterfaceBinding(
                prefix="m",
                protocol="axi_stream",
                role="master",
                domain_name="clk",
                confidence="guess",
            )

    def test_required_fields(self) -> None:
        for kwargs in (
            {"prefix": "", "protocol": "axi_stream", "role": "master", "domain_name": "clk"},
            {"prefix": "m", "protocol": "", "role": "master", "domain_name": "clk"},
            {"prefix": "m", "protocol": "axi_stream", "role": "master", "domain_name": ""},
        ):
            with pytest.raises(PlanValidationError):
                InterfaceBinding(**kwargs)

    def test_equality_independent_of_signal_dict_order(self) -> None:
        a = InterfaceBinding(
            prefix="m_axis",
            protocol="axi_stream",
            role="master",
            domain_name="aclk",
            signals={"tdata": "m_axis_tdata", "tvalid": "m_axis_tvalid"},
        )
        b = InterfaceBinding(
            prefix="m_axis",
            protocol="axi_stream",
            role="master",
            domain_name="aclk",
            signals={"tvalid": "m_axis_tvalid", "tdata": "m_axis_tdata"},
        )
        assert a == b
        assert hash(a) == hash(b)

    def test_to_dict(self) -> None:
        b = InterfaceBinding(
            prefix="m_axis",
            protocol="axi_stream",
            role="master",
            domain_name="aclk",
            signals=_signals(),
            confidence="structural",
        )
        d = b.to_dict()
        assert d["prefix"] == "m_axis"
        assert d["confidence"] == "structural"
        assert d["signals"] == _signals()


# ---------------------------------------------------------------------------
# TestbenchPlan
# ---------------------------------------------------------------------------


def _domain(name: str = "clk", *, with_reset: bool = True) -> ClockDomain:
    reset = ResetSpec(name="rst_n", active_low=True, style="sync") if with_reset else None
    return ClockDomain(name=name, clock=ClockSpec(name=name, period_hint=10), reset=reset)


def _binding(prefix: str, domain: str = "clk") -> InterfaceBinding:
    return InterfaceBinding(
        prefix=prefix,
        protocol="axi_stream",
        role="master",
        domain_name=domain,
        signals={"tvalid": f"{prefix}_tvalid", "tdata": f"{prefix}_tdata"},
    )


class TestTestbenchPlan:
    def test_minimal(self) -> None:
        p = TestbenchPlan(top="dut")
        assert p.top == "dut"
        assert p.domains == ()
        assert p.interfaces == ()
        assert not p.has_warnings()

    def test_lookup_helpers(self) -> None:
        d_clk = _domain("clk")
        d_aclk = _domain("aclk", with_reset=False)
        b1 = _binding("m_axis", "aclk")
        b2 = _binding("s_axis", "clk")
        p = TestbenchPlan(top="dut", domains=(d_clk, d_aclk), interfaces=(b1, b2))

        assert p.domain("aclk") is d_aclk
        assert p.interface("m_axis") is b1
        assert p.interfaces_for_domain("aclk") == (b1,)
        assert p.interfaces_for_domain("clk") == (b2,)
        assert p.interfaces_for_domain("missing") == ()

        with pytest.raises(KeyError):
            p.domain("nope")
        with pytest.raises(KeyError):
            p.interface("nope")

    def test_iterable_inputs_are_coerced(self) -> None:
        p = TestbenchPlan(
            top="dut",
            domains=[_domain("clk")],
            interfaces=[_binding("m_axis")],
            warnings=["w1"],
            overrides_applied=["m_axis.clock"],
        )
        assert isinstance(p.domains, tuple)
        assert isinstance(p.interfaces, tuple)
        assert p.warnings == ("w1",)
        assert p.overrides_applied == ("m_axis.clock",)

    def test_empty_top_rejected(self) -> None:
        with pytest.raises(PlanValidationError):
            TestbenchPlan(top="")

    def test_duplicate_domain_name_rejected(self) -> None:
        with pytest.raises(PlanValidationError, match="duplicate domain"):
            TestbenchPlan(top="dut", domains=(_domain("clk"), _domain("clk", with_reset=False)))

    def test_duplicate_clock_signal_rejected(self) -> None:
        d1 = ClockDomain(name="a", clock=ClockSpec(name="clk"))
        d2 = ClockDomain(name="b", clock=ClockSpec(name="clk"))
        with pytest.raises(PlanValidationError, match="more than one domain"):
            TestbenchPlan(top="dut", domains=(d1, d2))

    def test_unknown_interface_domain_rejected(self) -> None:
        with pytest.raises(PlanValidationError, match="unknown domain"):
            TestbenchPlan(
                top="dut",
                domains=(_domain("clk"),),
                interfaces=(_binding("m_axis", "missing"),),
            )

    def test_duplicate_interface_prefix_rejected(self) -> None:
        with pytest.raises(PlanValidationError, match="duplicate interface"):
            TestbenchPlan(
                top="dut",
                domains=(_domain("clk"),),
                interfaces=(_binding("m_axis"), _binding("m_axis")),
            )

    def test_equality(self) -> None:
        p1 = TestbenchPlan(top="dut", domains=(_domain("clk"),), interfaces=(_binding("m_axis"),))
        p2 = TestbenchPlan(top="dut", domains=(_domain("clk"),), interfaces=(_binding("m_axis"),))
        assert p1 == p2

    def test_to_dict_roundtrips_through_json(self) -> None:
        p = TestbenchPlan(
            top="dut",
            domains=(_domain("clk"), _domain("aclk", with_reset=False)),
            interfaces=(_binding("m_axis", "aclk"), _binding("s_axis", "clk")),
            warnings=("ambiguity on s_axis",),
            overrides_applied=("s_axis.clock",),
        )
        encoded = json.dumps(p.to_dict())
        decoded = json.loads(encoded)
        assert decoded["top"] == "dut"
        assert {d["name"] for d in decoded["domains"]} == {"clk", "aclk"}
        assert {i["prefix"] for i in decoded["interfaces"]} == {"m_axis", "s_axis"}
        assert decoded["warnings"] == ["ambiguity on s_axis"]
        assert decoded["overrides_applied"] == ["s_axis.clock"]

    def test_summary_mentions_key_facts(self) -> None:
        p = TestbenchPlan(
            top="dut",
            domains=(_domain("clk"),),
            interfaces=(_binding("m_axis"),),
            warnings=("watch out",),
            overrides_applied=("m_axis.domain",),
        )
        text = p.summary()
        assert "TestbenchPlan(top='dut')" in text
        assert "clk" in text
        assert "m_axis" in text
        assert "axi_stream" in text
        assert "watch out" in text
        assert "m_axis.domain" in text

    def test_summary_handles_empty_design(self) -> None:
        p = TestbenchPlan(top="dut")
        text = p.summary()
        assert "<none>" in text
