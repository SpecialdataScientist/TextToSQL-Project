class AgentError(RuntimeError):
    """Only application-authored, credential-free messages cross the API boundary."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class ToolInputError(ValueError):
    """Recoverable errors returned to the LLM as tool results."""


def find_agent_error(error: BaseException) -> AgentError | None:
    # AnyIO task groups can wrap an exception during transport teardown.
    if isinstance(error, AgentError):
        return error
    for nested in getattr(error, "exceptions", ()):
        found = find_agent_error(nested)
        if found is not None:
            return found
    return None
