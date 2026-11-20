"""Negative browser flows (UI half; the API half is test_negative_api.py): a reload during a job
keeps the job, two tabs stay consistent with the API, and a draft changed behind a tab's back is
reported stale instead of being finalized. One beam job in this module."""
from __future__ import annotations

import copy

from playwright.sync_api import expect

from harness.ui import CheckedBeam, StaleBanner, sign_in


def _open_beam(page, stack, token) -> CheckedBeam:
    sign_in(page, stack.config.ui_url, token)
    beam = CheckedBeam(page)
    beam.open_form()
    return beam


def test_reload_during_a_job_keeps_the_job(stack, token, api, page, shot, external_requests):
    beam = _open_beam(page, stack, token)
    beam.save_draft()
    beam.acknowledge()
    beam.finalize()
    job_id = beam.run_checks()
    shot(page, "reload_before")
    page.reload(wait_until="domcontentloaded")
    assert job_id not in page.url
    sign_in(page, stack.config.ui_url, token)  # a new session: the page forgets the id, not the job
    shot(page, "reload_after")
    job = api.wait_job(job_id, timeout_s=600)
    assert job["lifecycle"] == "completed" and job["outcome"] == "accepted", job
    assert api.get_job(job_id).json()["job_id"] == job_id
    assert external_requests == []


def test_two_tabs_stay_consistent_with_the_api(stack, token, api, page, second_tab, shot, external_requests):
    beam_a = _open_beam(page, stack, token)
    beam_b = _open_beam(second_tab, stack, token)
    id_a, rev_a, sha_a = beam_a.save_draft()
    id_b, rev_b, sha_b = beam_b.save_draft()
    assert id_a != id_b
    for draft_id, revision, sha in ((id_a, rev_a, sha_a), (id_b, rev_b, sha_b)):
        record = api.get_draft(draft_id).json()
        assert (record["draft"]["revision"], record["brief_sha256"]) == (revision, sha)
    # Editing in tab A moves only A's draft.
    beam_a.set_field("Length (mm)", "201")
    _, rev_a2, sha_a2 = beam_a.save_draft()
    assert rev_a2 == rev_a + 1 and sha_a2 != sha_a
    assert api.get_draft(id_a).json()["draft"]["revision"] == rev_a2
    assert api.get_draft(id_b).json()["draft"]["revision"] == rev_b
    assert beam_b.draft_caption() == (id_b, rev_b, sha_b)
    shot(page, "two_tabs_a")
    shot(second_tab, "two_tabs_b")
    assert external_requests == []


def test_draft_changed_in_another_tab_is_stale_and_not_finalized(stack, token, api, page, shot, external_requests):
    beam = _open_beam(page, stack, token)
    draft_id, revision, sha = beam.save_draft()
    beam.acknowledge()
    expect(beam.button("Finalize reviewed brief")).to_be_enabled(timeout=30_000)  # the ack rerun has settled
    prepared = api.get_draft(draft_id).json()
    edited = copy.deepcopy(prepared["input"])
    edited["requirements"]["length_mm"] = 201.0
    patched = api.patch_draft(draft_id, {"input": edited}, revision=revision)
    assert patched.status_code == 200, patched.text
    new_revision = patched.json()["draft"]["revision"]
    assert new_revision == revision + 1
    # Finalizing the old revision in this tab: the UI detects the change first and refuses.
    beam.button("Finalize reviewed brief").click()
    banner = StaleBanner(page)
    expect(banner.warning).to_be_visible(timeout=30_000)
    expect(beam.button("Finalize reviewed brief")).to_be_disabled()
    expect(page.get_by_text("Finalized brief")).to_have_count(0)
    shot(page, "stale_warning")
    banner.load_latest.click()
    expect(banner.warning).to_have_count(0, timeout=30_000)
    assert beam.draft_caption()[1] == new_revision
    shot(page, "stale_reloaded")
    assert external_requests == []
