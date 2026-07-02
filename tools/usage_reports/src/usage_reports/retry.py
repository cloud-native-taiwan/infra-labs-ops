from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_random_exponential


# Errors that retrying cannot help with: bad request shape, missing auth,
# malformed external responses, programmer errors. Letting tenacity retry
# these wastes calls and can amplify a single misconfiguration into a
# rate-limit incident across CloudKitty / Keystone / Resend.
#
# NOTE: This tool deliberately keeps an opt-out (denylist) retry policy rather
# than adopting infra_labs_common.retry's opt-in transient classifier. The
# call sites here retry any non-permanent error -- including generic errors
# that surface as RuntimeError (e.g. a Keystone 503 mapped to RuntimeError) --
# and the test-suite pins that behavior. Switching to the shared classifier
# would stop retrying those, so the two policies are intentionally NOT unified.
PERMANENT_ERRORS = (PermissionError, ValueError, KeyError, TypeError, AttributeError)


STANDARD_RETRY = retry(
    stop=stop_after_attempt(3),
    # Jittered exponential backoff avoids thundering-herd retries against
    # a throttled or recovering upstream service.
    wait=wait_random_exponential(min=1, max=10),
    retry=retry_if_not_exception_type(PERMANENT_ERRORS),
    reraise=True,
)
