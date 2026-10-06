# InsightCard RAG Pro — AWS Infrastructure (Terraform)
#
# Creates:
#   - S3 bucket (document storage + event notifications)
#   - SQS queue + DLQ (message buffer for ingestion)
#   - DynamoDB tables (metadata + short-term memory)
#   - ECR repositories (container images)
#   - ECS cluster + Fargate services (backend, ingestion worker, frontend)
#   - ALB (load balancer)
#   - CloudWatch dashboards + alarms (monitoring)
#   - IAM roles and policies

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# --- S3 Bucket: Document Storage ---
resource "aws_s3_bucket" "docs" {
  bucket = "${var.project_name}-docs-${var.environment}"
  tags = { Name = "${var.project_name}-docs", Environment = var.environment }
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_notification" "docs" {
  bucket = aws_s3_bucket.docs.id
  queue {
    queue_arn     = aws_sqs_queue.ingestion.arn
    events        = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_suffix = ".md"
  }
}

# --- SQS Queue + DLQ ---
resource "aws_sqs_queue" "ingestion_dlq" {
  name = "${var.project_name}-ingestion-dlq"
  message_retention_seconds = 1209600  # 14 days
}

resource "aws_sqs_queue" "ingestion" {
  name                       = "${var.project_name}-ingestion"
  delay_seconds              = 0
  max_message_size           = 262144
  message_retention_seconds  = 86400    # 1 day
  visibility_timeout_seconds = 300      # 5 min (must >= max processing time)
  receive_wait_time_seconds  = 20       # long polling
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "ingestion" {
  queue_url = aws_sqs_queue.ingestion.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingestion.arn
      Condition = { ArnLike = { "aws:SourceArn" = aws_s3_bucket.docs.arn } }
    }]
  })
}

# --- DynamoDB: Metadata Table ---
resource "aws_dynamodb_table" "metadata" {
  name         = "${var.project_name}_metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Name = "${var.project_name}-metadata", Environment = var.environment }
}

# --- DynamoDB: Short-Term Memory Table ---
resource "aws_dynamodb_table" "memory" {
  name         = "${var.project_name}_memory"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Name = "${var.project_name}-memory", Environment = var.environment }
}

# --- ECR Repositories ---
resource "aws_ecr_repository" "backend" {
  name                 = "${var.project_name}/backend"
  image_tag_mutability = "MUTABLE"
}

resource "aws_ecr_repository" "ingestion" {
  name                 = "${var.project_name}/ingestion"
  image_tag_mutability = "MUTABLE"
}

resource "aws_ecr_repository" "frontend" {
  name                 = "${var.project_name}/frontend"
  image_tag_mutability = "MUTABLE"
}

# --- OpenSearch Domain (vector search) ---
resource "aws_opensearch_domain" "main" {
  domain_name    = var.project_name
  engine_version = "OpenSearch_2.11"

  cluster_config {
    instance_type        = "t3.small.search"
    instance_count       = 1
    zone_awareness_enabled = false
  }

  ebs_options {
    ebs_enabled = true
    volume_size = 10
  }

  encrypt_at_rest { enabled = true }
  node_to_node_encryption { enabled = true }
  domain_endpoint_options { enforce_https = true }

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.ecs_task.arn }
      Action    = ["es:ESHttp*"]
      Resource  = "arn:aws:es:${var.aws_region}:*:domain/${var.project_name}/*"
    }]
  })
}

# --- ECS Cluster ---
resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-${var.environment}"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# --- IAM Execution Role ---
resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --- IAM Task Role (app permissions: S3, SQS, DynamoDB) ---
resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "${var.project_name}-task-policy"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:HeadObject"], Resource = [
        aws_s3_bucket.docs.arn, "${aws_s3_bucket.docs.arn}/*" ] },
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = [
        aws_sqs_queue.ingestion.arn, aws_sqs_queue.ingestion_dlq.arn ] },
      { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:Scan"], Resource = [
        aws_dynamodb_table.metadata.arn, aws_dynamodb_table.memory.arn ] },
      { Effect = "Allow", Action = ["es:ESHttp*"], Resource = "arn:aws:es:${var.aws_region}:*:domain/${var.project_name}/*" },
      { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "*" },
    ]
  })
}

# --- CloudWatch Log Groups ---
resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${var.project_name}/backend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "ingestion" {
  name              = "/ecs/${var.project_name}/ingestion"
  retention_in_days = 30
}

# --- SSM Parameter: DeepSeek API Key (encrypted) ---
resource "aws_ssm_parameter" "deepseek_api_key" {
  name        = "/${var.project_name}/deepseek-api-key"
  type        = "SecureString"
  value       = var.deepseek_api_key
  description = "DeepSeek API key for LLM generation (OpenAI-compatible endpoint)"
}

# Allow ECS execution role to read the SSM parameter
resource "aws_iam_role_policy" "ecs_execution_ssm" {
  name = "${var.project_name}-exec-ssm-read"
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters", "ssm:GetParameter"]
      Resource = aws_ssm_parameter.deepseek_api_key.arn
    }]
  })
}

# --- Security Groups ---
resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb-sg"
  description = "Allow HTTP inbound for ALB"
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs" {
  name        = "${var.project_name}-ecs-sg"
  description = "Allow traffic from ALB"
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- ALB ---
resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
}

data "aws_subnets" "default" {
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_lb_target_group" "frontend" {
  name        = "${var.project_name}-frontend-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"
  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group" "backend" {
  name        = "${var.project_name}-backend-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"
  health_check {
    path                = "/api/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 100
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
  condition {
    path_pattern { values = ["/api/*"] }
  }
}

# --- ECS Task Definitions ---
resource "aws_ecs_task_definition" "backend" {
  family                   = "${var.project_name}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "backend"
    image     = "${aws_ecr_repository.backend.repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "VECTOR_STORE_TYPE", value = "opensearch" },
      { name = "OPENSEARCH_HOST", value = aws_opensearch_domain.main.endpoint },
      { name = "OPENSEARCH_PORT", value = "443" },
      { name = "OPENSEARCH_SSL", value = "true" },
      { name = "OPENSEARCH_AUTH", value = "aws" },
      { name = "EMBEDDING_PROVIDER", value = "local" },
      { name = "LLM_PROVIDER", value = "openai" },
      { name = "LLM_MODEL", value = "deepseek-chat" },
      { name = "OPENAI_BASE_URL", value = "https://api.deepseek.com" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "S3_BUCKET", value = aws_s3_bucket.docs.id },
      { name = "SQS_QUEUE_URL", value = aws_sqs_queue.ingestion.url },
      { name = "DDB_TABLE_METADATA", value = aws_dynamodb_table.metadata.name },
      { name = "DDB_TABLE_MEMORY", value = aws_dynamodb_table.memory.name },
    ]
    secrets = [
      { name = "OPENAI_API_KEY", valueFrom = aws_ssm_parameter.deepseek_api_key.arn }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "ingestion" {
  family                   = "${var.project_name}-ingestion"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "ingestion"
    image     = "${aws_ecr_repository.ingestion.repository_url}:latest"
    essential = true
    environment = [
      { name = "VECTOR_STORE_TYPE", value = "opensearch" },
      { name = "OPENSEARCH_HOST", value = aws_opensearch_domain.main.endpoint },
      { name = "OPENSEARCH_PORT", value = "443" },
      { name = "OPENSEARCH_SSL", value = "true" },
      { name = "OPENSEARCH_AUTH", value = "aws" },
      { name = "EMBEDDING_PROVIDER", value = "local" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "S3_BUCKET", value = aws_s3_bucket.docs.id },
      { name = "SQS_QUEUE_URL", value = aws_sqs_queue.ingestion.url },
      { name = "DDB_TABLE_METADATA", value = aws_dynamodb_table.metadata.name },
      { name = "DDB_TABLE_MEMORY", value = aws_dynamodb_table.memory.name },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ingestion.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${var.project_name}-frontend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "frontend"
    image     = "${aws_ecr_repository.frontend.repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 80, protocol = "tcp" }]
  }])
}

# --- ECS Services ---
resource "aws_ecs_service" "backend" {
  name            = "${var.project_name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100
}

resource "aws_ecs_service" "ingestion" {
  name            = "${var.project_name}-ingestion"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.ingestion.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }
}

resource "aws_ecs_service" "frontend" {
  name            = "${var.project_name}-frontend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 80
  }
}

# --- CloudWatch Alarms ---
resource "aws_cloudwatch_metric_alarm" "sqs_depth" {
  alarm_name          = "${var.project_name}-sqs-queue-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Sum"
  threshold           = 100
  alarm_description   = "SQS queue depth > 100 — ingestion worker is lagging"
  dimensions          = { QueueName = aws_sqs_queue.ingestion.name }
}

resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  alarm_name          = "${var.project_name}-dlq-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Messages in DLQ — processing failures need investigation"
  dimensions          = { QueueName = aws_sqs_queue.ingestion_dlq.name }
}

resource "aws_cloudwatch_metric_alarm" "backend_5xx" {
  alarm_name          = "${var.project_name}-backend-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Backend returning 5xx errors"
}

# --- Outputs ---
output "alb_dns" {
  value = aws_lb.main.dns_name
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.main.endpoint
}

output "s3_bucket" {
  value = aws_s3_bucket.docs.id
}

output "sqs_queue_url" {
  value = aws_sqs_queue.ingestion.url
}

output "dynamodb_metadata_table" {
  value = aws_dynamodb_table.metadata.name
}

output "dynamodb_memory_table" {
  value = aws_dynamodb_table.memory.name
}

output "ecr_backend_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "ecr_ingestion_url" {
  value = aws_ecr_repository.ingestion.repository_url
}

output "ecr_frontend_url" {
  value = aws_ecr_repository.frontend.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}
