variable "aws_region" {
  default = "us-east-1"
}
variable "project_name" {
  default = "insightcard-rag"
}
variable "environment" {
  default = "prod"
}
variable "deepseek_api_key" {
  description = "DeepSeek API key (OpenAI-compatible endpoint). Stored encrypted in SSM Parameter Store."
  type        = string
  default     = ""
  sensitive   = true
}
