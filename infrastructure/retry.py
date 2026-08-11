import time
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type

from infrastructure.logging import logger


class RetryPolicy:
    """Configurable retry policy with exponential backoff."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
    ):
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    def calculate_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay for the given attempt
        number (0-indexed)."""
        delay = self.base_delay * (self.exponential_base**attempt)
        return min(delay, self.max_delay)


def retry_on_exception(
    policy: RetryPolicy,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that retries a function according to the given
    RetryPolicy whenever it raises one of the specified exception
    types. Re-raises the last exception if every attempt fails.

    Usage:
        policy = RetryPolicy(max_retries=3, base_delay=0.5)

        @retry_on_exception(policy, exceptions=(ConnectionError,))
        def flaky_call():
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Optional[BaseException], not BaseException, since it
            # genuinely starts unset. RetryPolicy.__init__ guarantees
            # max_retries >= 1, so the loop below always runs at least
            # once and last_exception is always set by the time we'd
            # raise it -- the assert makes that guarantee explicit and
            # checkable instead of implicit, and turns a would-be
            # confusing "raise None -> TypeError" into a clear
            # AssertionError if that guarantee is ever violated.
            last_exception: Optional[BaseException] = None

            for attempt in range(policy.max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    is_last_attempt = attempt == policy.max_retries - 1

                    if is_last_attempt:
                        logger.error(
                            f"{func.__name__} failed after " f"{policy.max_retries} attempts: {e}"
                        )
                        break

                    delay = policy.calculate_delay(attempt)
                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/"
                        f"{policy.max_retries}): {e}. Retrying in "
                        f"{delay:.2f}s..."
                    )
                    time.sleep(delay)

            assert last_exception is not None, (
                "unreachable: RetryPolicy guarantees max_retries >= 1, so the "
                "loop above always runs and sets last_exception before this point"
            )
            raise last_exception

        return wrapper

    return decorator