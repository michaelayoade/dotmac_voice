"""Feature desired-state models persist (conference, ring group, IVR, queue)."""

from sqlalchemy import select

from app.models.voice import (
    ConferenceRoom,
    IvrMenu,
    Queue,
    RingGroup,
    VoiceDomain,
)


def test_feature_models_persist(db_session):
    dom = VoiceDomain(customer_id="fm-c1", fusionpbx_domain="fm-c1.local")
    db_session.add(dom)
    db_session.flush()
    db_session.add_all(
        [
            ConferenceRoom(voice_domain_id=dom.id, number="3001"),
            RingGroup(voice_domain_id=dom.id, number="2000", members=["1002", "1003"]),
            IvrMenu(voice_domain_id=dom.id, number="4000", options={"1": "1002"}),
            Queue(voice_domain_id=dom.id, number="5000", agents=["1002"], name="Support"),
        ]
    )
    db_session.flush()

    rg = db_session.scalar(select(RingGroup).where(RingGroup.voice_domain_id == dom.id))
    assert rg.members == ["1002", "1003"]
    assert rg.strategy == "simultaneous"
    ivr = db_session.scalar(select(IvrMenu).where(IvrMenu.voice_domain_id == dom.id))
    assert ivr.options == {"1": "1002"}
    q = db_session.scalar(select(Queue).where(Queue.voice_domain_id == dom.id))
    assert q.agents == ["1002"] and q.strategy == "ring-all"
