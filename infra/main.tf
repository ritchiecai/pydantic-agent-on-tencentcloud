############################################################
# pydantic-ai agent MVP on Tencent Cloud
#
# 复用 tencentcloud-landing-zone-booster 模块（锁定到 git tag v0.1.0）。
# 资源组合：VPC + 子网 + 安全组 + CVM + NAT 网关(+EIP) + CLB(实例+监听器)。
#
# 敏感凭证（secret_id/key、model_api_key）一律经 TF_VAR_* 环境变量注入，
# 绝不写进 .tf / .tfvars。
############################################################

terraform {
  required_version = ">= 1.5"

  required_providers {
    tencentcloud = {
      source = "tencentcloudstack/tencentcloud"
      # landing-zone-booster v0.1.0 模块要求 >= 1.81.174
      version = ">= 1.81.174"
    }
  }
}

provider "tencentcloud" {
  region     = var.region
  secret_id  = var.tencentcloud_secret_id
  secret_key = var.tencentcloud_secret_key
}

# 所有模块均锁定到 landing-zone-booster 的 git tag v0.1.0。
# ⚠️ Terraform 不允许在 source 里用变量，故每个模块 source 都写全字面值。
locals {
  common_tags = {
    app         = "pydantic-agent-on-tencentcloud"
    provisioner = "terraform"
    创建者         = "ritchiecai"
  }
}

# 动态查询当前账号可用的 Ubuntu 22.04 公共镜像，避免硬编码 image_id
# 在不同账号/地域失效（占位镜像 img-487zeit5 在本账号无 RunInstances 权限）。
data "tencentcloud_images" "ubuntu" {
  image_name_regex = "TencentOS Server 4"
  image_type       = ["PUBLIC_IMAGE"]
}

############################################################
# 网络：VPC（专用，不污染默认网络）
############################################################
module "network" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/vpc?ref=v0.1.0"

  vpc_region       = var.region
  vpc_name         = "agent-vpc"
  vpc_cidr         = "10.0.0.0/16"
  vpc_is_multicast = false # 账号未开通多播，关闭以避免 UnauthorizedOperation
  tags             = local.common_tags
}

############################################################
# 子网：CVM 所在子网
############################################################
module "subnet" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/vpc-subnet?ref=v0.1.0"

  vpc_id              = module.network.vpc_id
  subnet_name         = "agent-subnet"
  subnet_cidr         = "10.0.1.0/24"
  availability_zone   = var.availability_zone
  subnet_is_multicast = false # 账号未开通多播，与 VPC 保持一致
  tags                = local.common_tags
}

############################################################
# 安全组：入向仅 CLB→CVM:8000，出向放行 443（调模型 API）
############################################################
module "security_group" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/vpc-security-group?ref=v0.1.0"

  name        = "agent-sg"
  description = "Allow CLB->CVM:8000 ingress and 443 egress for model API"
  tags        = local.common_tags

  ingress_rules = [
    {
      action      = "ACCEPT"
      protocol    = "TCP"
      port        = "8000"
      cidr_block  = "0.0.0.0/0" # 来源限制在 CLB 的安全组/源（MVP 用 0.0.0.0/0，生产应收敛）
      description = "CLB -> CVM app port"
    },
  ]

  egress_rules = [
    {
      action      = "ACCEPT"
      protocol    = "TCP"
      port        = "443"
      cidr_block  = "0.0.0.0/0"
      description = "CVM -> model provider API (HTTPS)"
    },
  ]
}

############################################################
# NAT 网关 + EIP：让无公网 IP 的 CVM 可出向访问模型 API
# 把 VPC 默认路由表的 0.0.0.0/0 指向该 NAT 网关。
############################################################
module "nat_gateway" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/nat-gateway?ref=v0.1.0"

  nat_gateway_name       = "agent-nat"
  vpc_id                 = module.network.vpc_id
  nat_gateway_bandwidth  = 100
  nat_eips               = ["agent-nat-eip"]
  nat_gateway_concurrent = 1000000
  tags                   = local.common_tags

  routable_attachments = {
    default = {
      route_table_id   = module.network.route_table_id
      destination_cidr = "0.0.0.0/0"
    }
  }
}

############################################################
# CVM：单实例，跑 FastAPI + pydantic-ai Agent
# 镜像：腾讯云公共 Ubuntu 22.04；user-data 注入部署脚本与密钥。
############################################################
module "cvm" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/cvm-instance?ref=v0.1.0"

  instance_name     = "agent-cvm"
  availability_zone = var.availability_zone
  # 留空 cvm_image_id 时自动用 data.tencentcloud_images 查询当前账号可用镜像
  image_id      = var.cvm_image_id != "" ? var.cvm_image_id : data.tencentcloud_images.ubuntu.images[0].image_id
  instance_type = var.cvm_instance_type
  system_disk_type   = "CLOUD_PREMIUM"
  system_disk_size   = 50
  allocate_public_ip         = false
  internet_max_bandwidth_out = 0 # 不分配公网 IP，带宽须置 0，否则 API 报 InvalidPermission
  vpc_id                     = module.network.vpc_id
  subnet_id                  = module.subnet.subnet_id[0]
  security_group_ids         = [module.security_group.id]

  # user-data：安装应用 + 写密钥 + 起 systemd（脚本见 scripts/deploy_app.sh）。
  # 密钥经 user_data_raw 注入，落地后立即 chmod 600 并自我删除（见 deploy_app.sh）。
  user_data_raw = templatefile("${path.module}/../scripts/deploy_app.sh.tftpl", {
    model_provider = var.model_provider
    model_string   = var.model_string
    model_api_key  = var.model_api_key
  })

  tags = local.common_tags
}

############################################################
# CLB：公网入口，HTTPS 终止（无证书时退化为 HTTP 80）→ 转发到 CVM:8000
############################################################
module "clb" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/clb-instance?ref=v0.1.0"

  clb_name     = "agent-clb"
  network_type = "OPEN"
  vpc_id       = module.network.vpc_id
  tags         = local.common_tags
}

# 监听器：默认 HTTP:80（MVP）；提供 ssl_certificate_id 时启用 HTTPS:443。
module "clb_listener_http" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/clb-listener?ref=v0.1.0"

  clb_id        = module.clb.clb_id
  listener_name = "agent-http"
  protocol      = "HTTP"
  port          = 80
  scheduler     = "WRR"
  # 模块 validation 在该变量为 null 时报错，显式给个合法值。
  session_expire_time = 30

  listener_target_instance = {
    enabled = true
    targets = [
      {
        instance_id = module.cvm.instance_id
        port        = 8000
        weight      = 10
      },
    ]
  }
}

module "clb_listener_https" {
  count  = var.ssl_certificate_id == "" ? 0 : 1
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/clb-listener?ref=v0.1.0"

  clb_id              = module.clb.clb_id
  listener_name       = "agent-https"
  protocol            = "HTTPS"
  port                = 443
  scheduler           = "WRR"
  session_expire_time = 30

  certificate = {
    ssl_mode = "UNIDIRECTIONAL"
    cert_id  = var.ssl_certificate_id
  }

  listener_target_instance = {
    enabled = true
    targets = [
      {
        instance_id = module.cvm.instance_id
        port        = 8000
        weight      = 10
      },
    ]
  }
}
