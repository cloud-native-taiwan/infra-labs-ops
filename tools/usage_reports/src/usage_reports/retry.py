from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_random_exponential


# Errors that retrying cannot help with: bad request shape, missing auth,
# malformed external responses, programmer errors. Letting tenacity retry
# these wastes calls and can amplify a single misconfiguration into a
# rate-limit incident across CloudKitty / Keystone / Resend.
PERMANENT_ERRORS = (PermissionError, ValueError, KeyError, TypeError, AttributeError)


STANDARD_RETRY = retry(
    stop=stop_after_attempt(3),
    # Jittered exponential backoff avoids thundering-herd retries against
    # a throttled or recovering upstream service.
    wait=wait_random_exponential(min=1, max=10),
    retry=retry_if_not_exception_type(PERMANENT_ERRORS),
    reraise=True,
)
