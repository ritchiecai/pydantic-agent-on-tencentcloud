############################################################
# 变量声明
#
# 敏感变量（tencentcloud_secret_id/key、model_api_key）一律经 TF_VAR_*
# 环境变量注入，绝不写进 .tf / .tfvars。
############################################################

variable "tencentcloud_secret_id" {
  description = "腾讯云 API SecretId（经 TF_VAR_tencentcloud_secret_id 注入）"
  type        = string
  sensitive   = true
}

variable "tencentcloud_secret_key" {
  description = "腾讯云 API SecretKey（经 TF_VAR_tencentcloud_secret_key 注入）"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "腾讯云地域"
  type        = string
  default     = "ap-guangzhou"
}

variable "availability_zone" {
  description = "可用区（需属于所选 region）"
  type        = string
  default     = "ap-guangzhou-3"
}

variable "cvm_instance_type" {
  description = "CVM 实例规格"
  type        = string
  default     = "SA2.MEDIUM4" # 2C4G，足够 MVP
}

variable "cvm_image_id" {
  description = "CVM 镜像 ID。留空则自动查询当前账号可用的 Ubuntu 22.04 公共镜像。"
  type        = string
  default     = ""
}

variable "model_api_key" {
  description = "模型 provider API key（经 TF_VAR_model_api_key 注入，写入 CVM 的 /etc/agent/env）"
  type        = string
  sensitive   = true
}

variable "model_string" {
  description = "pydantic-ai 模型串（provider:model）。国内可切国内可达 provider。"
  type        = string
  default     = "openai:gpt-4o-mini"
}

variable "model_provider" {
  description = "模型后端 provider：openai（默认）| deepseek | zhipu | tokenhub。决定 MODEL_PROVIDER 环境变量。"
  type        = string
  default     = "openai"
}

variable "model_base_url" {
  description = "OpenAI 兼容端点 base_url（写入 CVM 的 MODEL_BASE_URL）。留空走 provider 代码默认值；tokenhub 等自定义端点在此覆盖。"
  type        = string
  default     = ""
}

############################################################
# 腾讯云 Agent Runtime（代码沙箱，E2B 兼容）
############################################################
variable "runtime_api_key" {
  description = "Agent Runtime API Key（腾讯云控制台「API Keys」创建，形如 ark_xxxx；经 TF_VAR_runtime_api_key 注入，写入 CVM 的 /etc/agent/env -> E2B_API_KEY）"
  type        = string
  sensitive   = true
  default     = ""
}

variable "sandbox_template" {
  description = "沙箱工具名称（腾讯云 Agent Runtime 控制台「沙箱工具」创建；对应 E2B SDK template 参数）"
  type        = string
  default     = ""
}

variable "runtime_domain" {
  description = "Agent Runtime 接入域名。留空使用代码默认 ap-guangzhou.tencentags.com。"
  type        = string
  default     = ""
}

############################################################
# 腾讯云数据库 Agent Memory
############################################################
variable "memory_endpoint" {
  description = "Memory 实例「API 接入」展示的访问地址（控制台 https://console.cloud.tencent.com/tdai/memory ）"
  type        = string
  default     = ""
}

variable "memory_api_key" {
  description = "Memory 实例 API Key（经 TF_VAR_memory_api_key 注入，写入 CVM 的 /etc/agent/env -> AGENT_MEMORY_API_KEY）"
  type        = string
  sensitive   = true
  default     = ""
}

variable "memory_service_id" {
  description = "Memory 实例 ID（控制台「实例列表」可见，形如 mem-xxxxxxxx；写入 CVM 的 /etc/agent/env -> AGENT_MEMORY_SERVICE_ID。SDK 鉴权头 x-tdai-service-id 必需）"
  type        = string
  default     = ""
}

variable "ssl_certificate_id" {
  description = "腾讯云 SSL 证书 ID（用于 CLB HTTPS:443 监听器）。留空则只起 HTTP:80（MVP 允许）。"
  type        = string
  default     = ""
}
