"""
Filtering by a place includes what is inside it.

Locations are a tree — a site holds rooms, a room holds shelves — but an item
is stored against the exact node it sits on, which is the shelf. Matching on
equality therefore asked "what is sitting on the building itself", which is
nothing: choosing the only site in the list returned no items while 219 of
them were inside it.
"""
from app.api.v1.inventory import _location_subtree_ids
from app.models.inventory import InvLocation


def tree(db):
    """site > room > shelf, plus a second site that must not be swept in."""
    site = InvLocation(code="S1", name="NSTP NUST, Islamabad", location_type="internal")
    other = InvLocation(code="S2", name="Elsewhere", location_type="internal")
    db.add_all([site, other])
    db.flush()

    room = InvLocation(code="S1-R1", name="Store room", parent_id=site.id,
                       location_type="internal")
    db.add(room)
    db.flush()

    shelf = InvLocation(code="S1-R1-A", name="Shelf A", parent_id=room.id,
                        location_type="internal")
    far = InvLocation(code="S2-R1", name="Their room", parent_id=other.id,
                      location_type="internal")
    db.add_all([shelf, far])
    db.commit()
    return site, room, shelf, other, far


class TestASiteIncludesItsRooms:

    def test_a_site_reaches_every_depth(self, env):
        db = env.Session()
        try:
            site, room, shelf, _other, _far = tree(db)
            found = _location_subtree_ids(db, site.id)
            assert found == {site.id, room.id, shelf.id}
        finally:
            db.close()

    def test_a_room_reaches_its_shelves_but_not_its_site(self, env):
        db = env.Session()
        try:
            site, room, shelf, _other, _far = tree(db)
            found = _location_subtree_ids(db, room.id)
            assert found == {room.id, shelf.id}
            assert site.id not in found
        finally:
            db.close()

    def test_a_leaf_is_just_itself(self, env):
        db = env.Session()
        try:
            _site, _room, shelf, _other, _far = tree(db)
            assert _location_subtree_ids(db, shelf.id) == {shelf.id}
        finally:
            db.close()

    def test_a_sibling_site_is_not_swept_in(self, env):
        db = env.Session()
        try:
            site, _room, _shelf, other, far = tree(db)
            found = _location_subtree_ids(db, site.id)
            assert other.id not in found and far.id not in found
        finally:
            db.close()


class TestItCannotHang:

    def test_a_cycle_terminates(self, env):
        """
        Nothing should create one, but a tree built from a parent pointer can
        always be given a loop by a bad edit, and a walk that follows it for
        ever takes the whole endpoint with it.
        """
        db = env.Session()
        try:
            a = InvLocation(code="C1", name="A", location_type="internal")
            db.add(a)
            db.flush()
            b = InvLocation(code="C2", name="B", parent_id=a.id, location_type="internal")
            db.add(b)
            db.flush()
            a.parent_id = b.id          # A is now inside B, which is inside A
            db.commit()

            assert _location_subtree_ids(db, a.id) == {a.id, b.id}
        finally:
            db.close()
