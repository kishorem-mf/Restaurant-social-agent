# Lambda Module - Ingest Data Function and S3 Triggers

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "lambda_role_arn" {
  description = "IAM role ARN for Lambda execution"
  type        = string
}

variable "duckdb_layer_arn" {
  description = "ARN of the DuckDB Lambda layer"
  type        = string
}

variable "analytics_bucket" {
  description = "Analytics lake bucket name"
  type        = string
  default     = "instagram-analytics-lake"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

# Archive the Lambda code
data "archive_file" "ingest_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/ingest_data"
  output_path = "${path.module}/../../../lambdas/ingest_data.zip"
}

# Lambda function
resource "aws_lambda_function" "ingest_data" {
  function_name = "instagram-ingest-data"
  role          = var.lambda_role_arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 300  # 5 minutes
  memory_size   = 1024 # 1 GB

  filename         = data.archive_file.ingest_lambda.output_path
  source_code_hash = data.archive_file.ingest_lambda.output_base64sha256

  layers = [var.duckdb_layer_arn]

  environment {
    variables = {
      ANALYTICS_BUCKET = var.analytics_bucket
      S3_REGION        = var.aws_region
    }
  }

  tags = {
    Name        = "Instagram Ingest Lambda"
    Environment = var.environment
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "ingest_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.ingest_data.function_name}"
  retention_in_days = 14

  tags = {
    Name        = "Instagram Ingest Lambda Logs"
    Environment = var.environment
  }
}

# Permission for restaurant backups bucket to invoke Lambda
resource "aws_lambda_permission" "allow_s3_restaurants" {
  statement_id  = "AllowS3InvokeRestaurants"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_data.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::instagram-scraper-backups-kishore"
}

# Permission for post backups bucket to invoke Lambda
resource "aws_lambda_permission" "allow_s3_posts" {
  statement_id  = "AllowS3InvokePosts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_data.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::instagram-post-scraper-backups-kishore-us"
}

# S3 event notification for restaurant backups bucket
resource "aws_s3_bucket_notification" "restaurant_trigger" {
  bucket = "instagram-scraper-backups-kishore"

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest_data.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.allow_s3_restaurants]
}

# S3 event notification for post backups bucket
resource "aws_s3_bucket_notification" "post_trigger" {
  bucket = "instagram-post-scraper-backups-kishore-us"

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest_data.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.allow_s3_posts]
}

# Outputs
output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.ingest_data.function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.ingest_data.arn
}

output "lambda_invoke_arn" {
  description = "Lambda invoke ARN"
  value       = aws_lambda_function.ingest_data.invoke_arn
}
