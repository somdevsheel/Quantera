from fastapi import Request

from .lake import Lake


def get_lake(request: Request) -> Lake:
    return request.app.state.lake
