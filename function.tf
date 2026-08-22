terraform {
  required_version = ">= 1.12.2"

  backend "s3" {
    bucket = "square-qbo-statefiles-771971951923-us-east-1-an"
    region = "us-east-1"
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.61.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

variable "environment_name" {
  type        = string
  description = "The name of the environment"
}

data "aws_iam_policy_document" "allow_lambda_to_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_function" {
  name = "square-qbo-${var.environment_name}"

  assume_role_policy = data.aws_iam_policy_document.allow_lambda_to_assume_role.json
}

data "aws_iam_policy_document" "app_permissions" {
  statement {
    actions = [
      "lambda:InvokeFunction",
      "lambda:UpdateFunctionConfiguration",
    ]

    resources = [aws_lambda_function.lambda_function.arn]
  }
}

resource "aws_iam_policy" "app_permissions" {
  name        = "square-qbo-${var.environment_name}"
  description = "Allow the Lambda function to invoke and update itself"

  policy = data.aws_iam_policy_document.app_permissions.json
}

data "aws_iam_policy" "cloudwatch" {
  arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy" "xray" {
  arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy_attachment" "cloudwatch" {
  role       = aws_iam_role.lambda_function.name
  policy_arn = data.aws_iam_policy.cloudwatch.arn
}

resource "aws_iam_role_policy_attachment" "xray" {
  role       = aws_iam_role.lambda_function.name
  policy_arn = data.aws_iam_policy.xray.arn
}

resource "aws_iam_role_policy_attachment" "app_permissions" {
  role       = aws_iam_role.lambda_function.name
  policy_arn = aws_iam_policy.app_permissions.arn
}

resource "aws_lambda_function" "lambda_function" {
  region = "us-east-1"

  function_name = "square-qbo-${var.environment_name}"
  description   = "Record Square deposits as sales receipts in QuickBooks Online"

  role = aws_iam_role.lambda_function.arn

  runtime       = "python3.15"
  architectures = ["arm64"]

  environment {
    variables = {
      SQUARE_TOKEN             = sensitive("")
      SQUARE_SIGNATURE_KEY     = sensitive("")
      QUICKBOOKS_ENVIRONMENT   = sensitive("")
      QUICKBOOKS_CLIENT_ID     = sensitive("")
      QUICKBOOKS_CLIENT_SECRET = sensitive("")
      QUICKBOOKS_COMPANY_ID    = sensitive("")
      QUICKBOOKS_ACCESS_TOKEN  = sensitive("")
      QUICKBOOKS_REFRESH_TOKEN = sensitive("")
    }
  }

  package_type     = "Zip"
  filename         = "./_bundle.zip"
  handler          = "handler.handler"
  source_code_hash = filebase64sha256("./_bundle.zip")

  memory_size = 512
  timeout     = 30

  tracing_config {
    mode = "Active"
  }

  lifecycle {
    ignore_changes = [
      environment
    ]
  }
}

resource "aws_lambda_function_event_invoke_config" "config" {
  function_name                = aws_lambda_function.lambda_function.arn
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_function_url" "function_url" {
  region = "us-east-1"

  function_name      = aws_lambda_function.lambda_function.arn
  authorization_type = "NONE"
}

# https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html#urls-auth-none
resource "aws_lambda_permission" "allow_unauthenticated_access_to_function_url" {
  region = "us-east-1"

  statement_id           = "AllowUnauthenticatedAccessToInvokeFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.lambda_function.function_name
  principal              = "*"
  function_url_auth_type = "NONE"

  lifecycle {
    replace_triggered_by = [
      aws_lambda_function.lambda_function
    ]
  }
}

resource "aws_sns_topic" "alarm_topic" {
  name = "square-qbo-${var.environment_name}"
}

resource "aws_cloudwatch_metric_alarm" "function_failures" {
  alarm_name          = "square-qbo-${var.environment_name}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  period              = 60
  statistic           = "Minimum"
  threshold           = 0
  alarm_actions       = [aws_sns_topic.alarm_topic.arn]
  datapoints_to_alarm = 1
  dimensions = {
    FunctionName = aws_lambda_function.lambda_function.function_name
  }
}

output "function_url" {
  value = aws_lambda_function_url.function_url.function_url
}
