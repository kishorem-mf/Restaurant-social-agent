# Bedrock Guardrails - Anti-Hallucination Validation
#
# Uses Contextual Grounding to programmatically validate that the LLM's
# response is grounded in the actual query results. This is a FAIL-PROOF
# approach that blocks responses containing fabricated data.

variable "guardrails_environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

# Bedrock Guardrail for Anti-Hallucination
resource "aws_bedrock_guardrail" "anti_hallucination" {
  name                      = "instagram-anti-hallucination"
  description               = "Validates that responses are grounded in actual query results"
  blocked_input_messaging   = "Your request could not be processed."
  blocked_outputs_messaging = "I cannot provide specific data that wasn't found in the query results. Let me rephrase with verified information only."

  # Contextual Grounding - The key anti-hallucination feature
  # Checks if the model's response is factually consistent with the source data
  contextual_grounding_policy_config {
    filters_config {
      # Grounding threshold: How closely response must match source data
      # 0.7 = 70% confidence that claims are grounded in source
      type      = "GROUNDING"
      threshold = 0.7
    }
    filters_config {
      # Relevance threshold: How relevant response is to the query
      type      = "RELEVANCE"
      threshold = 0.5
    }
  }

  # Content filters for additional safety
  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
  }

  # Word policy - Block phrases that indicate fabrication
  word_policy_config {
    words_config {
      text = "for example, let's say"
    }
    words_config {
      text = "hypothetically"
    }
    words_config {
      text = "imagine if"
    }
    managed_word_lists_config {
      type = "PROFANITY"
    }
  }

  # Topic policy - Deny unverifiable claims
  topic_policy_config {
    topics_config {
      name       = "unverified_metrics"
      definition = "Specific engagement metrics (likes, comments, followers) that are not present in the query results"
      type       = "DENY"
      examples   = [
        "This post has 50,000 likes",
        "The creator has 100K followers",
        "Average engagement is 5%"
      ]
    }
    topics_config {
      name       = "fabricated_examples"
      definition = "Post IDs, creator names, or captions that were not returned in query results"
      type       = "DENY"
      examples   = [
        "Post MED001 shows",
        "Creator @foodie_example posted",
        "The caption reads 'example text'"
      ]
    }
  }

  tags = {
    Name        = "Instagram Anti-Hallucination Guardrail"
    Environment = var.guardrails_environment
    Purpose     = "Prevent fabrication in LLM responses"
  }
}

# Create a version of the guardrail for production use
resource "aws_bedrock_guardrail_version" "anti_hallucination_v1" {
  guardrail_arn = aws_bedrock_guardrail.anti_hallucination.guardrail_arn
  description   = "Initial version with contextual grounding"
}

# Outputs
output "guardrail_id" {
  description = "Guardrail ID for use with Bedrock Agent"
  value       = aws_bedrock_guardrail.anti_hallucination.guardrail_id
}

output "guardrail_arn" {
  description = "Guardrail ARN"
  value       = aws_bedrock_guardrail.anti_hallucination.guardrail_arn
}

output "guardrail_version" {
  description = "Guardrail version for production use"
  value       = aws_bedrock_guardrail_version.anti_hallucination_v1.version
}
