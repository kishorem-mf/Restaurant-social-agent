# Instagram Analytics Infrastructure Deployment Guide

This guide explains how to deploy the Instagram Analytics infrastructure using Terraform.

## Prerequisites

1. **AWS CLI configured** with appropriate credentials
2. **Terraform installed** (>= 1.5.0)
3. **DuckDB Lambda Layer** already created in AWS (ARN: `arn:aws:lambda:us-east-1:855673866222:layer:duckdb-python311:1`)
4. **Azure OpenAI credentials** stored in AWS Secrets Manager as `azure-openai-credentials`

## Architecture Overview

The infrastructure includes:
- **Orchestrator Lambda**: LLM-driven chat orchestrator with hybrid SQL + semantic search
- **Query Data Lambda**: Executes SQL queries on Parquet files using DuckDB
- **Response Validator Lambda**: Validates LLM responses against query evidence
- **API Gateway**: REST API endpoint for chat interface
- **S3 Bucket**: Analytics data lake for Parquet files
- **IAM Roles**: Appropriate permissions for Lambda functions
- **CloudWatch Log Groups**: Logging for all Lambda functions

## Deployment Steps

### 1. Configure Variables

Edit `terraform.tfvars` (already configured):

```hcl
aws_region  = "us-east-1"
environment = "prod"
duckdb_layer_arn = "arn:aws:lambda:us-east-1:855673866222:layer:duckdb-python311:1"
```

### 2. Initialize Terraform

```bash
cd infrastructure
terraform init
```

### 3. Review Changes

```bash
terraform plan
```

This will show you what Terraform will create, modify, or destroy.

### 4. Deploy Infrastructure

```bash
terraform apply
```

Type `yes` when prompted to confirm.

### 5. Get API Endpoint

After deployment, get the API Gateway URL:

```bash
terraform output api_gateway_url
```

Example output: `https://o5ha32swh7.execute-api.us-east-1.amazonaws.com/prod/chat`

## Deployed Resources

### Lambda Functions
- `instagram-orchestrator` - Main chat orchestrator (300s timeout, 1024MB memory)
- `instagram-query-data` - SQL query executor (180s timeout, 1024MB memory)
- `instagram-response-validator` - Response validator (60s timeout, 512MB memory)

### API Gateway
- REST API: `instagram-analytics-api`
- Stage: `prod`
- Endpoint: POST `/chat`
- CORS enabled for cross-origin requests

### S3 Bucket
- `instagram-analytics-lake` - Parquet data storage with versioning enabled

### IAM Roles
- `instagram-orchestrator-lambda-role` - Orchestrator permissions
- `instagram-query-lambda-role` - Query data permissions
- `instagram-validator-lambda-role` - Validator permissions

## Updating Lambda Code

To update Lambda function code without full redeployment:

### Update Orchestrator Lambda
```bash
cd lambdas/orchestrator
zip -r /tmp/orchestrator.zip . -x "*.pyc" -x "__pycache__/*" -x "venv/*"
aws lambda update-function-code \
  --function-name instagram-orchestrator \
  --zip-file fileb:///tmp/orchestrator.zip \
  --region us-east-1
```

### Update via Terraform
```bash
cd infrastructure
terraform apply -target=module.bedrock.aws_lambda_function.orchestrator
```

## Destroying Infrastructure

⚠️ **WARNING**: This will delete all resources including the S3 bucket and data.

```bash
terraform destroy
```

## Troubleshooting

### Resource Already Exists Errors

If you get "resource already exists" errors, import the existing resource:

```bash
# Example: Import Lambda function
terraform import module.bedrock.aws_lambda_function.orchestrator instagram-orchestrator
```

### State Drift

To check if your infrastructure has drifted from Terraform state:

```bash
terraform plan -refresh-only
```

### CloudWatch Logs

View Lambda logs:

```bash
# Orchestrator logs
aws logs tail /aws/lambda/instagram-orchestrator --follow

# Query data logs
aws logs tail /aws/lambda/instagram-query-data --follow
```

## Testing Deployment

### Test API Gateway Endpoint

```bash
curl -X POST https://<api-gateway-url>/prod/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "top posts on italian cuisine",
    "conversation_history": []
  }'
```

### Test via UI

1. Update `ui/chat-test.html` with the API Gateway URL
2. Start local server: `python lambdas/orchestrator/server.py`
3. Open browser: `http://localhost:8000`

## Cost Optimization

- **Lambda**: Pay per invocation (first 1M requests/month free)
- **API Gateway**: Pay per request ($3.50/million requests)
- **S3**: Pay for storage and requests
- **CloudWatch Logs**: First 5GB/month free

Estimated monthly cost for moderate usage: ~$10-50

## Security Best Practices

1. ✅ **IAM Roles**: Least privilege permissions configured
2. ✅ **S3 Bucket**: Public access blocked, versioning enabled
3. ✅ **Secrets**: Azure OpenAI credentials stored in Secrets Manager
4. ✅ **API Gateway**: CORS configured for allowed origins
5. ⚠️ **API Key**: Consider adding API Gateway API keys for production

## Monitoring

### CloudWatch Dashboards

Create a custom dashboard to monitor:
- Lambda invocation count
- Lambda error rate
- Lambda duration
- API Gateway latency

### Alarms

Set up CloudWatch alarms for:
- Lambda errors > 5% error rate
- Lambda duration > 25 seconds (API Gateway timeout is 29s)
- API Gateway 5xx errors

## Next Steps

1. Configure CloudWatch alarms
2. Set up API Gateway API keys for production
3. Enable X-Ray tracing for distributed tracing
4. Configure backup policies for S3 bucket
5. Implement CI/CD pipeline for automated deployments

## Support

For issues or questions:
- Check CloudWatch logs first
- Review Terraform state: `terraform show`
- Verify AWS console matches Terraform state
