# Main Terraform Configuration - Instagram Analytics Infrastructure
# Step 1: Data Ingestion Layer

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Optional: Configure backend for state storage
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "instagram-analytics/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

# Provider configuration
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "Instagram Analytics"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# Variables
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "duckdb_layer_arn" {
  description = "ARN of the DuckDB Lambda layer (create manually first)"
  type        = string
  # You'll need to set this after creating the layer:
  # aws lambda publish-layer-version --layer-name duckdb-python311 ...
}

# S3 Module - Analytics Lake Bucket
module "s3" {
  source      = "./modules/s3"
  environment = var.environment
}

# IAM Module - Lambda Execution Role
module "iam" {
  source      = "./modules/iam"
  environment = var.environment
}

# Lambda Module - Ingest Function and Triggers
module "lambda" {
  source           = "./modules/lambda"
  environment      = var.environment
  lambda_role_arn  = module.iam.lambda_role_arn
  duckdb_layer_arn = var.duckdb_layer_arn
  analytics_bucket = module.s3.analytics_lake_bucket_id
  aws_region       = var.aws_region

  depends_on = [module.s3, module.iam]
}

# Bedrock Module - Agent Action Group and KB Data Sources
module "bedrock" {
  source           = "./modules/bedrock"
  environment      = var.environment
  duckdb_layer_arn = var.duckdb_layer_arn
  analytics_bucket = module.s3.analytics_lake_bucket_id
  aws_region       = var.aws_region

  depends_on = [module.s3, module.lambda]
}

# Outputs
output "analytics_bucket" {
  description = "Analytics lake bucket name"
  value       = module.s3.analytics_lake_bucket_id
}

output "lambda_function" {
  description = "Ingest Lambda function name"
  value       = module.lambda.lambda_function_name
}

output "lambda_function_arn" {
  description = "Ingest Lambda function ARN"
  value       = module.lambda.lambda_function_arn
}

output "source_buckets" {
  description = "Source S3 buckets (existing scraper backups)"
  value = {
    restaurants = "instagram-scraper-backups-kishore"
    posts       = "instagram-post-scraper-backups-kishore-us"
  }
}

output "query_lambda_arn" {
  description = "Query Lambda ARN for Bedrock action group"
  value       = module.bedrock.query_lambda_arn
}

output "validator_lambda_arn" {
  description = "Response Validator Lambda ARN"
  value       = module.bedrock.validator_lambda_arn
}
