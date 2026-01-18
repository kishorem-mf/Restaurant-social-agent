# Glue Module - Database and Catalog for Iceberg tables

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

# Glue Database for Instagram analytics
resource "aws_glue_catalog_database" "instagram_db" {
  name        = "instagram_db"
  description = "Instagram analytics database with Iceberg tables"

  tags = {
    Name        = "Instagram Database"
    Environment = var.environment
  }
}

variable "analytics_bucket" {
  description = "S3 bucket for analytics data"
  type        = string
  default     = "instagram-analytics-lake"
}

# Iceberg table for query logs
resource "aws_glue_catalog_table" "query_logs" {
  database_name = aws_glue_catalog_database.instagram_db.name
  name          = "query_logs"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "table_type"        = "ICEBERG"
    "metadata_location" = "s3://${var.analytics_bucket}/iceberg/query_logs/metadata/"
  }

  storage_descriptor {
    location      = "s3://${var.analytics_bucket}/iceberg/query_logs/"
    input_format  = "org.apache.iceberg.mr.hive.HiveIcebergInputFormat"
    output_format = "org.apache.iceberg.mr.hive.HiveIcebergOutputFormat"

    columns {
      name = "query_id"
      type = "string"
    }

    columns {
      name = "timestamp"
      type = "timestamp"
    }

    columns {
      name = "sql_query"
      type = "string"
    }

    columns {
      name = "success"
      type = "boolean"
    }

    columns {
      name = "error_message"
      type = "string"
    }

    columns {
      name = "execution_time_ms"
      type = "int"
    }

    columns {
      name = "row_count"
      type = "int"
    }

    columns {
      name = "columns_returned"
      type = "string"
    }

    ser_de_info {
      serialization_library = "org.apache.iceberg.mr.hive.HiveIcebergSerDe"
    }
  }

  # Partition by date for efficient time-based queries
  partition_keys {
    name = "date"
    type = "date"
  }
}

# Output
output "database_name" {
  description = "Glue database name"
  value       = aws_glue_catalog_database.instagram_db.name
}

output "query_logs_table_name" {
  description = "Query logs Iceberg table name"
  value       = aws_glue_catalog_table.query_logs.name
}

output "database_arn" {
  description = "Glue database ARN"
  value       = aws_glue_catalog_database.instagram_db.arn
}
