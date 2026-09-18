"""Two snapshots in, signals out."""
import datetime as dt

from chorus.models import Delegate, Snapshot, TierVerdict
from chorus.pipeline.alerts import compute_alerts

NOW = dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc)


def verdict(tier, probability, stance="hike", delegates=(), n_docs=10):
    return TierVerdict(issue_id="fed-rate", tier=tier, probability=probability,
                       dominant_stance=stance, n_docs=n_docs, n_speakers=n_docs,
                       delegates=list(delegates), headline=f"{tier} says {stance}")


def delegate(leader_id, share=0.3, tier="crowd"):
    return Delegate(id=f"{tier}:{leader_id}", issue_id="fed-rate", tier=tier,
                    leader_id=leader_id, name=leader_id, verdict="we believe",
                    stance="hike", share=share)


def snapshot(blended=0.5, tiers=None, divergences=()):
    return Snapshot(issue_id="fed-rate", ts=NOW, blended_probability=blended,
                    tiers=tiers or {}, divergences=list(divergences))


def kinds(alerts):
    return {a.kind for a in alerts}


def test_no_signal_when_nothing_moved(cfg, issue):
    snap = snapshot(0.5, {"crowd": verdict("crowd", 0.5)})
    assert compute_alerts(cfg, issue, snap, snap) == []


def test_a_consensus_shift_fires_past_the_threshold(cfg, issue):
    before = snapshot(0.40, {"crowd": verdict("crowd", 0.40)})
    after = snapshot(0.60, {"crowd": verdict("crowd", 0.60)})
    assert "consensus_shift" in kinds(compute_alerts(cfg, issue, after, before))


def test_a_stance_flip_is_critical(cfg, issue):
    before = snapshot(0.5, {"crowd": verdict("crowd", 0.5, stance="hold")})
    after = snapshot(0.5, {"crowd": verdict("crowd", 0.5, stance="hike")})
    found = [a for a in compute_alerts(cfg, issue, after, before)
             if a.kind == "stance_flip"]
    assert found and found[0].severity == "critical"


def test_the_public_contradicting_officialdom_is_the_headline_signal(cfg, issue):
    snap = snapshot(0.5, {"official": verdict("official", 0.10, stance="hold"),
                          "crowd": verdict("crowd", 0.70)})
    found = compute_alerts(cfg, issue, snap, None)
    assert "official_contradiction" in kinds(found)
    assert found[0].severity == "critical"


def test_a_new_bloc_is_reported_once_it_is_material(cfg, issue):
    before = snapshot(0.5, {"crowd": verdict("crowd", 0.5, delegates=[delegate("a")])})
    after = snapshot(0.5, {"crowd": verdict(
        "crowd", 0.5, delegates=[delegate("a"), delegate("b", share=0.25)])})
    assert "new_argument" in kinds(compute_alerts(cfg, issue, after, before))


def test_a_marginal_new_bloc_is_not_reported(cfg, issue):
    before = snapshot(0.5, {"crowd": verdict("crowd", 0.5, delegates=[delegate("a")])})
    after = snapshot(0.5, {"crowd": verdict(
        "crowd", 0.5, delegates=[delegate("a"), delegate("b", share=0.02)])})
    assert "new_argument" not in kinds(compute_alerts(cfg, issue, after, before))


def test_blocs_are_compared_by_identity_not_by_display_name(cfg, issue):
    """A panel that keeps its ids cannot invent a 'new' bloc by rewording."""
    old = delegate("hike--services-inflation")
    renamed = delegate("hike--services-inflation")
    renamed.name = "completely different words"
    before = snapshot(0.5, {"crowd": verdict("crowd", 0.5, delegates=[old])})
    after = snapshot(0.5, {"crowd": verdict("crowd", 0.5, delegates=[renamed])})
    assert "new_argument" not in kinds(compute_alerts(cfg, issue, after, before))


def test_a_volume_spike_is_information_not_an_emergency(cfg, issue):
    before = snapshot(0.5, {"crowd": verdict("crowd", 0.5, n_docs=4)})
    after = snapshot(0.5, {"crowd": verdict("crowd", 0.5, n_docs=40)})
    found = [a for a in compute_alerts(cfg, issue, after, before)
             if a.kind == "volume_spike"]
    assert found and found[0].severity == "info"


def test_signals_are_ordered_most_severe_first(cfg, issue):
    snap = snapshot(0.5, {"official": verdict("official", 0.10, stance="hold"),
                          "crowd": verdict("crowd", 0.70)})
    found = compute_alerts(cfg, issue, snap, None)
    order = [a.severity for a in found]
    assert order == sorted(order, key=lambda s: {"critical": 0, "warn": 1,
                                                 "info": 2}[s])
