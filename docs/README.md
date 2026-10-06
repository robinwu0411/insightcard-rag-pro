# InsightCard RAG Pro — 完整技术文档

## 目录
1. [架构总览](#1-架构总览)
2. [组件清单](#2-组件清单)
3. [项目结构](#3-项目结构)
4. [部署步骤](#4-部署步骤)
5. [灰度发布与验证](#5-灰度发布与验证)
6. [上线后问题发现](#6-上线后问题发现)
7. [优化指南](#7-优化指南)
8. [准确度评估标准](#8-准确度评估标准)

---

## 1. 架构总览

```
                    用户浏览器
                        │
                        ▼
                ┌───────────────┐
                │   ALB (80)    │  ← Application Load Balancer
                └───┬───────┬───┘
                    │       │
          /api/*    │       │  /*
                    ▼       ▼
     ┌──────────────┐  ┌──────────────┐
     │  Backend     │  │  Frontend    │
     │  (FastAPI)   │  │  (Nginx+React)│
     │  ECS × 2     │  │  ECS × 1     │
     └──────┬───────┘  └──────────────┘
            │
     ┌──────┼──────────────────┐
     │      │                  │
     ▼      ▼                  ▼
┌────────┐ ┌────────────┐ ┌──────────┐
│DynamoDB│ │ 向量数据库  │ │ OpenAI/  │
│(记忆+  │ │ (OpenSearch/│ │ Bedrock  │
│ 元数据)│ │  Milvus)   │ │ (LLM)    │
└────────┘ └────────────┘ └──────────┘

     离线数据准备（独立 ECS Service）
     ┌──────────────┐
     │ Ingestion    │
     │ Worker       │
     │ ECS × 1      │
     └──┬───────────┘
        │ 轮询
        ▼
   ┌─────────┐     事件通知      ┌──────────┐
   │ SQS     │ ←────────────── │ S3 Bucket│
   │ + DLQ   │                 │ (文档存储)│
   └─────────┘                 └──────────┘
        │
        ▼
   ┌────────────┐
   │ DynamoDB   │  ← 去重 + 状态追踪
   │ (metadata) │
   └────────────┘

监控: CloudWatch (日志 + 指标 + 告警)
CI/CD: GitHub Actions (按路径过滤独立构建)
```

### 三个 ECS Service

| Service | 容器 | CPU/内存 | 副本数 | 触发方式 |
|---------|------|---------|--------|---------|
| Frontend | Nginx + React 静态文件 | 0.25 vCPU / 0.5GB | 1 | HTTP (ALB) |
| Backend | FastAPI + RAG retriever | 0.5 vCPU / 1GB | 2 | HTTP (ALB → /api/*) |
| Ingestion | Python Worker (SQS consumer) | 1 vCPU / 2GB | 1 | SQS 长轮询 |

---

## 2. 组件清单

### AWS 服务

| 组件 | 服务 | 用途 | 计费方式 |
|------|------|------|---------|
| 文档存储 | S3 | 存储原始知识文档 (PDF/Word/MD) | 按存储量 + 请求 |
| 事件通知 | S3 → SQS | 文件上传/删除自动触发事件 | 免费 (S3 事件) |
| 消息队列 | SQS + DLQ | 解耦 S3 和 Worker，缓冲消息 | 按请求 |
| 元数据库 | DynamoDB | 文档处理状态 + 去重 (file_hash) | 按需 (PAY_PER_REQUEST) |
| 短期记忆 | DynamoDB | 会话上下文 (TTL 自动过期) | 按需 |
| 容器镜像 | ECR × 3 | 存储 Docker 镜像 | 按存储量 |
| 容器运行 | ECS Fargate | 无服务器容器运行 | 按 vCPU/GB·秒 |
| 负载均衡 | ALB | HTTP 路由 (/api/* → backend, /* → frontend) | 按小时 + LCU |
| 监控 | CloudWatch | 日志 + 指标 + 告警 | 按指标/日志量 |

### 代码组件

| 模块 | 文件 | 职责 |
|------|------|------|
| shared/config | shared/config.py | 全局配置 (环境变量驱动) |
| shared/vector_store | shared/vector_store.py | 向量库抽象 (OpenSearch/Milvus) |
| shared/embedding | shared/embedding_client.py | Embedding 客户端 (local/TEI/OpenAI) |
| shared/dynamodb | shared/dynamodb_client.py | DynamoDB 封装 (元数据+记忆) |
| shared/s3 | shared/s3_client.py | S3 文件下载/上传 |
| backend/api | backend/main.py | FastAPI HTTP 端点 + SSE |
| backend/retriever | backend/rag/retriever.py | 两阶段检索 (向量+rerank+MMR) |
| backend/generator | backend/rag/generator.py | LLM 生成 (OpenAI/Bedrock/模板) |
| backend/pipeline | backend/rag/pipeline.py | 编排 (metric→retrieve→generate) |
| backend/chunker | backend/rag/chunker.py | Markdown 感知分块 |
| backend/cleaner | backend/rag/cleaner.py | 清洗 (PII/去噪/去重/质检) |
| backend/tools | backend/tools/metric_tools.py | 指标数据 (模拟 Ripple) |
| ingestion/worker | ingestion/worker.py | SQS 消费 + 全链路处理 |

---

## 3. 项目结构

```
insightcard-rag-pro/
├── shared/                         # 公共代码 (COPY 进两个镜像)
│   ├── config.py                   # 全局配置
│   ├── vector_store.py             # 向量库抽象层
│   ├── embedding_client.py         # Embedding 客户端
│   ├── dynamodb_client.py          # DynamoDB 封装
│   ├── s3_client.py                # S3 封装
│   └── requirements.txt
│
├── backend/                        # → ECS Service 2 (Backend API)
│   ├── Dockerfile
│   ├── main.py                     # FastAPI 入口
│   ├── config.py                   # 配置 re-export
│   ├── api/                        # API 路由 (未来拆分)
│   ├── rag/                        # RAG 在线检索
│   │   ├── retriever.py            #   向量检索 + 混合 rerank + MMR
│   │   ├── generator.py            #   LLM 生成 (3 种模式)
│   │   ├── pipeline.py             #   编排器
│   │   ├── chunker.py              #   分块器 (共享)
│   │   └── cleaner.py              #   清洗器 (共享)
│   ├── tools/
│   │   └── metric_tools.py         # 指标数据
│   ├── knowledge_docs/             # 7 个知识文档
│   └── requirements.txt
│
├── ingestion/                      # → ECS Service 3 (Worker)
│   ├── Dockerfile
│   ├── worker.py                   # SQS 消费 + 全链路处理
│   └── requirements.txt
│
├── frontend/                       # → ECS Service 1 (Frontend)
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── App.jsx                 # 主页面
│       ├── InsightCard.jsx         # 卡片组件
│       └── main.jsx
│
├── infra/                          # Terraform 基础设施
│   ├── main.tf                     # 全部 AWS 资源
│   └── variables.tf
│
├── deploy/
│   └── deploy.sh                   # 一键部署脚本
│
├── .github/workflows/              # CI/CD
│   ├── deploy-backend.yml          # 改 backend/ → 只构建部署 backend+ingestion
│   └── deploy-frontend.yml         # 改 frontend/ → 只构建部署 frontend
│
├── docker-compose.yml              # 本地开发
└── README.md
```

---

## 4. 部署步骤

### 前置准备

```bash
# 1. 安装 AWS CLI
sudo installer -pkg /tmp/AWSCLIV2.pkg -target /

# 2. 配置 AWS 凭证
aws configure
# 输入: Access Key ID, Secret Access Key, Region (us-east-1), output format (json)

# 3. 安装 Terraform
brew install terraform  # 或从 https://terraform.io 下载

# 4. 验证
aws sts get-caller-identity  # 确认凭证有效
terraform version
```

### 本地开发

```bash
cd ~/Desktop/insightcard-rag-pro

# 一键启动全部服务 (backend + ingestion + frontend)
docker-compose up --build

# 访问
open http://localhost:5173        # 前端
open http://localhost:8000/docs   # API 文档
```

### 生产部署

```bash
cd ~/Desktop/insightcard-rag-pro

# 一键部署 (构建镜像 → 推送 ECR → Terraform → 更新 ECS)
chmod +x deploy/deploy.sh
./deploy/deploy.sh --all

# 或分步执行
./deploy/deploy.sh --build      # 构建 Docker 镜像
./deploy/deploy.sh --push       # 推送到 ECR
./deploy/deploy.sh --terraform  # 创建 AWS 基础设施
./deploy/deploy.sh --deploy     # 滚动更新 ECS 服务
```

### 部署后验证

```bash
# 获取 ALB DNS
cd infra && terraform output alb_dns

# 健康检查
curl http://<ALB_DNS>/api/health
# 期望: {"status":"ok","knowledge_base_chunks":78,...}

# 触发知识入库
curl -X POST http://<ALB_DNS>/api/rag/ingest

# 测试 SSE 流
curl -N http://<ALB_DNS>/api/insight/stream?metric_name=net_ppm
```

---

## 5. 灰度发布与验证

### 5.1 灰度发布流程

```
新版本代码 → 推到 main 分支
    ↓
GitHub Actions 自动构建镜像 → 推 ECR (tag: latest + commit-sha)
    ↓
ECS 滚动更新 (deployment_minimum_healthy_percent=100)
    ↓
新 Task 启动 → ALB 健康检查 ( /api/health )
    ↓
健康检查通过 → 旧 Task 优雅停止
    ↓
CloudWatch 指标观察 15 分钟
    ↓
  ├─ 指标正常 → 灰度完成
    └─ 指标异常 → 回滚 (重新部署旧 tag)
```

### 5.2 ECS 滚动部署配置

```hcl
# Terraform 中已配置:
deployment_maximum_percent         = 200  # 最多 2x 副本 (先起新的再停旧的)
deployment_minimum_healthy_percent = 100  # 至少保持 1x 健康副本
```

### 5.3 回滚操作

```bash
# 查看部署历史
aws ecs describe-services --cluster insightcard-rag-prod --service insightcard-rag-backend \
  --query 'services[0].deployments' --output table

# 回滚到上一版本 (用旧 tag)
aws ecs describe-task-definition --task-definition insightcard-rag-backend:REVISION \
  --query 'taskDefinition.containerDefinitions[0].image' --output text

# 用旧镜像重新部署
aws ecs update-service --cluster insightcard-rag-prod \
  --service insightcard-rag-backend \
  --task-definition insightcard-rag-backend:PREVIOUS_REVISION \
  --force-new-deployment
```

### 5.4 灰度验证 Checklist

- [ ] 健康检查 `/api/health` 返回 200
- [ ] SSE `/api/insight/stream` 能正常流式输出
- [ ] CloudWatch 5xx 错误率 < 1%
- [ ] SQS 队列深度无异常增长
- [ ] DynamoDB 读写延迟 < 10ms
- [ ] LLM 生成延迟 P99 < 5s (OpenAI) 或 < 2s (Bedrock)
- [ ] 知识库 chunk 数量与预期一致

---

## 6. 上线后问题发现

### 6.1 监控面板

```bash
# CloudWatch 指标看板
aws cloudwatch get-dashboard --dashboard-name insightcard-rag 2>/dev/null || \
  aws cloudwatch put-dashboard --dashboard-name insightcard-rag \
    --dashboard-body file://deploy/dashboard.json
```

### 6.2 关键告警 (已在 Terraform 中配置)

| 告警 | 触发条件 | 含义 | 处理 |
|------|---------|------|------|
| SQS 队列深度 | > 100 条 | Worker 处理跟不上 | 扩容 ingestion 副本数 |
| DLQ 消息 | > 0 | 处理失败 3 次 | 查 DLQ 消息内容，修复 bug |
| Backend 5xx | > 5/min | API 服务异常 | 查看 CloudWatch Logs |
| ALB 健康检查失败 | target < 1 | 容器挂了 | ECS 自动重启 |

### 6.3 常见问题排查

#### 问题: 检索结果不相关
```
排查路径:
1. curl /api/insight/preview?metric_name=net_ppm → 看 retrieved_chunks
2. 检查 semantic_score 和 rerank_score
3. 如果 semantic_score 都很低 → embedding 模型问题或知识库内容不对
4. 如果 semantic_score 高但 rerank 不对 → rerank 权重需调整
5. 如果 MMR 过滤掉了相关结果 → 调整 lambda_param
```

#### 问题: LLM 生成不引用知识库内容
```
排查路径:
1. 检查 prompt 是否包含 retrieved chunks → 看 _build_openai_prompt()
2. 检查 chunks 文本是否完整 (未被截断)
3. 如果用 template mode → 检查 _extract_actions_from_chunks 正则是否匹配
4. 切换到 OpenAI mode 验证是否是 LLM 问题
```

#### 问题: 文档入库失败
```
排查路径:
1. 查 CloudWatch Logs: /ecs/insightcard-rag/ingestion
2. 查 DynamoDB: get_doc_status(s3_key) → 看 status 和 error_msg
3. 查 DLQ: aws sqs receive-message --queue-url <DLQ_URL>
4. 常见原因: 文件格式不支持、PDF 解析 OOM、embedding API 限流
```

#### 问题: SSE 断开
```
排查路径:
1. ALB idle timeout 默认 60s → 长推荐可能超时
   修复: aws elbv2 modify-load-balancer-attributes --attributes key=idle_timeout.timeout_seconds,value=300
2. Nginx proxy_read_timeout → 已在 nginx.conf 配 300s
3. CloudWatch Logs 查 backend 是否有 Stream error
```

---

## 7. 优化指南

### 7.1 检索质量优化

| 优化项 | 做法 | 效果 |
|--------|------|------|
| 换 embedding 模型 | 从 MiniLM → BGE-large-zh | 中文检索准确率 +15-20% |
| 加 BM25 混合检索 | ES/OpenSearch BM25 + 向量 RRF 融合 | 关键词精确匹配 +20% |
| 加 rerank 模型 | bge-reranker-v2-m3 Cross-Encoder | Top-3 精度 +10-15% |
| 调分块策略 | 按文档类型路由 (法律→条款, 技术文档→标题) | 召回率 +5-10% |
| Query 改写 | LLM 改写 query / HyDE | 模糊 query 召回 +10% |

### 7.2 性能优化

| 优化项 | 做法 | 预期效果 |
|--------|------|---------|
| Embedding 批量化 | batch_size=64-256 | 吞吐量 ×10 |
| Milvus HNSW 参数 | ef=64 → ef=128 | 查询延迟 -30% |
| DynamoDB 缓存 | DAX (DynamoDB Accelerator) | 读延迟 10ms → 微秒 |
| 前端 CDN | CloudFront 缓存静态资源 | 首屏 -50% |
| LLM 换 Bedrock | Claude Haiku 替代 GPT-4o-mini | 延迟 -40% (内网) |

### 7.3 成本优化

| 项目 | 月成本 (估算) | 优化手段 |
|------|-------------|---------|
| ECS Fargate | ~$45 (3 services) | 非高峰时段缩容到 0 |
| DynamoDB | ~$5 (按需) | TTL 自动清理过期数据 |
| S3 | ~$1 | Lifecycle 规则转 IA/Glacier |
| SQS | ~$0 (免费层) | - |
| ALB | ~$18 | - |
| CloudWatch | ~$5 | 日志保留 30 天 |
| OpenAI API | ~$10-50 (按用量) | 换 Bedrock 或本地部署 |

---

## 8. 准确度评估标准

### 8.1 检索准确度 (Retrieval Quality)

| 指标 | 定义 | 目标 | 测量方法 |
|------|------|------|---------|
| Recall@K | Top-K 结果中包含正确答案的比例 | > 0.85 | 人工标注 100 个 query 的正确 chunk |
| Precision@K | Top-K 结果中相关结果的比例 | > 0.70 | 同上 |
| MRR | 正确答案的排名倒数 | > 0.65 | 同上 |
| NDCG@3 | 归一化折损累积增益 | > 0.70 | 同上 |

### 8.2 生成准确度 (Generation Quality)

| 指标 | 定义 | 目标 | 测量方法 |
|------|------|------|---------|
| 引用准确率 | 引用的 playbook section 确实包含相关内容 | > 90% | 人工核对 50 个推荐 |
| 事实一致性 | 推荐内容与知识库一致 (无幻觉) | > 95% | 人工核对 |
| 可执行性 | 推荐的 action 是否具体可操作 | > 80% | PM 评分 1-5 |
| 完整性 | 是否覆盖了所有关键 remediation 步骤 | > 75% | 对比 playbook 完整内容 |

### 8.3 系统准确度 (System Quality)

| 指标 | 定义 | 目标 |
|------|------|------|
| Bad Case 率 | 用户标记"不相关/不准确"的比例 | < 5% |
| 拒答率 | 检索置信度低 → 主动拒答的比例 | 10-20% |
| 回答延迟 P99 | 从请求到第一个 token 的时间 | < 3s |
| 端到端延迟 P99 | 从请求到完整推荐的时间 | < 10s |

### 8.4 A/B 测试流程

```
1. 准备 100 个标准测试 query (覆盖所有 metric × 各 gap 级别)
2. 用当前版本 (baseline) 跑一遍，记录结果
3. 部署新版本 (candidate)，用相同 query 跑一遍
4. 人工盲评: 对每个结果打分 (1-5)
5. 计算 win/tie/loss 比例
6. win rate > 60% → 发布; < 40% → 回滚; 40-60% → 需更多数据
```

### 8.5 Regression Test

```bash
# 运行回归测试
python -m pytest tests/test_retrieval_quality.py --benchmark
python -m pytest tests/test_generation_accuracy.py --benchmark

# 对比 baseline
python scripts/compare_results.py --baseline results/baseline.json --current results/latest.json
```
