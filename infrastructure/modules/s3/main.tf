# S3 Module - Analytics Lake Bucket
# Source buckets (instagram-scraper-backups-kishore, instagram-post-scraper-backups-kishore-us) already exist

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

# Analytics lake bucket (Iceberg destination) - NEW
resource "aws_s3_bucket" "analytics_lake" {
  bucket = "instagram-analytics-lake"

  tags = {
    Name        = "Instagram Analytics Lake"
    Environment = var.environment
    Purpose     = "Iceberg tables storage"
  }
}

# Enable versioning for Iceberg time-travel
resource "aws_s3_bucket_versioning" "analytics_lake" {
  bucket = aws_s3_bucket.analytics_lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "analytics_lake" {
  bucket = aws_s3_bucket.analytics_lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Existing scraper buckets (in us-east-1, referenced by name only)
# - instagram-scraper-backups-kishore (restaurants)
# - instagram-post-scraper-backups-kishore-us (posts)

# Outputs
output "analytics_lake_bucket_id" {
  description = "Analytics lake bucket ID"
  value       = aws_s3_bucket.analytics_lake.id
}

output "analytics_lake_bucket_arn" {
  description = "Analytics lake bucket ARN"
  value       = aws_s3_bucket.analytics_lake.arn
}

output "restaurant_backups_bucket_name" {
  description = "Restaurant backups bucket name"
  value       = "instagram-scraper-backups-kishore"
}

output "post_backups_bucket_name" {
  description = "Post backups bucket name"
  value       = "instagram-post-scraper-backups-kishore-us"
}
