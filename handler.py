"""
Record Square deposits as sales receipts in QuickBooks Online
"""

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import LambdaFunctionUrlResolver, Response
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

tracer = Tracer()
logger = Logger()
app = LambdaFunctionUrlResolver()


@app.get("/ping")
@tracer.capture_method
def ping() -> Response:  # type: ignore
    """
    Return an arbitrary successful response, for health checks
    """
    return Response(status_code=200, content_type="text/plain", body="pong")


@app.get("/robots.txt")
@tracer.capture_method
def robotstxt() -> Response:  # type: ignore
    """
    Return a robots.txt file
    """
    return Response(
        status_code=200, content_type="text/plain", body="User-agent: *\nDisallow: /"
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.LAMBDA_FUNCTION_URL)
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext) -> dict:  # type: ignore
    """
    Main Lambda event handler
    """
    return app.resolve(event, context)
