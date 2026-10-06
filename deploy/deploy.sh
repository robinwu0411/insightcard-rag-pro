#!/usr/bin/env bash
set -euo pipefail

# Ensure terraform + aws are findable
export PATH="/usr/local/bin:/Users/robinwu/.local/bin:$PATH"

# ============================================================
#  InsightCard RAG Pro — One-Click Deploy Script
#  Usage: ./deploy/deploy.sh [--build] [--push] [--terraform] [--deploy]
# ============================================================

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_NAME="insightcard-rag"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '')"

if [ -z "$AWS_ACCOUNT_ID" ]; then
  echo "ERROR: AWS CLI not configured. Run 'aws configure' first."
  exit 1
fi

echo "============================================================"
echo "  InsightCard RAG Pro — Deploy"
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo "  Region: $AWS_REGION"
echo "============================================================"

# --- Step 1: Build Docker images ---
build_images() {
  echo -e "\n[1/4] Building Docker images..."
  cd "$PROJECT_ROOT"

  export DOCKER_BUILDKIT=0
  docker build -t "$PROJECT_NAME/backend:latest" -f backend/Dockerfile .
  docker build -t "$PROJECT_NAME/ingestion:latest" -f ingestion/Dockerfile .
  docker build -t "$PROJECT_NAME/frontend:latest" -f frontend/Dockerfile .

  echo "  Done. 3 images built."
}

# --- Step 2: Push to ECR ---
push_images() {
  echo -e "\n[2/4] Pushing images to ECR..."

  # Login to ECR first
  aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com" 2>/dev/null

  for repo in backend ingestion frontend; do
    ECR_URI="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$PROJECT_NAME/$repo"
    echo "  Pushing $repo → $ECR_URI"
    docker tag "$PROJECT_NAME/$repo:latest" "$ECR_URI:latest"
    docker push "$ECR_URI:latest"
  done

  echo "  Done. 3 images pushed."
}

# --- Step 3: Terraform apply ---
run_terraform() {
  echo -e "\n[3/4] Applying Terraform infrastructure..."
  cd "$PROJECT_ROOT/infra"

  # DeepSeek API key: env var takes priority, then existing terraform.tfvars
  if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    echo "deepseek_api_key = \"${DEEPSEEK_API_KEY}\"" > terraform.tfvars
    echo "  DeepSeek API key loaded from \$DEEPSEEK_API_KEY -> terraform.tfvars"
  elif [ -f terraform.tfvars ]; then
    echo "  Using existing terraform.tfvars"
  else
    echo "  WARNING: DEEPSEEK_API_KEY not set and no terraform.tfvars — LLM will fall back to template mode"
  fi

  terraform init
  terraform plan -out=tfplan
  terraform apply tfplan

  echo "  Infrastructure created."
  terraform output
}

# --- Step 4: Update ECS services ---
update_services() {
  echo -e "\n[4/4] Updating ECS services (rolling deploy)..."
  CLUSTER="$PROJECT_NAME-prod"

  for svc in backend ingestion frontend; do
    echo "  Updating $svc..."
    aws ecs update-service \
      --cluster "$CLUSTER" \
      --service "$PROJECT_NAME-$svc" \
      --force-new-deployment \
      --region "$AWS_REGION"
  done

  echo "  Waiting for deployments to stabilize..."
  for svc in backend ingestion frontend; do
    aws ecs wait services-stable \
      --cluster "$CLUSTER" \
      --services "$PROJECT_NAME-$svc" \
      --region "$AWS_REGION" 2>/dev/null || true
  done
}

# --- Run ---
if [ "${1:-}" = "--all" ] || [ "${1:-}" = "" ]; then
  run_terraform
  build_images
  push_images
  update_services
else
  for arg in "$@"; do
    case $arg in
      --build)   build_images ;;
      --push)    push_images ;;
      --terraform) run_terraform ;;
      --deploy)  update_services ;;
    esac
  done
fi

echo -e "\n============================================================"
echo "  Deploy complete!"
echo "  ALB DNS: $(cd infra && terraform output -raw alb_dns 2>/dev/null || echo 'run terraform output')"
echo "============================================================"
