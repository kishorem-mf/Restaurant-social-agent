# IAM Module - Lambda Query Role and Policy

variable "query_environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

# Lambda execution role for query function
resource "aws_iam_role" "lambda_query" {
  name = "instagram-query-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = {
    Name        = "Instagram Query Lambda Role"
    Environment = var.query_environment
  }
}

# Lambda policy for S3 read/write and CloudWatch (DuckDB-only, no Glue)
resource "aws_iam_role_policy" "lambda_query_policy" {
  name = "instagram-query-lambda-policy"
  role = aws_iam_role.lambda_query.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3ReadAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          "arn:aws:s3:::instagram-analytics-lake",
          "arn:aws:s3:::instagram-analytics-lake/data/*"
        ]
      },
      {
        Sid    = "S3QueryLogsWriteAccess"
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = [
          "arn:aws:s3:::instagram-analytics-lake/data/query_logs/*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/instagram-query-data*"
      }
    ]
  })
}

# Outputs
output "query_lambda_role_arn" {
  description = "Query Lambda execution role ARN"
  value       = aws_iam_role.lambda_query.arn
}

output "query_lambda_role_name" {
  description = "Query Lambda execution role name"
  value       = aws_iam_role.lambda_query.name
}
