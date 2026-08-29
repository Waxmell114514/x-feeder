import datetime as dt

from xfeeder.models import CohortVerdict, Delegate, Snapshot
from xfeeder.pipeline.alerts import compute_alerts

NOW = dt.datetime(2026, 8, 29, 12, 0, tzinfo=dt.timezone.utc)


def verdict(cohort, prob, stance="hike", delegates=(), headline="h"):
    return CohortVerdict(
        issue_id="fed-rate", cohort=cohort, probability=prob,
        dominant_stance=stance, stance_shares={stance: 1.0}, headline=headline,
        n_posts=30, n_authors=20, delegates=list(delegates),
    )


def snapshot(ts_offset_h=0, **cohorts):
    return Snapshot(
        issue_id="fed-rate", ts=NOW + dt.timedelta(hours=ts_offset_h),
        cohorts=cohorts,
        blended_probability=(sum(v.probability for v in cohorts.values())
                             / len(cohorts)) if cohorts else None,
    )


def kinds(alerts):
    return {a.kind for a in alerts}


def test_no_alerts_when_nothing_moved(cfg, issue):
    a = snapshot(crowd=verdict("crowd", 0.5))
    b = snapshot(1, crowd=verdict("crowd", 0.51))
    assert compute_alerts(cfg, issue, b, a) == []


def test_consensus_shift_fires_past_the_threshold(cfg, issue):
    a = snapshot(crowd=verdict("crowd", 0.40))
    b = snapshot(1, crowd=verdict("crowd", 0.55))
    assert "consensus_shift" in kinds(compute_alerts(cfg, issue, b, a))


def test_stance_flip_is_critical(cfg, issue):
    a = snapshot(crowd=verdict("crowd", 0.45, stance="hold"))
    b = snapshot(1, crowd=verdict("crowd", 0.48, stance="hike"))
    found = [x for x in compute_alerts(cfg, issue, b, a) if x.kind == "stance_flip"]
    assert found and found[0].severity == "critical"


def test_the_crowd_contradicting_officialdom_is_the_headline_signal(cfg, issue):
    cur = snapshot(official=verdict("official", 0.12, stance="hold"),
                   crowd=verdict("crowd", 0.65))
    found = compute_alerts(cfg, issue, cur, None)
    contradiction = [a for a in found if a.kind == "official_contradiction"]
    assert contradiction and contradiction[0].severity == "critical"


def test_a_new_bloc_is_reported_once_it_is_material(cfg, issue):
    old = verdict("crowd", 0.5, delegates=[
        Delegate(id="1", issue_id="fed-rate", cohort="crowd", name="旧派",
                 verdict="v", stance="hike", share=0.5)])
    new = verdict("crowd", 0.5, delegates=[
        Delegate(id="1", issue_id="fed-rate", cohort="crowd", name="旧派",
                 verdict="v", stance="hike", share=0.5),
        Delegate(id="2", issue_id="fed-rate", cohort="crowd", name="新派",
                 verdict="v2", stance="hike", share=0.3)])
    found = compute_alerts(cfg, issue, snapshot(1, crowd=new), snapshot(crowd=old))
    assert "new_argument" in kinds(found)


def test_a_marginal_new_bloc_is_not_reported(cfg, issue):
    old = verdict("crowd", 0.5, delegates=[])
    new = verdict("crowd", 0.5, delegates=[
        Delegate(id="2", issue_id="fed-rate", cohort="crowd", name="小派",
                 verdict="v", stance="hike", share=0.03)])
    found = compute_alerts(cfg, issue, snapshot(1, crowd=new), snapshot(crowd=old))
    assert "new_argument" not in kinds(found)


def test_alerts_are_ordered_most_severe_first(cfg, issue):
    cur = snapshot(official=verdict("official", 0.10, stance="hold"),
                   crowd=verdict("crowd", 0.70))
    found = compute_alerts(cfg, issue, cur, None)
    severities = [a.severity for a in found]
    assert severities == sorted(severities, key={"critical": 0, "warn": 1,
                                                 "info": 2}.__getitem__)
