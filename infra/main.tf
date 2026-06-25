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
    # time_sleep：等待 CVM 的 TAT agent 就绪后再下发部署命令。
    time = {
      source  = "hashicorp/time"
      version = ">= 0.9"
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

  # 七层 listener_rule 的 host 头：默认用 CLB 自己的 VIP，销毁阶段或边界
  # 场景（CLB 尚未分配 VIP）回退到占位 "_"。Tencent CLB 的转发规则 domain
  # 字段必填且非空；占位值不影响 destroy（资源本身将被销毁），亦避免 plan
  # 因 [0] 索引越空 list 而硬崩。
  clb_listener_domain = try(module.clb.clb_vips[0], "_")
}

# 动态查询当前账号可用的 tencentos 22.04 公共镜像，避免硬编码 image_id
# 在不同账号/地域失效（占位镜像 img-487zeit5 在本账号无 RunInstances 权限）。
data "tencentcloud_images" "tencentos" {
  image_name_regex = "TencentOS Server 3.2"
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
    {
      action      = "ACCEPT"
      protocol    = "TCP"
      port        = "80"
      cidr_block  = "0.0.0.0/0" # tokenhub 等 HTTP 端点（如 http://43.163.42.168/tokenhub/v1）走 80
      description = "CVM -> model provider API (HTTP, e.g. tokenhub)"
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
# 镜像：腾讯云公共镜像（默认预装 TAT agent）。
# 部署不再走 user-data，而是用 TAT（自动化助手）下发命令，见下方
# tencentcloud_tat_command / tencentcloud_tat_invocation_invoke_attachment：
#   - 可在控制台/CLI 重复执行、查看每次执行的 stdout/stderr，便于调试；
#   - 与 cloud-init 一次性、日志难查相比更可观测。
############################################################
module "cvm" {
  source = "git::https://github.com/terraform-tencentcloud-modules/tencentcloud-landing-zone-booster.git//modules/cvm-instance?ref=v0.1.0"

  instance_name     = "agent-cvm"
  availability_zone = var.availability_zone
  # 留空 cvm_image_id 时自动用 data.tencentcloud_images 查询当前账号可用镜像
  image_id      = var.cvm_image_id != "" ? var.cvm_image_id : data.tencentcloud_images.tencentos.images[0].image_id
  instance_type = var.cvm_instance_type
  system_disk_type   = "CLOUD_PREMIUM"
  system_disk_size   = 50
  allocate_public_ip         = false
  internet_max_bandwidth_out = 0 # 不分配公网 IP，带宽须置 0，否则 API 报 InvalidPermission
  vpc_id                     = module.network.vpc_id
  subnet_id                  = module.subnet.subnet_id[0]
  security_group_ids         = [module.security_group.id]

  tags = local.common_tags
}

############################################################
# TAT 部署：替代 user-data
#
# 1) time_sleep：CVM RunInstances 返回后，TAT agent 仍需数十秒就绪，
#    过早 invoke 会报 agent 不在线。等待 60s 再下发。
# 2) tencentcloud_tat_command：注册部署脚本（content 用 templatefile 渲染，
#    密钥沿用既有注入路径，不经 TAT 自定义参数历史）。
# 3) tencentcloud_tat_invocation_invoke_attachment：把命令下发到 CVM 执行。
#
# ⚠️ 脚本更新后重新在机器上执行：
#    terraform apply -replace=tencentcloud_tat_invocation_invoke_attachment.deploy_app
#    （invoke_attachment 各参数均 ForceNew，replace 即触发重新执行）
############################################################
resource "time_sleep" "wait_tat_agent" {
  depends_on      = [module.cvm]
  create_duration = "60s"
}

resource "tencentcloud_tat_command" "deploy_app" {
  command_name      = "agent-deploy-app"
  description       = "Deploy pydantic-ai agent: write env, install deps, start systemd"
  command_type      = "SHELL"
  working_directory = "/root"
  username          = "root"
  timeout           = 1200 # 20 分钟，覆盖 uv sync 拉依赖耗时

  content = templatefile("${path.module}/../scripts/deploy_app.sh.tftpl", {
    model_provider    = var.model_provider
    model_string      = var.model_string
    model_api_key     = var.model_api_key
    model_base_url    = var.model_base_url
    runtime_api_key   = var.runtime_api_key
    sandbox_template  = var.sandbox_template
    runtime_domain    = var.runtime_domain
    memory_endpoint   = var.memory_endpoint
    memory_api_key    = var.memory_api_key
    memory_service_id = var.memory_service_id
    app_git_repo      = var.app_git_repo
    app_git_ref       = var.app_git_ref
  })
}

resource "tencentcloud_tat_invocation_invoke_attachment" "deploy_app" {
  command_id        = tencentcloud_tat_command.deploy_app.id
  instance_id       = module.cvm.instance_id
  username          = "root"
  working_directory = "/root"
  timeout           = 1200

  depends_on = [time_sleep.wait_tat_agent]
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

  # 七层(HTTP/HTTPS)监听器必须通过转发规则(location)绑定后端：
  # listener_target_instance 仅对四层(TCP/UDP)生效，七层需用 listener_rules。
  listener_rules = [
    {
      domain = local.clb_listener_domain
      url    = "/"

      health_check = {
        enabled = true
        path    = "/healthz"
      }

      target_instance = {
        enabled = true
        targets = [
          {
            instance_id = module.cvm.instance_id
            port        = 8000
            weight      = 10
          },
        ]
      }
    },
  ]
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

  # 七层监听器需用 listener_rules 创建转发规则并绑定后端（详见 HTTP 监听器说明）。
  listener_rules = [
    {
      domain = local.clb_listener_domain
      url    = "/"

      health_check = {
        enabled = true
        path    = "/healthz"
      }

      target_instance = {
        enabled = true
        targets = [
          {
            instance_id = module.cvm.instance_id
            port        = 8000
            weight      = 10
          },
        ]
      }
    },
  ]
}
