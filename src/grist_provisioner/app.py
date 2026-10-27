"""The HTTP API PrivacyFence calls."""

import json
from collections.abc import Callable
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from grist_provisioner.service import Provisioner, ProvisionError
from grist_provisioner.state import StateStore


def create_app(provisioner: Provisioner, state: StateStore) -> Starlette:
    def authorized(request: Request) -> bool:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and state.key_valid(token.strip())

    def run(request: Request, call: Callable[[], dict[str, Any]], status: int = 200) -> Response:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            return JSONResponse(call(), status_code=status)
        except ProvisionError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status)

    async def self_info(request: Request) -> Response:
        return await run_in_threadpool(
            run, request, lambda: {"name": "grist-provisioner", "accounts": state.count_links()}
        )

    async def put_account(request: Request) -> Response:
        raw = await request.body()
        external_id = request.path_params["external_id"]

        def call() -> dict[str, Any]:
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            if not isinstance(body, dict):
                raise ProvisionError(400, "bad request")
            return provisioner.upsert(external_id, body.get("email"))

        return await run_in_threadpool(run, request, call)

    async def post_key(request: Request) -> Response:
        external_id = request.path_params["external_id"]
        return await run_in_threadpool(run, request, lambda: provisioner.issue_key(external_id), 201)

    async def delete_account(request: Request) -> Response:
        external_id = request.path_params["external_id"]
        return await run_in_threadpool(run, request, lambda: provisioner.release(external_id))

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    account = "/api/provisioning/accounts/{external_id}"
    return Starlette(
        routes=[
            Route("/api/provisioning/self", self_info, methods=["GET"]),
            Route(account, put_account, methods=["PUT"]),
            Route(account + "/key", post_key, methods=["POST"]),
            Route(account, delete_account, methods=["DELETE"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )
