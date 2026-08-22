"""
Record Square deposits as sales receipts in QuickBooks Online
"""

import os
import traceback
from json import loads, dumps

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import LambdaFunctionUrlResolver, Response
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes

from boto3 import client

from square.utils.webhooks_helper import verify_signature

tracer = Tracer()
logger = Logger()
app = LambdaFunctionUrlResolver()


def get_auth_client(app: LambdaFunctionUrlResolver) -> AuthClient:
    """
    Get the QuickBooks auth client
    """
    event = app.current_event
    protocol = event.headers.get("x-forwarded-proto", "https")
    host = event.headers.get("host") or event.request_context.domain_name
    redirect_uri = f"{protocol}://{host}/quickbooks/auth/callback"

    return AuthClient(
        client_id=os.environ["QUICKBOOKS_CLIENT_ID"],
        client_secret=os.environ["QUICKBOOKS_CLIENT_SECRET"],
        redirect_uri=redirect_uri,
        environment=os.environ["QUICKBOOKS_ENVIRONMENT"],
    )


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


@app.get("/quickbooks/auth/start")
@tracer.capture_method
def quickbooks_auth_start() -> Response:  # type: ignore
    """
    Redirect to QuickBooks to authorize access to the company
    """
    auth_client = get_auth_client(app)

    return Response(
        status_code=302,
        content_type="text/plain",
        body="",
        headers={"Location": auth_client.get_authorization_url([Scopes.ACCOUNTING])},
    )


@app.get("/quickbooks/auth/callback")
@tracer.capture_method
def quickbooks_auth_callback() -> Response:  # type: ignore
    """
    Handle the callback from QuickBooks after authorization
    """
    code = app.current_event.query_string_parameters.get("code")
    realm_id = app.current_event.query_string_parameters.get("realmId")
    
    if not code or not realm_id:
        return Response(status_code=400, content_type="text/plain", body="Missing code or realmId")

    auth_client = get_auth_client(app)
    auth_client.get_bearer_token(code, realm_id=realm_id)

    new_environment_variables = {
        "SQUARE_TOKEN": os.environ["SQUARE_TOKEN"],
        "SQUARE_SIGNATURE_KEY": os.environ["SQUARE_SIGNATURE_KEY"],
        "QUICKBOOKS_ENVIRONMENT": os.environ["QUICKBOOKS_ENVIRONMENT"],
        "QUICKBOOKS_CLIENT_ID": os.environ["QUICKBOOKS_CLIENT_ID"],
        "QUICKBOOKS_CLIENT_SECRET": os.environ["QUICKBOOKS_CLIENT_SECRET"],
        "QUICKBOOKS_COMPANY_ID": realm_id,
        "QUICKBOOKS_ACCESS_TOKEN": auth_client.access_token,
        "QUICKBOOKS_REFRESH_TOKEN": auth_client.refresh_token,
    }

    try:
        lambda_client = client("lambda")
        lambda_client.update_function_configuration(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            Environment={
                "Variables": new_environment_variables
            }
        )
    except Exception as e:
        logger.error(f"Failed to update function configuration: {e}")
        logger.error(f"Environment variables: {new_environment_variables}")
        return Response(status_code=500, content_type="text/plain", body="Failed to store credentials")

    return Response(status_code=200, content_type="text/plain", body="Authorization successful")



@app.post("/square/webhook")
@tracer.capture_method
def square_webhook() -> Response:  # type: ignore
    """
    Handle Square webhook events
    """
    event = app.current_event
    host = event.headers.get("host") or event.request_context.domain_name
    url = f"https://{host}" + event.raw_path

    signature = event.headers.get("x-square-hmacsha256-signature")
    if not signature:
        return Response(status_code=401, content_type="text/plain", body="Missing signature")

    if not verify_signature(request_body=event.body, signature_header=signature, signature_key=os.environ["SQUARE_SIGNATURE_KEY"], notification_url=url):
        return Response(status_code=401, content_type="text/plain", body="Invalid signature")

    logger.info("Webhook received", extra={"body": event.body})

    try:
        lambda_client = client("lambda")
        lambda_client.invoke(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=dumps({"payout_id": loads(event.body)["data"]["id"]})
        )
    except Exception as e:
        logger.error(traceback.format_exc())

    return Response(status_code=200, content_type="text/plain", body="Webhook received")


def process_payout(payout_id: str) -> dict[str,str]:
    """
    Process a payout event
    """
    logger.info("Processing payout", extra={"payout_id": payout_id})
    return {"status": "ok"}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.LAMBDA_FUNCTION_URL)
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext) -> dict:  # type: ignore
    """
    Main Lambda event handler
    """
    print(event)
    payout_id = event.get("payout_id")
    if payout_id:
        return process_payout(payout_id)

    return app.resolve(event, context)
