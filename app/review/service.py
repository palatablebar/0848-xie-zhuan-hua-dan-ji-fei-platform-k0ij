import json
from copy import deepcopy

from app.errors import DomainIssue
from app.review.policy import approval_allowed


def create_run(store, payload):
    row = store.create(payload["run_key"], payload["profile"], payload)
    return serialize(row)


def approve(store, run_id, decision, comment, expected_revision):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    payload = json.loads(row["payload"])
    if decision == "APPROVED" and not approval_allowed(payload):
        raise DomainIssue("GATE_BLOCKED", 409)
    if decision not in {"APPROVED", "HELD"}:
        raise DomainIssue("BAD_REQUEST")
    row, _snapshot, problem = store.review(run_id, decision, comment.strip(), expected_revision)
    if problem == "missing":
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if problem == "stale":
        raise DomainIssue("STALE_REVIEW", 409)
    return serialize(row)


def apply_amendment(store, run_id, body):
    """签发话单补正。

    规则：仅已签发批次可补正；补正必须基于当前生效版本（``base_version``），
    落后的补正被拒绝且不改动任何数据；同一 ``request_key`` 重试直接返回已签发版本。
    """
    request_key = _require_text(body, "request_key")
    base_version = _require_int(body, "base_version")
    comment = str(body.get("comment", "")).strip()
    reason = str(body.get("reason", "")).strip()
    adjustments = body.get("adjustments")
    if not isinstance(adjustments, list) or not adjustments:
        raise DomainIssue("ADJUSTMENT_INVALID", 400)

    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if row["status"] != "APPROVED":
        raise DomainIssue("NOT_APPROVED", 409)

    normalized = []
    for item in adjustments:
        if not isinstance(item, dict):
            raise DomainIssue("ADJUSTMENT_INVALID", 400)
        label = item.get("label")
        amount = item.get("amount_cents")
        if not isinstance(label, str) or not label.strip() or isinstance(amount, bool):
            raise DomainIssue("ADJUSTMENT_INVALID", 400)
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise DomainIssue("ADJUSTMENT_INVALID", 400)
        if amount < 0:
            raise DomainIssue("ADJUSTMENT_INVALID", 400)
        normalized.append({"label": label.strip(), "amount_cents": amount})

    payload = deepcopy(json.loads(row["payload"]))
    full_rows = payload.get("result_rows") or payload.get("rows", [])
    if not isinstance(full_rows, list) or not full_rows:
        raise DomainIssue("ADJUSTMENT_TARGET_NOT_FOUND", 422)
    by_label = {item.get("label"): item for item in full_rows if isinstance(item, dict)}
    preview_labels = {item.get("label") for item in payload.get("rows", []) if isinstance(item, dict)}
    applied = []
    for change in normalized:
        target = by_label.get(change["label"])
        if target is None or "amount_cents" not in target:
            raise DomainIssue("ADJUSTMENT_TARGET_NOT_FOUND", 422, change["label"])
        target["amount_cents"] = change["amount_cents"]
        if change["label"] in preview_labels:
            next(item for item in payload["rows"] if item.get("label") == change["label"])["amount_cents"] = change["amount_cents"]
        applied.append(change)

    ledger = payload.get("ledger")
    if ledger is not None:
        ledger["amount_total"] = str(sum(int(item.get("amount_cents") or 0) for item in full_rows))

    new_version = row["revision"] + 1
    payload["amendment"] = {"request_key": request_key, "base_version": base_version,
                            "version": new_version, "reason": reason, "adjustments": applied}
    row, snapshot, problem = store.amend(
        run_id, payload, new_version, "AMENDMENT", base_version, request_key, comment, reason)
    if problem == "missing":
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if problem == "stale":
        raise DomainIssue("STALE_BASE", 409)
    if problem == "duplicate":
        # 同一补正重试：返回历史已签发版本，不新增版本
        return serialize(row), True
    return serialize(row), False


def list_versions(store, run_id):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return [serialize_version(item) for item in store.list_versions(run_id)]


def get_version(store, run_id, version):
    item = store.get_version(run_id, version)
    if item is None:
        raise DomainIssue("VERSION_NOT_FOUND", 404)
    return item


def serialize_version(item):
    block = dict(item["payload"].get("version_block", {}))
    block.update(version_id=item["id"], digest=item["digest"])
    return block


def serialize(row):
    if row is None:
        return None
    payload = json.loads(row["payload"])
    payload.update(run_id=row["id"], status=row["status"], revision=row["revision"],
                   comment=row["comment"], created_at=row["created_at"])
    return payload


def _require_text(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DomainIssue("BAD_REQUEST")
    return value.strip()


def _require_int(body, key):
    value = body.get(key)
    if isinstance(value, bool):
        raise DomainIssue("BAD_REQUEST")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DomainIssue("BAD_REQUEST")
