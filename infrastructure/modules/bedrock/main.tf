# Bedrock Module - Agent Action Group and KB Data Sources

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
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

variable "agent_id" {
  description = "Existing Bedrock Agent ID"
  type        = string
  default     = "41OTBCJO2G"
}

variable "aws_account_id" {
  description = "AWS Account ID"
  type        = string
  default     = "855673866222"
}

variable "knowledge_base_id" {
  description = "Existing Knowledge Base ID"
  type        = string
  default     = "QQJTQJ1VWU"
}

variable "restaurant_bucket" {
  description = "Restaurant backups bucket name"
  type        = string
  default     = "instagram-scraper-backups-kishore"
}

# IAM Role for Query Lambda
resource "aws_iam_role" "query_lambda" {
  name = "instagram-query-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "Instagram Query Lambda Role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "query_lambda" {
  name = "instagram-query-lambda-policy"
  role = aws_iam_role.query_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.analytics_bucket}",
          "arn:aws:s3:::${var.analytics_bucket}/*"
        ]
      }
    ]
  })
}

# Archive the Query Lambda code
data "archive_file" "query_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/query_data"
  output_path = "${path.module}/../../../lambdas/query_data.zip"
}

# Query Lambda function
resource "aws_lambda_function" "query_data" {
  function_name = "instagram-query-data"
  role          = aws_iam_role.query_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512

  filename         = data.archive_file.query_lambda.output_path
  source_code_hash = data.archive_file.query_lambda.output_base64sha256

  layers = [var.duckdb_layer_arn]

  environment {
    variables = {
      ANALYTICS_BUCKET = var.analytics_bucket
      S3_REGION        = var.aws_region
    }
  }

  tags = {
    Name        = "Instagram Query Lambda"
    Environment = var.environment
  }
}

# CloudWatch Log Group for Query Lambda
resource "aws_cloudwatch_log_group" "query_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.query_data.function_name}"
  retention_in_days = 14

  tags = {
    Name        = "Instagram Query Lambda Logs"
    Environment = var.environment
  }
}

# Permission for Bedrock to invoke Query Lambda (already exists)
# resource "aws_lambda_permission" "bedrock_invoke" {
#   statement_id  = "AllowBedrockInvoke"
#   action        = "lambda:InvokeFunction"
#   function_name = aws_lambda_function.query_data.function_name
#   principal     = "bedrock.amazonaws.com"
#   source_arn    = "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:agent/${var.agent_id}"
# }

# Upload OpenAPI schema to S3 for Bedrock
resource "aws_s3_object" "api_schema" {
  bucket = var.analytics_bucket
  key    = "schemas/analytics-api.json"
  source = "${path.module}/../../../infrastructure/schemas/analytics-api.json"
  etag   = filemd5("${path.module}/../../../infrastructure/schemas/analytics-api.json")

  content_type = "application/json"
}

# Bedrock Agent Action Group (already exists - managed manually)
# resource "aws_bedrockagent_agent_action_group" "analytics" {
#   action_group_name          = "analytics-query"
#   agent_id                   = var.agent_id
#   agent_version              = "DRAFT"
#   description                = "Query Instagram posts and restaurant analytics data using DuckDB"
#   skip_resource_in_use_check = true
#
#   action_group_executor {
#     lambda = aws_lambda_function.query_data.arn
#   }
#
#   api_schema {
#     s3 {
#       s3_bucket_name = var.analytics_bucket
#       s3_object_key  = aws_s3_object.api_schema.key
#     }
#   }
# }

# Restaurant data source already exists in Knowledge Base (created manually)
# Commenting out to avoid conflict
# resource "aws_bedrockagent_data_source" "restaurants" {
#   knowledge_base_id = var.knowledge_base_id
#   name              = "restaurant-profiles"
#   description       = "Restaurant Instagram profile data"
#
#   data_source_configuration {
#     type = "S3"
#     s3_configuration {
#       bucket_arn = "arn:aws:s3:::${var.restaurant_bucket}"
#     }
#   }
# }

# =============================================================================
# RESPONSE VALIDATOR LAMBDA (Anti-Hallucination)
# =============================================================================

# IAM Role for Validator Lambda
resource "aws_iam_role" "validator_lambda" {
  name = "instagram-validator-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "Instagram Validator Lambda Role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "validator_lambda" {
  name = "instagram-validator-lambda-policy"
  role = aws_iam_role.validator_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "S3AccessForValidation"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.analytics_bucket}",
          "arn:aws:s3:::${var.analytics_bucket}/*"
        ]
      }
    ]
  })
}

# Archive the Validator Lambda code
data "archive_file" "validator_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/response_validator"
  output_path = "${path.module}/../../../lambdas/response_validator.zip"
}

# Validator Lambda function
# Now includes DuckDB for INDEPENDENT query verification
resource "aws_lambda_function" "response_validator" {
  function_name = "instagram-response-validator"
  role          = aws_iam_role.validator_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 60      # Increased for DuckDB query execution
  memory_size   = 512     # Increased for DuckDB memory requirements

  filename         = data.archive_file.validator_lambda.output_path
  source_code_hash = data.archive_file.validator_lambda.output_base64sha256

  # Add DuckDB layer for independent query verification
  layers = [var.duckdb_layer_arn]

  environment {
    variables = {
      LOG_LEVEL        = "INFO"
      ANALYTICS_BUCKET = var.analytics_bucket
      S3_REGION        = var.aws_region
    }
  }

  tags = {
    Name        = "Instagram Response Validator"
    Environment = var.environment
    Purpose     = "Anti-hallucination validation with independent verification"
  }
}

# CloudWatch Log Group for Validator Lambda
resource "aws_cloudwatch_log_group" "validator_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.response_validator.function_name}"
  retention_in_days = 14

  tags = {
    Name        = "Instagram Validator Lambda Logs"
    Environment = var.environment
  }
}

# Permission for Bedrock to invoke Validator Lambda
resource "aws_lambda_permission" "bedrock_invoke_validator" {
  statement_id  = "AllowBedrockInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.response_validator.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:agent/${var.agent_id}"
}

# Upload Validator OpenAPI schema to S3
resource "aws_s3_object" "validator_schema" {
  bucket = var.analytics_bucket
  key    = "schemas/validator-api.json"
  source = "${path.module}/../../../infrastructure/schemas/validator-api.json"
  etag   = filemd5("${path.module}/../../../infrastructure/schemas/validator-api.json")

  content_type = "application/json"
}

# Validator Action Group - Create manually in AWS Console if needed
# The validator Lambda is deployed and can be invoked directly
# Bedrock action group creation has schema validation issues
# resource "aws_bedrockagent_agent_action_group" "validator" {
#   action_group_name          = "response-validator"
#   agent_id                   = var.agent_id
#   agent_version              = "DRAFT"
#   description                = "Validates responses against query evidence"
#   skip_resource_in_use_check = true
#
#   action_group_executor {
#     lambda = aws_lambda_function.response_validator.arn
#   }
#
#   api_schema {
#     s3 {
#       s3_bucket_name = var.analytics_bucket
#       s3_object_key  = aws_s3_object.validator_schema.key
#     }
#   }
# }

# =============================================================================
# ORCHESTRATOR LAMBDA (LLM-Driven Routing with Hybrid Approach)
# =============================================================================

# IAM Role for Orchestrator Lambda
resource "aws_iam_role" "orchestrator_lambda" {
  name = "instagram-orchestrator-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "Instagram Orchestrator Lambda Role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "orchestrator_lambda" {
  name = "instagram-orchestrator-lambda-policy"
  role = aws_iam_role.orchestrator_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "S3AccessForQuery"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.analytics_bucket}",
          "arn:aws:s3:::${var.analytics_bucket}/*"
        ]
      },
      {
        Sid    = "BedrockKnowledgeBaseAccess"
        Effect = "Allow"
        Action = [
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate"
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:knowledge-base/${var.knowledge_base_id}"
      },
      {
        Sid    = "BedrockModelAccess"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/*"
      },
      {
        Sid    = "SecretsManagerAccess"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:azure-openai-credentials-*"
      },
      {
        Sid    = "InvokeLambdaTools"
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = [
          aws_lambda_function.query_data.arn,
          aws_lambda_function.response_validator.arn
        ]
      }
    ]
  })
}

# Archive the Orchestrator Lambda code
data "archive_file" "orchestrator_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambdas/orchestrator"
  output_path = "${path.module}/../../../lambdas/orchestrator.zip"
  excludes    = ["__pycache__", "*.pyc", "tests", "server.py", ".pytest_cache"]
}

# Orchestrator Lambda function
resource "aws_lambda_function" "orchestrator" {
  function_name = "instagram-orchestrator"
  role          = aws_iam_role.orchestrator_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 300  # 5 minutes for LLM processing
  memory_size   = 1024 # More memory for LLM orchestration

  filename         = data.archive_file.orchestrator_lambda.output_path
  source_code_hash = data.archive_file.orchestrator_lambda.output_base64sha256

  layers = [var.duckdb_layer_arn]

  environment {
    variables = {
      ANALYTICS_BUCKET             = var.analytics_bucket
      S3_REGION                    = var.aws_region
      KNOWLEDGE_BASE_ID            = var.knowledge_base_id
      AWS_ACCOUNT_ID               = var.aws_account_id
      QUERY_DATA_LAMBDA            = aws_lambda_function.query_data.function_name
      RESPONSE_VALIDATOR_LAMBDA    = aws_lambda_function.response_validator.function_name
      AZURE_OPENAI_SECRET_NAME     = "azure-openai-credentials"
    }
  }

  tags = {
    Name        = "Instagram Orchestrator Lambda"
    Environment = var.environment
    Purpose     = "LLM-driven routing with hybrid SQL+semantic search"
  }
}

# CloudWatch Log Group for Orchestrator Lambda
resource "aws_cloudwatch_log_group" "orchestrator_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.orchestrator.function_name}"
  retention_in_days = 14

  tags = {
    Name        = "Instagram Orchestrator Lambda Logs"
    Environment = var.environment
  }
}

# =============================================================================
# API GATEWAY (REST API for Orchestrator)
# =============================================================================

resource "aws_api_gateway_rest_api" "orchestrator" {
  name        = "instagram-analytics-api"
  description = "API for Instagram Analytics Chat Orchestrator"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = {
    Name        = "Instagram Analytics API"
    Environment = var.environment
  }
}

# /chat resource
resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.orchestrator.id
  parent_id   = aws_api_gateway_rest_api.orchestrator.root_resource_id
  path_part   = "chat"
}

# POST /chat method
resource "aws_api_gateway_method" "chat_post" {
  rest_api_id   = aws_api_gateway_rest_api.orchestrator.id
  resource_id   = aws_api_gateway_resource.chat.id
  http_method   = "POST"
  authorization = "NONE"
}

# Lambda integration for POST /chat
resource "aws_api_gateway_integration" "chat_post" {
  rest_api_id = aws_api_gateway_rest_api.orchestrator.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orchestrator.invoke_arn
}

# CORS OPTIONS method for /chat
resource "aws_api_gateway_method" "chat_options" {
  rest_api_id   = aws_api_gateway_rest_api.orchestrator.id
  resource_id   = aws_api_gateway_resource.chat.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "chat_options" {
  rest_api_id = aws_api_gateway_rest_api.orchestrator.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_options.http_method

  type = "MOCK"
  request_templates = {
    "application/json" = "{\"statusCode\": 200}"
  }
}

resource "aws_api_gateway_method_response" "chat_options" {
  rest_api_id = aws_api_gateway_rest_api.orchestrator.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_options.http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }

  response_models = {
    "application/json" = "Empty"
  }
}

resource "aws_api_gateway_integration_response" "chat_options" {
  rest_api_id = aws_api_gateway_rest_api.orchestrator.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_options.http_method
  status_code = aws_api_gateway_method_response.chat_options.status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS,POST,PUT'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}

# Lambda permission for API Gateway to invoke orchestrator
resource "aws_lambda_permission" "api_gateway_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.orchestrator.execution_arn}/*/*"
}

# API Gateway deployment
resource "aws_api_gateway_deployment" "orchestrator" {
  depends_on = [
    aws_api_gateway_integration.chat_post,
    aws_api_gateway_integration.chat_options
  ]

  rest_api_id = aws_api_gateway_rest_api.orchestrator.id

  lifecycle {
    create_before_destroy = true
  }

  # Trigger redeployment when integration changes
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_integration.chat_post.id,
      aws_api_gateway_integration.chat_options.id,
    ]))
  }
}

# API Gateway Stage
resource "aws_api_gateway_stage" "orchestrator" {
  deployment_id = aws_api_gateway_deployment.orchestrator.id
  rest_api_id   = aws_api_gateway_rest_api.orchestrator.id
  stage_name    = var.environment

  tags = {
    Name        = "Instagram Analytics API Stage"
    Environment = var.environment
  }
}

# =============================================================================
# OUTPUTS
# =============================================================================

output "query_lambda_arn" {
  description = "Query Lambda ARN"
  value       = aws_lambda_function.query_data.arn
}

output "validator_lambda_arn" {
  description = "Validator Lambda ARN"
  value       = aws_lambda_function.response_validator.arn
}

output "orchestrator_lambda_arn" {
  description = "Orchestrator Lambda ARN"
  value       = aws_lambda_function.orchestrator.arn
}

output "api_gateway_url" {
  description = "API Gateway endpoint URL for chat"
  value       = "https://${aws_api_gateway_rest_api.orchestrator.id}.execute-api.${var.aws_region}.amazonaws.com/${aws_api_gateway_stage.orchestrator.stage_name}/chat"
}
