from app.errors import DomainIssue
from app.evidence.manifest import stable_digest

OPTIONAL_FIELDS = ("rows", "amendment", "version_block")


def render(run):
    if run is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if run["status"] != "APPROVED":
        raise DomainIssue("NOT_APPROVED", 409)
    artifact = {
        "schema": "workbench-evidence/v1",
        "run_id": run["run_id"],
        "project": run["project"],
        "rule": run["rule"],
        "profile": run["profile"],
        "source": run["source"],
        "counts": run["counts"],
        "ledger": run["ledger"],
        "summary": run["summary"],
        "review": {"status": run["status"], "revision": run["revision"], "comment": run["comment"]},
    }
    for key in OPTIONAL_FIELDS:
        if key in run:
            artifact[key] = run[key]
    artifact["artifact_sha256"] = stable_digest(artifact)
    return artifact

def render_snapshot(snapshot):
    """从不可变版本快照渲染证据，保证历史凭据可原样复查。"""
    run = dict(snapshot)
    run.pop("result_rows", None)
    review = run["review"]
    run["status"] = review["status"]
    run["revision"] = review["revision"]
    run["comment"] = review["comment"]
    return render(run)
