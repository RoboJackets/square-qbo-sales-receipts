"""
Record Square deposits as sales receipts in QuickBooks Online
"""

import os
import traceback
from json import dumps, loads

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import LambdaFunctionUrlResolver, Response
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

from boto3 import client

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes


from quickbooks import QuickBooks
from quickbooks.objects.account import Account
from quickbooks.objects.base import Ref
from quickbooks.objects.detailline import SalesItemLine, SalesItemLineDetail
from quickbooks.objects.item import Item
from quickbooks.objects.salesreceipt import SalesReceipt

from requests import get

from square import Square
from square.types.order_line_item import OrderLineItem
from square.types.payment import Payment
from square.utils.webhooks_helper import verify_signature

tracer = Tracer()
logger = Logger()
app = LambdaFunctionUrlResolver()


def get_auth_client() -> AuthClient:
    """
    Get the QuickBooks auth client
    """
    if hasattr(app, "current_event"):
        event = app.current_event
        protocol = event.headers.get("x-forwarded-proto", "https")
        host = event.headers.get("host") or event.request_context.domain_name
        redirect_uri = f"{protocol}://{host}/quickbooks/auth/callback"
    else:
        redirect_uri = None

    auth_client = AuthClient(
        client_id=os.environ["QUICKBOOKS_CLIENT_ID"],
        client_secret=os.environ["QUICKBOOKS_CLIENT_SECRET"],
        redirect_uri=redirect_uri,
        environment=os.environ["QUICKBOOKS_ENVIRONMENT"],
        access_token=(
            os.environ["QUICKBOOKS_ACCESS_TOKEN"]
            if os.environ["QUICKBOOKS_ACCESS_TOKEN"] != ""
            else None
        ),
        refresh_token=(
            os.environ["QUICKBOOKS_REFRESH_TOKEN"]
            if os.environ["QUICKBOOKS_REFRESH_TOKEN"] != ""
            else None
        ),
        realm_id=(
            os.environ["QUICKBOOKS_COMPANY_ID"]
            if os.environ["QUICKBOOKS_COMPANY_ID"] != ""
            else None
        ),
    )

    if auth_client.access_token is not None:
        response = get(
            url=f"https://quickbooks.api.intuit.com/v3/company/{auth_client.realm_id}/companyinfo/{auth_client.realm_id}",
            headers={
                "Authorization": f"Bearer {auth_client.access_token}",
                "Content-Type": "application/json",
            },
            timeout=(30, 30),
        )
        if response.status_code == 401:
            auth_client.refresh()

            new_environment_variables = {
                "SQUARE_TOKEN": os.environ["SQUARE_TOKEN"],
                "SQUARE_SIGNATURE_KEY": os.environ["SQUARE_SIGNATURE_KEY"],
                "QUICKBOOKS_ENVIRONMENT": os.environ["QUICKBOOKS_ENVIRONMENT"],
                "QUICKBOOKS_CLIENT_ID": os.environ["QUICKBOOKS_CLIENT_ID"],
                "QUICKBOOKS_CLIENT_SECRET": os.environ["QUICKBOOKS_CLIENT_SECRET"],
                "QUICKBOOKS_COMPANY_ID": auth_client.realm_id,
                "QUICKBOOKS_ACCESS_TOKEN": auth_client.access_token,
                "QUICKBOOKS_REFRESH_TOKEN": auth_client.refresh_token,
                "QUICKBOOKS_CUSTOMER_ID": os.environ["QUICKBOOKS_CUSTOMER_ID"],
                "QUICKBOOKS_CLASS_ID": os.environ["QUICKBOOKS_CLASS_ID"],
                "QUICKBOOKS_DEPOSIT_ACCOUNT_ID": os.environ[
                    "QUICKBOOKS_DEPOSIT_ACCOUNT_ID"
                ],
                "QUICKBOOKS_PAYMENT_METHOD_ID": os.environ[
                    "QUICKBOOKS_PAYMENT_METHOD_ID"
                ],
                "QUICKBOOKS_DUES_ITEM_ID": os.environ["QUICKBOOKS_DUES_ITEM_ID"],
                "QUICKBOOKS_PROCESSING_FEE_ITEM_ID": os.environ[
                    "QUICKBOOKS_PROCESSING_FEE_ITEM_ID"
                ],
            }

            try:
                lambda_client = client("lambda")
                lambda_client.update_function_configuration(
                    FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                    Environment={"Variables": new_environment_variables},
                )
            except Exception as e:
                logger.error(
                    f"Failed to update function configuration: {e}",
                )
                logger.error(f"Environment variables: {new_environment_variables}")
                raise e

    return auth_client


@app.get("/ping")
@tracer.capture_method
def ping() -> Response:  # type: ignore[type-arg]
    """
    Return an arbitrary successful response, for health checks
    """
    return Response(status_code=200, content_type="text/plain", body="pong")


@app.get("/robots.txt")
@tracer.capture_method
def robotstxt() -> Response:  # type: ignore[type-arg]
    """
    Return a robots.txt file
    """
    return Response(
        status_code=200, content_type="text/plain", body="User-agent: *\nDisallow: /"
    )


@app.get("/quickbooks/auth/start")
@tracer.capture_method
def quickbooks_auth_start() -> Response:  # type: ignore[type-arg]
    """
    Redirect to QuickBooks to authorize access to the company
    """
    auth_client = get_auth_client()

    return Response(
        status_code=302,
        content_type="text/plain",
        body="",
        headers={"Location": auth_client.get_authorization_url([Scopes.ACCOUNTING])},
    )


@app.get("/quickbooks/auth/callback")
@tracer.capture_method
def quickbooks_auth_callback() -> Response:  # type: ignore[type-arg]
    """
    Handle the callback from QuickBooks after authorization
    """
    code = app.current_event.query_string_parameters.get("code")
    realm_id = app.current_event.query_string_parameters.get("realmId")

    if not code or not realm_id:
        return Response(
            status_code=400, content_type="text/plain", body="Missing code or realmId"
        )

    if (
        os.environ["QUICKBOOKS_COMPANY_ID"] != ""
        and os.environ["QUICKBOOKS_COMPANY_ID"] != realm_id
    ):
        return Response(
            status_code=400, content_type="text/plain", body="Already authorized"
        )

    auth_client = get_auth_client()
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
        "QUICKBOOKS_CUSTOMER_ID": os.environ["QUICKBOOKS_CUSTOMER_ID"],
        "QUICKBOOKS_CLASS_ID": os.environ["QUICKBOOKS_CLASS_ID"],
        "QUICKBOOKS_DEPOSIT_ACCOUNT_ID": os.environ["QUICKBOOKS_DEPOSIT_ACCOUNT_ID"],
        "QUICKBOOKS_PAYMENT_METHOD_ID": os.environ["QUICKBOOKS_PAYMENT_METHOD_ID"],
        "QUICKBOOKS_DUES_ITEM_ID": os.environ["QUICKBOOKS_DUES_ITEM_ID"],
        "QUICKBOOKS_PROCESSING_FEE_ITEM_ID": os.environ[
            "QUICKBOOKS_PROCESSING_FEE_ITEM_ID"
        ],
    }

    try:
        lambda_client = client("lambda")
        lambda_client.update_function_configuration(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            Environment={"Variables": new_environment_variables},
        )
    except Exception as e:
        logger.error(f"Failed to update function configuration: {e}")
        logger.error(f"Environment variables: {new_environment_variables}")
        return Response(
            status_code=500,
            content_type="text/plain",
            body="Failed to store credentials",
        )

    return Response(
        status_code=200, content_type="text/plain", body="Authorization successful"
    )


@app.post("/square/webhook")
@tracer.capture_method
def square_webhook() -> Response:  # type: ignore[type-arg]
    """
    Handle Square webhook events
    """
    event = app.current_event
    host = event.headers.get("host") or event.request_context.domain_name
    url = f"https://{host}" + event.raw_path

    signature = event.headers.get("x-square-hmacsha256-signature")
    if not signature:
        return Response(
            status_code=401, content_type="text/plain", body="Missing signature"
        )

    if not verify_signature(
        request_body=event.body,  # type: ignore[arg-type]
        signature_header=signature,
        signature_key=os.environ["SQUARE_SIGNATURE_KEY"],
        notification_url=url,
    ):
        return Response(
            status_code=401, content_type="text/plain", body="Invalid signature"
        )

    logger.info("Webhook received", extra={"body": event.body})

    try:
        lambda_client = client("lambda")
        lambda_client.invoke(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=dumps({"payout_id": loads(event.body)["data"]["id"]}),  # type: ignore[arg-type]
        )
    except Exception:
        logger.error(traceback.format_exc())

    return Response(status_code=200, content_type="text/plain", body="Webhook received")


def validate_item(qb: QuickBooks, label: str, item_id: str) -> None:
    """
    Fetch a QuickBooks item and verify it has an income account configured
    """
    if not item_id:
        raise ValueError(f"{label} item ID is not configured in the environment")

    item = Item.get(item_id, qb=qb)

    def ref_dict(ref: Ref) -> dict[str, str] | None:
        if not ref:
            return None
        return {"name": ref.name, "type": ref.type, "value": ref.value}

    logger.info(
        "QuickBooks item retrieved",
        extra={
            "label": label,
            "item_id": item.Id,
            "item_name": item.Name,
            "item_type": item.Type,
            "active": item.Active,
            "income_account_ref": ref_dict(item.IncomeAccountRef),
            "expense_account_ref": ref_dict(item.ExpenseAccountRef),
            "asset_account_ref": ref_dict(item.AssetAccountRef),
        },
    )

    if not item.IncomeAccountRef:
        raise ValueError(
            f"Item {label} ({item_id}) does not have an Income account configured in QuickBooks"
        )

    income_account = Account.get(item.IncomeAccountRef.value, qb=qb)
    logger.info(
        "QuickBooks income account retrieved",
        extra={
            "label": label,
            "account_id": income_account.Id,
            "account_name": income_account.Name,
            "account_type": income_account.AccountType,
            "account_sub_type": income_account.AccountSubType,
        },
    )


def build_dues_line(order_line_item: OrderLineItem) -> SalesItemLine:
    """
    Build a sales item line for dues
    """
    dues = SalesItemLine()
    dues.SalesItemLineDetail = SalesItemLineDetail()
    dues.SalesItemLineDetail.Qty = None
    dues.SalesItemLineDetail.UnitPrice = None
    dues.SalesItemLineDetail.ItemRef = Ref()
    dues.SalesItemLineDetail.ItemRef.value = os.environ["QUICKBOOKS_DUES_ITEM_ID"]
    dues.SalesItemLineDetail.ClassRef = Ref()
    dues.SalesItemLineDetail.ClassRef.value = os.environ["QUICKBOOKS_CLASS_ID"]
    dues.Description = " - ".join(
        [order_line_item.name, order_line_item.variation_name]  # type: ignore[list-item]
    )
    dues.Amount = order_line_item.total_money.amount / 100  # type: ignore[union-attr,operator]

    return dues


def build_processing_fee_line(
    order_line_item: OrderLineItem, payment: Payment
) -> SalesItemLine:
    """
    Build a sales item line for processing fees
    """
    if len(payment.processing_fee) != 1:  # type: ignore[arg-type]
        raise ValueError("Payment should have exactly one processing fee")

    processing_fee = SalesItemLine()
    processing_fee.SalesItemLineDetail = SalesItemLineDetail()
    processing_fee.SalesItemLineDetail.Qty = None
    processing_fee.SalesItemLineDetail.UnitPrice = None
    processing_fee.SalesItemLineDetail.ItemRef = Ref()
    processing_fee.SalesItemLineDetail.ItemRef.value = os.environ[
        "QUICKBOOKS_PROCESSING_FEE_ITEM_ID"
    ]
    processing_fee.SalesItemLineDetail.ClassRef = Ref()
    processing_fee.SalesItemLineDetail.ClassRef.value = os.environ[
        "QUICKBOOKS_CLASS_ID"
    ]
    processing_fee.Description = " - ".join(
        ["Square Processing Fee", order_line_item.name, order_line_item.variation_name]  # type: ignore[list-item]
    )
    processing_fee.Amount = -payment.processing_fee[0].amount_money.amount / 100  # type: ignore[union-attr,operator,index]

    return processing_fee


def process_payout(payout_id: str) -> dict[str, str]:
    """
    Process a payout event
    """
    logger.info("Processing payout", extra={"payout_id": payout_id})

    square = Square()

    payout_response = square.payouts.get(payout_id=payout_id)

    logger.info("Payout retrieved", extra={"payout": payout_response})

    end_to_end_id = payout_response.payout.end_to_end_id

    qb = QuickBooks(
        auth_client=get_auth_client(), company_id=os.environ["QUICKBOOKS_COMPANY_ID"]
    )

    validate_item(qb, "Dues", os.environ["QUICKBOOKS_DUES_ITEM_ID"])
    validate_item(qb, "Processing Fee", os.environ["QUICKBOOKS_PROCESSING_FEE_ITEM_ID"])

    existing_sales_receipts = SalesReceipt.filter(qb=qb, PaymentRefNum=end_to_end_id)

    if len(existing_sales_receipts) > 0:
        existing_receipt = SalesReceipt.get(existing_sales_receipts[0].Id, qb=qb)
        logger.info(
            "Sales receipt already exists",
            extra={
                "end_to_end_id": end_to_end_id,
                "receipt_json": loads(existing_receipt.to_json()),
            },
        )
        return {"status": "ok"}

    receipt_lines = []

    payout_entries = square.payouts.list_entries(payout_id=payout_id)

    for entry in payout_entries:
        if entry.type == "CHARGE":
            payment_response = square.payments.get(
                payment_id=entry.type_charge_details.payment_id
            )

            if payment_response.payment.order_id is None:
                raise ValueError(
                    f"Unable to process payment not associated with an order: {payment_response.payment.id}"
                )

            order_response = square.orders.get(
                order_id=payment_response.payment.order_id
            )

            if len(order_response.order.line_items) != 1:
                raise ValueError(
                    f"Order has more than one line item: {payment_response.payment.order_id}"
                )

            order_line_item = order_response.order.line_items[0]

            if order_line_item.name == "Dues":
                receipt_lines.append(build_dues_line(order_line_item))

            else:
                raise ValueError(
                    f"Unknown order line item name: {order_line_item.name}"
                )

            receipt_lines.append(
                build_processing_fee_line(order_line_item, payment_response.payment)
            )

        else:
            raise ValueError(f"Unknown payout entry type: {entry.type}")

    receipt = SalesReceipt()
    receipt.TxnDate = payout_response.payout.arrival_date
    receipt.DocNumber = end_to_end_id
    receipt.PaymentRefNum = end_to_end_id
    receipt.CustomerRef = Ref()
    receipt.CustomerRef.value = os.environ["QUICKBOOKS_CUSTOMER_ID"]
    receipt.CurrencyRef = Ref()
    receipt.CurrencyRef.value = "USD"
    receipt.ClassRef = Ref()
    receipt.ClassRef.value = os.environ["QUICKBOOKS_CLASS_ID"]
    receipt.DepositToAccountRef = Ref()
    receipt.DepositToAccountRef.value = os.environ["QUICKBOOKS_DEPOSIT_ACCOUNT_ID"]
    receipt.PaymentMethodRef = Ref()
    receipt.PaymentMethodRef.value = os.environ["QUICKBOOKS_PAYMENT_METHOD_ID"]
    receipt.Line = receipt_lines

    print(receipt.to_json())

    saved_receipt = receipt.save(qb=qb)
    logger.info("Saved receipt", extra={"receipt_json": loads(saved_receipt.to_json())})

    return {"status": "ok"}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.LAMBDA_FUNCTION_URL)
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext) -> dict:  # type: ignore[type-arg]
    """
    Main Lambda event handler
    """
    print(event)
    payout_id = event.get("payout_id")
    if payout_id:
        return process_payout(payout_id)

    return app.resolve(event, context)
