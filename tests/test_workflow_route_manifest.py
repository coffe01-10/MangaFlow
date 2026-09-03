"""Route manifest regression for the workflow route surface.

The pinned snapshot was captured from the monolithic
``app.api.routes.workflow`` module before it was split into a route package.
It fixes the exact registration order plus, per operation: method, path,
endpoint name, status code, response model, OpenAPI operation id and the
response metadata (status codes and schema names). Any reordering that could
introduce path shadowing, any dropped/duplicated route and any drift in HTTP
semantics therefore fails these tests.
"""

import re

from fastapi.routing import APIRoute

from app.api.routes.workflow import router as workflow_router
from app.config import get_settings
from app.main import app

PINNED_ROUTES = [
    {"method": "GET", "path": "/chapters/{chapter_id}/pages", "name": "list_pages", "status_code": None, "response_model": "list", "operationId": "list_pages_api_v1_chapters__chapter_id__pages_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "GET", "path": "/pages/{page_id}", "name": "get_page", "status_code": None, "response_model": "PageRead", "operationId": "get_page_api_v1_pages__page_id__get", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageRead"]},
    {"method": "GET", "path": "/pages/{page_id}/readiness", "name": "get_page_readiness", "status_code": None, "response_model": "PageReadinessRead", "operationId": "get_page_readiness_api_v1_pages__page_id__readiness_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageReadinessRead"]},
    {"method": "GET", "path": "/chapters/{chapter_id}/production-readiness", "name": "get_chapter_production_readiness", "status_code": None, "response_model": "ChapterProductionReadinessRead", "operationId": "get_chapter_production_readiness_api_v1_chapters__chapter_id__production_readiness_get", "responses": ["200", "422"], "schemas": ["ChapterProductionReadinessRead", "HTTPValidationError"]},
    {"method": "GET", "path": "/pages/{page_id}/production-readiness", "name": "get_page_production_readiness", "status_code": None, "response_model": "PageProductionReadinessRead", "operationId": "get_page_production_readiness_api_v1_pages__page_id__production_readiness_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageProductionReadinessRead"]},
    {"method": "GET", "path": "/pages/{page_id}/generation-workbench", "name": "get_generation_workbench", "status_code": None, "response_model": "GenerationWorkbenchRead", "operationId": "get_generation_workbench_api_v1_pages__page_id__generation_workbench_get", "responses": ["200", "422"], "schemas": ["GenerationWorkbenchRead", "HTTPValidationError"]},
    {"method": "GET", "path": "/pages/{page_id}/storyboard", "name": "get_storyboard", "status_code": None, "response_model": "StoryboardRead", "operationId": "get_storyboard_api_v1_pages__page_id__storyboard_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "StoryboardRead"]},
    {"method": "PATCH", "path": "/pages/{page_id}/layout", "name": "patch_page_layout", "status_code": None, "response_model": "StoryboardRead", "operationId": "patch_page_layout_api_v1_pages__page_id__layout_patch", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "StoryboardRead"]},
    {"method": "PATCH", "path": "/pages/{page_id}/reading-order", "name": "patch_page_reading_order", "status_code": None, "response_model": "StoryboardRead", "operationId": "patch_page_reading_order_api_v1_pages__page_id__reading_order_patch", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "StoryboardRead"]},
    {"method": "PUT", "path": "/pages/{page_id}/storyboard-geometry", "name": "put_page_storyboard_geometry", "status_code": None, "response_model": "StoryboardRead", "operationId": "put_page_storyboard_geometry_api_v1_pages__page_id__storyboard_geometry_put", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "StoryboardRead"]},
    {"method": "PATCH", "path": "/panels/{panel_id}", "name": "update_panel", "status_code": None, "response_model": "PanelRead", "operationId": "update_panel_api_v1_panels__panel_id__patch", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PanelRead"]},
    {"method": "POST", "path": "/panels/{panel_id}/dialogues", "name": "create_dialogue", "status_code": 201, "response_model": "DialogueRead", "operationId": "create_dialogue_api_v1_panels__panel_id__dialogues_post", "responses": ["201", "422"], "schemas": ["DialogueRead", "HTTPValidationError"]},
    {"method": "PATCH", "path": "/dialogues/{dialogue_id}", "name": "update_dialogue", "status_code": None, "response_model": "DialogueRead", "operationId": "update_dialogue_api_v1_dialogues__dialogue_id__patch", "responses": ["200", "422"], "schemas": ["DialogueRead", "HTTPValidationError"]},
    {"method": "DELETE", "path": "/dialogues/{dialogue_id}", "name": "delete_dialogue", "status_code": 204, "response_model": None, "operationId": "delete_dialogue_api_v1_dialogues__dialogue_id__delete", "responses": ["204", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "POST", "path": "/pages/{page_id}/batches", "name": "start_batch", "status_code": 201, "response_model": "GenerationBatchRead", "operationId": "start_batch_api_v1_pages__page_id__batches_post", "responses": ["201", "422"], "schemas": ["GenerationBatchRead", "HTTPValidationError"]},
    {"method": "GET", "path": "/pages/{page_id}/batches", "name": "list_batches", "status_code": None, "response_model": "list", "operationId": "list_batches_api_v1_pages__page_id__batches_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "POST", "path": "/batches/{batch_id}/candidates", "name": "create_candidate", "status_code": 202, "response_model": "CandidateQueuedRead", "operationId": "create_candidate_api_v1_batches__batch_id__candidates_post", "responses": ["202", "422"], "schemas": ["CandidateQueuedRead", "HTTPValidationError"]},
    {"method": "GET", "path": "/batches/{batch_id}/candidates", "name": "list_candidates", "status_code": None, "response_model": "list", "operationId": "list_candidates_api_v1_batches__batch_id__candidates_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "PATCH", "path": "/candidates/{candidate_id}/favorite", "name": "favorite_candidate", "status_code": None, "response_model": "PageCandidateRead", "operationId": "favorite_candidate_api_v1_candidates__candidate_id__favorite_patch", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageCandidateRead"]},
    {"method": "DELETE", "path": "/candidates/{candidate_id}", "name": "delete_candidate", "status_code": 204, "response_model": None, "operationId": "delete_candidate_api_v1_candidates__candidate_id__delete", "responses": ["204", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "POST", "path": "/pages/{page_id}/select-candidate", "name": "select_candidate", "status_code": None, "response_model": "PageRead", "operationId": "select_candidate_api_v1_pages__page_id__select_candidate_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageRead"]},
    {"method": "POST", "path": "/pages/{page_id}/selected-candidate/keep", "name": "keep_selected_candidate", "status_code": None, "response_model": "PageRead", "operationId": "keep_selected_candidate_api_v1_pages__page_id__selected_candidate_keep_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageRead"]},
    {"method": "DELETE", "path": "/pages/{page_id}/selected-candidate", "name": "retract_selected_candidate", "status_code": None, "response_model": "PageRead", "operationId": "retract_selected_candidate_api_v1_pages__page_id__selected_candidate_delete", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageRead"]},
    {"method": "POST", "path": "/pages/{page_id}/next", "name": "next_page", "status_code": None, "response_model": "PageRead", "operationId": "next_page_api_v1_pages__page_id__next_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "PageRead"]},
    {"method": "GET", "path": "/projects/{project_id}/library", "name": "library", "status_code": None, "response_model": "LibraryRead", "operationId": "library_api_v1_projects__project_id__library_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "LibraryRead"]},
    {"method": "GET", "path": "/projects/{project_id}/jobs", "name": "list_jobs", "status_code": None, "response_model": "list", "operationId": "list_jobs_api_v1_projects__project_id__jobs_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "GET", "path": "/jobs/{job_id}", "name": "get_job", "status_code": None, "response_model": "JobRead", "operationId": "get_job_api_v1_jobs__job_id__get", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobRead"]},
    {"method": "POST", "path": "/jobs/{job_id}/cancel", "name": "cancel", "status_code": None, "response_model": "JobRead", "operationId": "cancel_api_v1_jobs__job_id__cancel_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobRead"]},
    {"method": "POST", "path": "/jobs/{job_id}/retry", "name": "retry", "status_code": None, "response_model": "JobRead", "operationId": "retry_api_v1_jobs__job_id__retry_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobRead"]},
    {"method": "POST", "path": "/jobs/{job_id}/archive", "name": "archive_job", "status_code": None, "response_model": "JobRead", "operationId": "archive_job_api_v1_jobs__job_id__archive_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobRead"]},
    {"method": "POST", "path": "/jobs/{job_id}/restore", "name": "restore_job", "status_code": None, "response_model": "JobRead", "operationId": "restore_job_api_v1_jobs__job_id__restore_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobRead"]},
    {"method": "POST", "path": "/projects/{project_id}/jobs/archive-completed", "name": "archive_completed_jobs", "status_code": None, "response_model": "JobArchiveResult", "operationId": "archive_completed_jobs_api_v1_projects__project_id__jobs_archive_completed_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobArchiveResult"]},
    {"method": "POST", "path": "/projects/{project_id}/jobs/bulk-archive", "name": "bulk_archive_jobs", "status_code": None, "response_model": "JobArchiveResult", "operationId": "bulk_archive_jobs_api_v1_projects__project_id__jobs_bulk_archive_post", "responses": ["200", "422"], "schemas": ["HTTPValidationError", "JobArchiveResult"]},
    {"method": "DELETE", "path": "/jobs/{job_id}", "name": "delete_job", "status_code": 204, "response_model": None, "operationId": "delete_job_api_v1_jobs__job_id__delete", "responses": ["204", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "GET", "path": "/jobs/{job_id}/model-call-attempts", "name": "list_model_call_attempts", "status_code": None, "response_model": "list", "operationId": "list_model_call_attempts_api_v1_jobs__job_id__model_call_attempts_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "POST", "path": "/candidates/{candidate_id}/inspect", "name": "inspect_candidate", "status_code": 202, "response_model": "JobRead", "operationId": "inspect_candidate_api_v1_candidates__candidate_id__inspect_post", "responses": ["202", "422"], "schemas": ["HTTPValidationError", "JobRead"]},
    {"method": "GET", "path": "/candidates/{candidate_id}/inspections", "name": "list_inspections", "status_code": None, "response_model": "list", "operationId": "list_inspections_api_v1_candidates__candidate_id__inspections_get", "responses": ["200", "422"], "schemas": ["HTTPValidationError"]},
    {"method": "POST", "path": "/candidates/{candidate_id}/repairs", "name": "repair_candidate", "status_code": 202, "response_model": "CandidateQueuedRead", "operationId": "repair_candidate_api_v1_candidates__candidate_id__repairs_post", "responses": ["202", "422"], "schemas": ["CandidateQueuedRead", "HTTPValidationError"]},
    {"method": "POST", "path": "/candidates/{candidate_id}/upscale", "name": "upscale_candidate", "status_code": 202, "response_model": "CandidateQueuedRead", "operationId": "upscale_candidate_api_v1_candidates__candidate_id__upscale_post", "responses": ["202", "422"], "schemas": ["CandidateQueuedRead", "HTTPValidationError"]},
]


def _iter_workflow_api_routes(router):
    """Yield APIRoutes in registration order, descending into lazy includes."""

    for route in router.routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_workflow_api_routes(nested)
        else:
            yield route


def _workflow_route_entries():
    entries = []
    for route in _iter_workflow_api_routes(workflow_router):
        assert isinstance(route, APIRoute), f"unexpected route object: {route!r}"
        module = getattr(route.endpoint, "__module__", "")
        assert module == "app.api.routes.workflow" or module.startswith(
            "app.api.routes.workflow."
        ), f"route {route.name} registered outside the workflow package: {module}"
        for method in sorted(route.methods):
            entries.append(
                {
                    "method": method,
                    "path": route.path,
                    "name": route.name,
                    "status_code": route.status_code,
                    "response_model": (
                        route.response_model.__name__ if route.response_model else None
                    ),
                }
            )
    return entries


def _pinned_openapi_operations():
    return {
        (entry["path"], entry["method"]): entry
        for entry in PINNED_ROUTES
    }


def test_route_manifest_matches_pinned_baseline():
    current = _workflow_route_entries()
    pinned_core = [
        {
            key: entry[key]
            for key in ("method", "path", "name", "status_code", "response_model")
        }
        for entry in PINNED_ROUTES
    ]
    assert current == pinned_core


def test_openapi_operations_match_pinned_baseline():
    prefix = get_settings().api_prefix
    operations = {}
    for path, methods in app.openapi()["paths"].items():
        for method, operation in methods.items():
            if "workflow" not in operation.get("tags", []):
                continue
            assert path.startswith(prefix), f"unexpected prefix for {path}"
            schemas = []
            for response in operation["responses"].values():
                for content in response.get("content", {}).values():
                    stack = [content.get("schema", {})]
                    while stack:
                        node = stack.pop()
                        if not isinstance(node, dict):
                            continue
                        if "$ref" in node:
                            schemas.append(node["$ref"].rsplit("/", 1)[-1])
                        for key in ("items", "allOf", "anyOf", "oneOf"):
                            if key in node:
                                stack.extend(node[key])
                        for value in node.get("properties", {}).values():
                            stack.append(value)
            operations[(path[len(prefix) :], method.upper())] = {
                "operationId": operation["operationId"],
                "responses": sorted(operation["responses"].keys()),
                "schemas": sorted(dict.fromkeys(schemas)),
            }

    pinned = _pinned_openapi_operations()
    assert sorted(operations.keys()) == sorted(pinned.keys()), (
        "workflow operations in OpenAPI drifted from the pinned manifest"
    )
    for key, expected in pinned.items():
        assert operations[key] == {
            "operationId": expected["operationId"],
            "responses": expected["responses"],
            "schemas": expected["schemas"],
        }, f"OpenAPI metadata drifted for {key}"


def test_workflow_routes_have_unique_path_and_method():
    seen: dict[tuple[str, str], str] = {}
    for entry in _workflow_route_entries():
        key = (entry["path"], entry["method"])
        assert key not in seen, f"duplicate registration for {key}: {seen[key]}"
        seen[key] = entry["name"]


def test_workflow_routes_do_not_shadow_each_other():
    def as_pattern(path: str) -> re.Pattern[str]:
        parts = []
        for segment in path.split("/"):
            parts.append("[^/]+" if segment.startswith("{") else re.escape(segment))
        return re.compile("^/" + "/".join(parts) + "$")

    entries = _workflow_route_entries()
    for index, earlier in enumerate(entries):
        pattern = as_pattern(earlier["path"])
        for later in entries[index + 1 :]:
            if earlier["method"] != later["method"]:
                continue
            assert not pattern.match(later["path"]), (
                f"{earlier['method']} {earlier['path']} ({earlier['name']}) shadows "
                f"{later['method']} {later['path']} ({later['name']})"
            )
