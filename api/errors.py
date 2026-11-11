"""Error envelope: {"error": {"code", "message", "details"}} (docs/api_design.md §7)."""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def invalid(message, **details):
    return APIError(400, "INVALID_PARAMETER", message, details or None)


def no_data(message="no data in the requested range", **details):
    return APIError(404, "NO_DATA_IN_RANGE", message, details or None)


def unknown_instrument(**details):
    return APIError(404, "UNKNOWN_INSTRUMENT", "no instrument matches the identifier", details or None)


def install(app):
    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError):
        return JSONResponse(status_code=exc.status,
                            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(status_code=400,
                            content={"error": {"code": "INVALID_PARAMETER", "message": "invalid request parameters",
                                               "details": details}})
