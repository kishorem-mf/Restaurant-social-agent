# IAM Module - Lambda Ingest Role and Policy

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

# Lambda execution role
resource "aws_iam_role" "lambda_ingest" {
  name = "instagram-ingest-lambda-role"

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
    Name        = "Instagram Ingest Lambda Role"
    Environment = var.environment
  }
}

# Lambda policy for S3, Glue, and CloudWatch
resource "aws_iam_role_policy" "lambda_ingest_policy" {
  name = "instagram-ingest-lambda-policy"
  role = aws_iam_role.lambda_ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          # Source buckets (existing scraper backups)
          "arn:aws:s3:::instagram-scraper-backups-kishore",
          "arn:aws:s3:::instagram-scraper-backups-kishore/*",
          "arn:aws:s3:::instagram-post-scraper-backups-kishore-us",
          "arn:aws:s3:::instagram-post-scraper-backups-kishore-us/*",
          # Destination bucket (analytics lake)
          "arn:aws:s3:::instagram-analytics-lake",
          "arn:aws:s3:::instagram-analytics-lake/*"
        ]
      },
      {
        Sid    = "GlueAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
          "glue:GetPartitions",
          "glue:BatchCreatePartition",
          "glue:BatchDeletePartition"
        ]
        Resource = [
          "arn:aws:glue:*:*:catalog",
          "arn:aws:glue:*:*:database/instagram_db",
          "arn:aws:glue:*:*:table/instagram_db/*"
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
        Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/instagram-ingest-data*"
      }
    ]
  })
}

# Outputs
output "lambda_role_arn" {
  description = "Lambda execution role ARN"
  value       = aws_iam_role.lambda_ingest.arn
}

output "lambda_role_name" {
  description = "Lambda execution role name"
  value       = aws_iam_role.lambda_ingest.name
}
