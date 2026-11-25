"""话单补正签发规则测试。

覆盖：原凭据原样复查、补正后当期用新金额、同版补正先签发者胜、
失败保留旧版、重试幂等不新增版本、历次版本可核对。
"""
import pytest

from app.errors import DomainIssue


def approved_run(client, profile="clean"):
    run = client.post("/api/runs", json={"profile": profile}).get_json()
    response = client.post(
        f"/api/runs/{run['run_id']}/review",
        json={"decision": "APPROVED", "comment": "首次签发", "expected_revision": 0})
    assert response.status_code == 200
    return run


def amend(client, run_id, request_key, base_version, label, amount, **extra):
    body = {"request_key": request_key, "base_version": base_version,
            "adjustments": [{"label": label, "amount_cents": amount}], **extra}
    return client.post(f"/api/runs/{run_id}/amendments", json=body)


def test_amendment_requires_approved_run(client):
    run = client.post("/api/runs", json={"profile": "clean"}).get_json()
    response = amend(client, run["run_id"], "fix-1", 0, run["rows"][0]["label"], 100)
    assert response.status_code == 409
    assert response.get_json()["code"] == "NOT_APPROVED"


def test_amendment_creates_new_version_and_updates_current_amounts(client):
    run = approved_run(client)
    label = run["rows"][0]["label"]
    original_total = run["ledger"]["amount_total"]

    response = amend(client, run["run_id"], "fix-1", 1, label, 999, reason="费率勘误")
    assert response.status_code == 201
    updated = response.get_json()
    assert updated["revision"] == 2
    assert updated["rows"][0]["amount_cents"] == 999
    # 当期对账立即使用新金额
    assert updated["ledger"]["amount_total"] != original_total
    assert int(updated["ledger"]["amount_total"]) == 999 + sum(
        row["amount_cents"] for row in updated["rows"][1:])


def test_historical_evidence_keeps_original_amounts(client):
    run = approved_run(client)
    rid = run["run_id"]
    label = run["rows"][0]["label"]
    old_amount = next(row["amount_cents"] for row in run["rows"] if row["label"] == label)
    old_total = run["ledger"]["amount_total"]

    amend(client, rid, "fix-1", 1, label, 999)

    v1 = client.get(f"/api/runs/{rid}/evidence?version=1").get_json()
    v2 = client.get(f"/api/runs/{rid}/evidence?version=2").get_json()
    # 原签发凭据原样可复查
    assert next(row["amount_cents"] for row in v1["rows"] if row["label"] == label) == old_amount
    assert v1["ledger"]["amount_total"] == old_total
    assert v1["version_block"]["kind"] == "ISSUE"
    assert v1["version_block"]["base_version"] is None
    # 新凭据承载补正金额
    assert next(row["amount_cents"] for row in v2["rows"] if row["label"] == label) == 999
    assert v2["version_block"]["kind"] == "AMENDMENT"
    assert v2["version_block"]["base_version"] == 1
    assert v1["artifact_sha256"] != v2["artifact_sha256"]


def test_current_evidence_tracks_latest_version(client):
    run = approved_run(client)
    rid = run["run_id"]
    label = run["rows"][0]["label"]
    amend(client, rid, "fix-1", 1, label, 999)

    current = client.get(f"/api/runs/{rid}/evidence").get_json()
    latest = client.get(f"/api/runs/{rid}/evidence?version=2").get_json()
    assert current["artifact_sha256"] == latest["artifact_sha256"]
    assert current["version_block"]["version"] == 2


def test_two_amendments_on_same_base_first_one_wins(client):
    run = approved_run(client)
    rid = run["run_id"]
    label = run["rows"][0]["label"]

    first = amend(client, rid, "fix-A", 1, label, 111)
    assert first.status_code == 201
    # 第二份仍基于旧版 v1：被拒绝，已生效的更正不被冲掉
    second = amend(client, rid, "fix-B", 1, label, 222)
    assert second.status_code == 409
    assert second.get_json()["code"] == "STALE_BASE"

    current = client.get(f"/api/runs/{rid}").get_json()
    assert current["revision"] == 2
    assert current["rows"][0]["amount_cents"] == 111
    versions = client.get(f"/api/runs/{rid}/versions").get_json()["versions"]
    assert [item["version"] for item in versions] == [1, 2]


def test_rejected_amendment_can_be_rebased_onto_current_version(client):
    run = approved_run(client)
    rid = run["run_id"]
    label = run["rows"][0]["label"]

    amend(client, rid, "fix-A", 1, label, 111)
    stale = amend(client, rid, "fix-B", 1, label, 222)
    assert stale.status_code == 409
    # 基于当前版 v2 重新复核后可以签发
    rebased = amend(client, rid, "fix-B", 2, label, 222)
    assert rebased.status_code == 201
    assert rebased.get_json()["revision"] == 3
    assert rebased.get_json()["rows"][0]["amount_cents"] == 222


def test_failed_amendment_keeps_old_version(client):
    run = approved_run(client)
    rid = run["run_id"]
    before = client.get(f"/api/runs/{rid}").get_json()

    bad_target = amend(client, rid, "fix-bad", 1, "NOT-EXIST", 1)
    assert bad_target.status_code == 422
    assert bad_target.get_json()["code"] == "ADJUSTMENT_TARGET_NOT_FOUND"

    after = client.get(f"/api/runs/{rid}").get_json()
    assert after["revision"] == before["revision"]
    assert after["ledger"]["amount_total"] == before["ledger"]["amount_total"]
    assert len(client.get(f"/api/runs/{rid}/versions").get_json()["versions"]) == 1


def test_retry_same_amendment_is_idempotent(client):
    run = approved_run(client)
    rid = run["run_id"]
    label = run["rows"][0]["label"]
    body = {"request_key": "fix-retry", "base_version": 1,
            "adjustments": [{"label": label, "amount_cents": 777}]}

    first = client.post(f"/api/runs/{rid}/amendments", json=body)
    assert first.status_code == 201
    # 同一份补正再次提交：返回历史结果，不新增版本
    second = client.post(f"/api/runs/{rid}/amendments", json=body)
    assert second.status_code == 200
    assert second.get_json()["revision"] == 2

    versions = client.get(f"/api/runs/{rid}/versions").get_json()["versions"]
    assert [item["version"] for item in versions] == [1, 2]
    assert [item for item in versions if item["kind"] == "AMENDMENT"][0]["request_key"] == "fix-retry"


def test_versions_list_records_full_chain(client):
    run = approved_run(client)
    rid = run["run_id"]
    label = run["rows"][0]["label"]
    amend(client, rid, "fix-1", 1, label, 111)
    amend(client, rid, "fix-2", 2, label, 222)

    data = client.get(f"/api/runs/{rid}/versions").get_json()
    assert data["current_version"] == 3
    chain = data["versions"]
    assert [item["version"] for item in chain] == [1, 2, 3]
    assert [item["kind"] for item in chain] == ["ISSUE", "AMENDMENT", "AMENDMENT"]
    assert chain[1]["base_version"] == 1
    assert chain[2]["base_version"] == 2
    assert all(len(item["digest"]) == 64 for item in chain)


def test_version_detail_returns_snapshot(client):
    run = approved_run(client)
    rid = run["run_id"]
    detail = client.get(f"/api/runs/{rid}/versions/1").get_json()
    assert detail["version"] == 1
    assert detail["kind"] == "ISSUE"

    assert client.get(f"/api/runs/{rid}/versions/99").status_code == 404


def test_unknown_run_amendment_returns_404(client):
    response = client.post("/api/runs/nope/amendments",
                           json={"request_key": "x", "base_version": 1,
                                 "adjustments": [{"label": "a", "amount_cents": 1}]})
    assert response.status_code == 404


@pytest.mark.parametrize("body", [
    {"request_key": "x", "base_version": 1, "adjustments": []},
    {"request_key": "x", "base_version": 1, "adjustments": "not-a-list"},
    {"request_key": "x", "base_version": 1,
     "adjustments": [{"label": "a", "amount_cents": -1}]},
    {"request_key": "x", "base_version": 1,
     "adjustments": [{"label": "", "amount_cents": 1}]},
    {"request_key": "x", "base_version": 1,
     "adjustments": [{"label": "a", "amount_cents": "1.5"}]},
    {"base_version": 1, "adjustments": [{"label": "a", "amount_cents": 1}]},
    {"request_key": "x", "adjustments": [{"label": "a", "amount_cents": 1}]},
])
def test_invalid_amendment_payloads_are_rejected(client, body):
    run = approved_run(client)
    response = client.post(f"/api/runs/{run['run_id']}/amendments", json=body)
    assert response.status_code == 400
    assert client.get(f"/api/runs/{run['run_id']}").get_json()["revision"] == 1


def test_store_enforces_first_writer_under_concurrent_base(client):
    """同一基准版本的两次补正即便绕过 HTTP 顺序，事务内也只放行一份。"""
    from app.review.service import apply_amendment

    run = approved_run(client)
    store = client.application.extensions["runs"]
    rid = run["run_id"]
    label = run["rows"][0]["label"]

    body1 = {"request_key": "c1", "base_version": 1,
             "adjustments": [{"label": label, "amount_cents": 111}]}
    body2 = {"request_key": "c2", "base_version": 1,
             "adjustments": [{"label": label, "amount_cents": 222}]}
    _, replayed1 = apply_amendment(store, rid, body1)
    assert replayed1 is False
    with pytest.raises(DomainIssue) as exc:
        apply_amendment(store, rid, body2)
    assert exc.value.code == "STALE_BASE"
    assert store.get(rid)["revision"] == 2
    assert len(store.list_versions(rid)) == 2
