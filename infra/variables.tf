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
  description = "CVM 镜像 ID（腾讯云公共 Ubuntu 22.04）。不同账号/地域可用 image_id 不同，按需替换。"
  type        = string
  default     = "img-487zeit5" # 占位：Ubuntu Server 22.04 LTS（公共镜像），请替换为本账号可用的 image_id
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

variable "ssl_certificate_id" {
  description = "腾讯云 SSL 证书 ID（用于 CLB HTTPS:443 监听器）。留空则只起 HTTP:80（MVP 允许）。"
  type        = string
  default     = ""
}
