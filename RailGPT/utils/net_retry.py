import time
import random
import inspect
import requests
from typing import Callable, Iterable, Type, Any, Optional

from agent.psw import AgentState


class RetryExhausted(Exception):
    pass


def _accepts_attempt_keyword(func: Callable[..., Any]) -> bool:
    """Inspect once so an internal TypeError never reruns the callable."""

    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False

    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or (
            parameter.name == "attempt"
            and parameter.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        )
        for parameter in parameters
    )


def truncated_binary_backoff(
    func: Callable[..., Any],
    *,
    max_attempts: int = 5,
    slot_time: float = 1.0,
    max_backoff_exp: int = 4,
    retry_on_status: Iterable[int] = (429, 500, 502, 503, 504),
    retry_on_exceptions: Iterable[Type[Exception]] = (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.ChunkedEncodingError,
    ),
    psw: Optional[Any] = None,   # 👈 新增：可选 PSW
    context: Optional[str] = None,  # 👈 给状态机用的人类可读上下文
):
    """
    CSMA/CD-style truncated binary exponential backoff
    - 完全兼容旧调用
    - PSW 是可选的
    """

    last_exc: Optional[Exception] = None
    accepts_attempt = _accepts_attempt_keyword(func)

    for attempt in range(1, max_attempts + 1):
        try:
            if psw:
                psw.set_state(
                    AgentState.QUERYING,
                    f"network attempt {attempt}"
                )

            # 兼容 func() / func(attempt=)
            if accepts_attempt:
                return func(attempt=attempt)
            return func()

        except requests.HTTPError as e:
            if e.response is None or e.response.status_code in retry_on_status:
                last_exc = e
            else:
                raise

        except retry_on_exceptions as e:
            last_exc = e

        except Exception:
            raise

        if attempt >= max_attempts:
            break

        exp = min(attempt, max_backoff_exp)
        wait = random.uniform(0, 2 ** exp) * slot_time

        if psw:
            psw.set_state(
                AgentState.RETRYING,
                f"{context or 'request'} retry {attempt}, wait {wait:.2f}s"
            )

        time.sleep(wait)

    if psw:
        psw.set_state(
            AgentState.ERROR,
            f"{context or 'request'} exhausted retries"
        )

    raise RetryExhausted(str(last_exc))
