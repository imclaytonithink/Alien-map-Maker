"""Regression tests for compressed/coalescing history and viewport culling."""
from __future__ import annotations

from core.history import History
from core.project import Layer, Piece, new_project, embed_png


def main():
    # -- history round trip with a large embedded image kept as a shared blob
    project = new_project()
    level = project.levels[0]
    blob = "A" * 5000
    level.add(Piece(name="img", w=10, h=10, embedded=blob))
    clock = [0.0]
    hist = History(clock=lambda: clock[0])
    hist.push(project, "one")
    level.pieces[0].x = 50
    hist.push(project, "two")
    assert len(hist._blobs) == 1, "embedded blob must be stored once"
    level.pieces[0].x = 99
    assert hist.undo(project) == "two"
    assert project.levels[0].pieces[0].x == 50
    assert project.levels[0].pieces[0].embedded == blob
    assert hist.redo(project) == "two"
    assert project.levels[0].pieces[0].x == 99
    hist.undo(project); hist.undo(project)
    assert project.levels[0].pieces[0].x == 0
    assert not hist.can_undo() and hist.can_redo()
    assert hist.undos == [] and hist.redos[-1][0] == "one"

    # -- coalescing: continuous slider edits become one undo step
    project = new_project()
    hist = History(clock=lambda: clock[0])
    for i in range(10):
        clock[0] += 0.1
        hist.push(project, "Edit opacity", coalesce=True)
    assert len(hist.undos) == 1
    clock[0] += 5.0                      # pause -> a new step
    hist.push(project, "Edit opacity", coalesce=True)
    assert len(hist.undos) == 2
    hist.push(project, "Edit opacity")   # not coalescing -> always a step
    assert len(hist.undos) == 3
    hist.push(project, "Other", coalesce=True)
    hist.push(project, "Edit opacity", coalesce=True)
    assert len(hist.undos) == 5          # different labels never merge

    # blobs of evicted snapshots are released
    project = new_project()
    project.levels[0].add(Piece(w=1, h=1, embedded="B" * 1000))
    small = History(limit=2, clock=lambda: clock[0])
    small.push(project, "a")
    project.levels[0].pieces.clear()
    small.push(project, "b"); small.push(project, "c")
    assert not small._blobs

    # -- viewport culling keeps draw order and drops off-screen pieces
    project = new_project()
    level = project.levels[0]
    near = Piece(x=10, y=10, w=20, h=20)
    far = Piece(x=5000, y=5000, w=20, h=20)
    level.add(near); level.add(far)
    assert level.paint_order() == [near, far]
    assert level.paint_order((0, 0, 200, 200)) == [near]
    level.layers[0].visible = False
    assert level.paint_order() == []

    # -- new layer color label round trips
    project = new_project()
    project.levels[0].layers[0].color = "#4a90e2"
    from core.project import Project
    again = Project.from_dict(project.to_dict())
    assert again.levels[0].layers[0].color == "#4a90e2"
    print("Batch 11 history, culling and layer-label checks passed.")


if __name__ == "__main__":
    main()
