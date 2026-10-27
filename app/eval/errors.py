"""The one exception whose MESSAGE the runner prints (review round 3).

runner.main prints every other exception as its type name only: a library's
text can carry client data (PyMuPDF's "Failed to open file '<path>'",
excel_gold quoting a sheet title, pydantic quoting an input's content). An
intentional refusal whose wording the operator needs, and which by
construction holds no client value, is raised as RunnerError instead."""


class RunnerError(ValueError):
    """A deliberate, already-safe message: printed as `ERROR: <message>`.
    A ValueError subclass so code that caught ValueError keeps working."""
