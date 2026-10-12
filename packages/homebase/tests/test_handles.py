from homebase.handles import make_handle, matches, project_name, worktree_slug

SID = "19c3d05a-b567-4dc6-8db5-6a642d74a708"


def test_worktree_slug_keeps_slash_free():
    assert worktree_slug("exp/batch-one") == "exp-batch-one"
    assert worktree_slug("") == "detached"


def test_project_name_is_shortest_unique_tail():
    assert project_name("vibrantlabs/eops/itsmbench-core", ["personal/ateam"]) == "itsmbench-core"
    assert project_name("vibrantlabs/core", ["personal/core", "vibrantlabs/core"]) == "vibrantlabs/core"


def test_make_handle():
    assert make_handle(SID, "vibrantlabs/eops/itsmbench-core", [], "exp/batch-one") == "itsmbench-core/exp-batch-one/19c3d0"


def test_matches_any_unique_suffix_or_prefix():
    handle = "itsmbench-core/exp-batch-one/19c3d0"
    for q in (SID, "19c3d0", "19c3", "exp-batch-one/19c3d0", handle, "itsmbench-core/exp-batch-one", "itsmbench-core"):
        assert matches(q, SID, handle), q
    for q in ("batch-one/19c3d0", "19c", "bench-core", "ffffff"):
        assert not matches(q, SID, handle), q
