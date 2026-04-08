terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda/handler.py"
  output_path = "${path.module}/lambda.zip"
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mysilo_lambda" {
  name               = "${var.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "mysilo_lambda_s3" {
  statement {
    sid    = "ReadWriteStateFile"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:HeadObject",
    ]
    resources = [
      "arn:aws:s3:::${var.bucket}/*",
    ]
  }

  statement {
    sid     = "ListBucket"
    effect  = "Allow"
    actions = ["s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.bucket}",
    ]
  }
}

resource "aws_iam_role_policy" "mysilo_lambda_s3" {
  name   = "${var.function_name}-s3-policy"
  role   = aws_iam_role.mysilo_lambda.id
  policy = data.aws_iam_policy_document.mysilo_lambda_s3.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.mysilo_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "mysilo" {
  function_name    = var.function_name
  role             = aws_iam_role.mysilo_lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 30

  # Concurrency of 1 serialises all writes to the remote state file,
  # preventing concurrent Lambdas from corrupting it.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      BUCKET           = var.bucket
      REMOTE_STATE_KEY = var.remote_state_key
    }
  }
}

resource "aws_lambda_permission" "s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mysilo.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::${var.bucket}"
}

resource "aws_s3_bucket_notification" "mysilo" {
  bucket = var.bucket

  lambda_function {
    lambda_function_arn = aws_lambda_function.mysilo.arn
    events = [
      "s3:ObjectCreated:*",
      "s3:ObjectRemoved:*",
    ]
  }

  depends_on = [aws_lambda_permission.s3_invoke]
}
