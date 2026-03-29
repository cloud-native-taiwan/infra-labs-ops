from tenacity import retry, stop_after_attempt, wait_exponential


STANDARD_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    reraise=True,
)
