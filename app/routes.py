from flask import Blueprint, abort, jsonify, request, send_file

from app.catalog import SPEC
from app.config import SAMPLES
from app.errors import DomainIssue
from app.evidence.render import render, render_snapshot
from app.review.service import (apply_amendment, approve, create_run, get_version,
                                list_versions, serialize, serialize_version)
from app.service import pipeline


api = Blueprint("api", __name__)
STAGE_INDEX = {"intake": 1, "prepare": 2, "calculate": 3, "decision": 4, "reconcile": 5}


def store():
    from flask import current_app
    return current_app.extensions["runs"]


@api.get("/api/catalog")
def catalog():
    return jsonify(code=SPEC["code"], name=SPEC["name"], actor=SPEC["actor"], rule=SPEC["rule"],
                   rule_short=SPEC["rule_short"],
                   stages=[{"id": item, "name": name} for item, name in zip(
                       ["intake", "prepare", "calculate", "decision", "reconcile", "review", "evidence"],
                       SPEC["stages"])],
                   chart=SPEC["chart"], result_columns=SPEC["result_columns"],
                   columns=SPEC["columns"], samples=["dirty", "clean"])


@api.post("/api/stages/<stage_id>")
def stage(stage_id):
    if stage_id not in STAGE_INDEX:
        raise DomainIssue("STAGE_UNKNOWN", 404)
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    upto = SPEC["stages"][STAGE_INDEX[stage_id] - 1]
    return jsonify(pipeline(profile, upto=upto))


@api.post("/api/runs")
def create():
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    payload = pipeline(profile)
    return jsonify(create_run(store(), payload))


@api.get("/api/runs/<run_id>")
def get_run(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify(run)


@api.post("/api/runs/<run_id>/review")
def review(run_id):
    data = request.get_json(silent=True) or {}
    if "decision" not in data or "expected_revision" not in data:
        raise DomainIssue("BAD_REQUEST")
    return jsonify(approve(store(), run_id, data["decision"], data.get("comment", ""), int(data["expected_revision"])))


@api.post("/api/runs/<run_id>/amendments")
def amend(run_id):
    data = request.get_json(silent=True) or {}
    run, replayed = apply_amendment(store(), run_id, data)
    response = jsonify(run)
    response.status_code = 200 if replayed else 201
    return response


@api.get("/api/runs/<run_id>/versions")
def versions(run_id):
    run = serialize(store().get(run_id))
    if run is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify({"run_id": run_id, "current_version": run["revision"],
                    "versions": list_versions(store(), run_id)})


@api.get("/api/runs/<run_id>/versions/<int:version>")
def version_detail(run_id, version):
    return jsonify(serialize_version(get_version(store(), run_id, version)))


@api.get("/api/runs/<run_id>/evidence")
def evidence(run_id):
    version = request.args.get("version", type=int)
    if version is None:
        run = serialize(store().get(run_id))
        if run is None:
            raise DomainIssue("RUN_NOT_FOUND", 404)
        if run["status"] != "APPROVED":
            raise DomainIssue("NOT_APPROVED", 409)
        item = get_version(store(), run_id, run["revision"])
        return jsonify(render_snapshot(item["payload"]))
    item = get_version(store(), run_id, version)
    return jsonify(render_snapshot(item["payload"]))


@api.get("/api/examples/<profile>")
def example(profile):
    if profile not in {"clean", "dirty"}:
        abort(404)
    return send_file(SAMPLES / f"{profile}.csv", as_attachment=True)
